"""HTTP client for the World Bank Procurement Notices search API.

Endpoint: https://search.worldbank.org/api/v2/procnotices

The client is deliberately dumb: it fetches pages and hands back parsed
dictionaries. Mapping to the database lives in :mod:`..sync`.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from typing import Any, Iterator

import requests
from django.conf import settings
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)


class WorldBankAPIError(RuntimeError):
    """Raised when a page cannot be fetched or parsed."""


@dataclass(slots=True)
class NoticePage:
    """One page of upstream results."""

    notices: list[dict[str, Any]]
    offset: int
    rows: int
    total: int | None = None

    @property
    def is_empty(self) -> bool:
        return not self.notices


@dataclass(slots=True)
class WorldBankClient:
    """Thin, retrying wrapper around the procurement-notices endpoint."""

    base_url: str = field(default_factory=lambda: settings.WORLDBANK["API_URL"])
    timeout: int = field(default_factory=lambda: settings.WORLDBANK["HTTP_TIMEOUT"])
    user_agent: str = field(default_factory=lambda: settings.WORLDBANK["USER_AGENT"])
    session: requests.Session | None = None

    def __post_init__(self) -> None:
        if self.session is None:
            self.session = self._build_session()

    def _build_session(self) -> requests.Session:
        session = requests.Session()
        # Retry only on transient conditions; never on 4xx (except 429).
        retry = Retry(
            total=3,
            connect=3,
            read=3,
            backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}),
            raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=10)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({"User-Agent": self.user_agent, "Accept": "application/json"})
        return session

    # -- low level ---------------------------------------------------------
    def fetch_page(self, *, offset: int = 0, rows: int = 100, **filters: Any) -> NoticePage:
        """Fetch a single page. Raises :class:`WorldBankAPIError` on failure."""
        params: dict[str, Any] = {"format": "json", "rows": rows, "os": offset}
        params.update({k: v for k, v in filters.items() if v not in (None, "")})

        try:
            response = self.session.get(self.base_url, params=params, timeout=self.timeout)  # type: ignore[union-attr]
        except requests.RequestException as exc:
            raise WorldBankAPIError(f"request failed (offset={offset}): {exc}") from exc

        if response.status_code != 200:
            raise WorldBankAPIError(
                f"unexpected status {response.status_code} (offset={offset})"
            )

        try:
            payload = response.json()
        except ValueError:
            # Notice bodies occasionally carry stray control characters that
            # strict JSON rejects; a tolerant re-parse recovers the page
            # instead of throwing the whole slice away.
            try:
                payload = json.loads(response.text, strict=False)
            except ValueError as exc:
                raise WorldBankAPIError(
                    f"invalid JSON body (offset={offset}): {exc}"
                ) from exc

        if not isinstance(payload, dict):
            raise WorldBankAPIError(f"unexpected payload type {type(payload).__name__}")

        # Out-of-range offsets and bad parameters come back as 200 + {"error": …}.
        if "error" in payload and "procnotices" not in payload:
            raise WorldBankAPIError(
                f"upstream error (offset={offset}): {str(payload['error'])[:200]}"
            )

        notices = payload.get("procnotices") or []
        if not isinstance(notices, list):
            raise WorldBankAPIError("`procnotices` is not a list")

        return NoticePage(
            notices=[n for n in notices if isinstance(n, dict)],
            offset=offset,
            rows=rows,
            total=_safe_int(payload.get("total")),
        )

    # -- high level --------------------------------------------------------
    def iter_pages(
        self,
        *,
        max_pages: int,
        rows: int,
        start_offset: int = 0,
        **filters: Any,
    ) -> Iterator[NoticePage | WorldBankAPIError]:
        """Yield up to ``max_pages`` pages.

        A page that cannot be fetched is yielded as the exception instead of
        raising, so one bad page never aborts an entire sync run. Iteration
        stops early on an empty page or once the upstream total is reached.
        """
        offset = start_offset
        for page_number in range(max_pages):
            try:
                page = self.fetch_page(offset=offset, rows=rows, **filters)
            except WorldBankAPIError as exc:
                logger.warning("Page %s failed (offset=%s): %s", page_number + 1, offset, exc)
                yield exc
                offset += rows
                continue

            yield page

            if page.is_empty:
                logger.info("Empty page at offset %s — stopping pagination.", offset)
                return

            offset += rows
            if page.total is not None and offset >= page.total:
                logger.info("Reached upstream total (%s) — stopping pagination.", page.total)
                return


def _safe_int(value: Any) -> int | None:
    try:
        return int(str(value).strip())
    except (TypeError, ValueError):
        return None
