"""Find a winning company's website so users can research competitors.

The TZ asks: from a Contract Award, take the winning company's name and find
its web site, so users can analyse competitors or contact them.

Implementation notes:

* Runs through :mod:`.providers`, so the search happens on whichever provider
  is configured — Claude's server-side tool or Gemini's Google Search
  grounding. Either way this backend needs no search-engine account of its own.
* The model is asked for a bare URL (or ``NONE``) rather than JSON: web search
  attaches citations to its text, and citations cannot be combined with the
  structured-output format.
* The returned URL is validated locally — scheme, and a reject-list of search
  engines, directories and social networks — so a plausible-looking but
  useless answer is not stored as fact.
* Every result records where it came from (``ai_web_search``), so a human can
  tell an inferred URL from a verified one.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from urllib.parse import urlparse

from django.conf import settings
from django.db.models import F
from django.utils import timezone

from ...models import ContractAward
from .client import AIUnavailable
from .providers import search_answer, search_enabled

logger = logging.getLogger(__name__)

SOURCE_AI_SEARCH = "ai_web_search"

# Hosts that are never a company's own site.
_REJECTED_HOST_FRAGMENTS = (
    "google.", "bing.", "yahoo.", "duckduckgo.", "baidu.",
    "wikipedia.org", "linkedin.com", "facebook.com", "twitter.com", "x.com",
    "instagram.com", "youtube.com", "crunchbase.com", "bloomberg.com",
    "opencorporates.com", "dnb.com", "zoominfo.com", "glassdoor.",
    "worldbank.org", "devex.com", "tenders.", "alibaba.com", "indiamart.com",
)

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)

#: A field label the award parser mistook for a supplier name.
_LABEL_RE = re.compile(
    r"(?:name|supplier|address|country|company|bidder)", re.IGNORECASE
)

SYSTEM_PROMPT = """\
You find the official website of a company that won a public procurement \
contract.

Method:
- Search the web for the company using its exact name and, when given, its \
country.
- Return only the company's own primary domain (its homepage), not a \
directory, registry, news article, social network, marketplace, or search \
result page.
- If several companies share the name, prefer the one whose country and line \
of business match the contract.
- If you cannot identify the company's own website with reasonable \
confidence, answer exactly NONE.

Answer format: the bare URL on a single line, or NONE. No explanation, no \
markdown, no punctuation around it.\
"""


@dataclass(slots=True)
class WebsiteResult:
    url: str = ""
    source: str = ""
    checked: bool = True

    @property
    def found(self) -> bool:
        return bool(self.url)


def find_company_website(
    company_name: str, *, country: str = "", context: str = ""
) -> WebsiteResult:
    """Search for ``company_name``'s official website.

    Returns an empty result rather than raising when AI is unavailable, the
    model declines, or the answer fails validation.
    """
    # The award parser occasionally carries a field label through as the
    # supplier ("Name:"), and a trailing colon is common on real names too.
    # Rare — 2 rows in 5,325 — but each one spends a live search to learn
    # nothing, so they are filtered here rather than at the call site.
    name = (company_name or "").strip().rstrip(":").strip()
    if len(name) < 3 or _LABEL_RE.fullmatch(name):
        return WebsiteResult(checked=False)

    if not search_enabled():
        return WebsiteResult(checked=False)

    prompt_lines = [f"Company: {name}"]
    if country:
        prompt_lines.append(f"Country: {country}")
    if context:
        prompt_lines.append(f"Contract: {context[:300]}")
    prompt_lines.append("")
    prompt_lines.append("What is this company's official website?")

    try:
        answer = search_answer(
            system=SYSTEM_PROMPT,
            prompt="\n".join(prompt_lines),
            max_searches=settings.ANTHROPIC["ENRICH_MAX_SEARCHES"],
        )
    except AIUnavailable as exc:
        logger.info("Website lookup unavailable (%s).", exc)
        return WebsiteResult(checked=False)
    except Exception as exc:  # noqa: BLE001 - enrichment is best-effort
        logger.warning("Website lookup failed for %r: %s", name, exc)
        return WebsiteResult(checked=False)

    if not answer:
        logger.info("Website lookup declined for %r.", name)
        return WebsiteResult()

    url = _extract_url(answer)
    if not url or not is_plausible_company_url(url):
        return WebsiteResult()

    # Shape alone does not make a domain real. Observed live: a lookup for an
    # Afghan trading company returned `hwgrp.com`, which passes every rule
    # above, resolves in DNS, and then refuses every connection. Storing that
    # as "the winner's website" is worse than storing nothing.
    if not _responds(url):
        logger.info("Discarded unreachable website %r for %r.", url, name)
        return WebsiteResult()

    return WebsiteResult(url=url, source=SOURCE_AI_SEARCH)


def _responds(url: str, timeout: float = 6.0) -> bool:
    """Whether anything answers at ``url``.

    Deliberately lenient about *what* answers: a 403 or a 500 still means a
    real host is there, and plenty of company sites block HEAD or greet a bare
    client rudely. Only an outright failure to connect — refused, DNS-less, or
    a dead TLS endpoint — counts as "this domain is not a website". A timeout
    is treated as reachable, since a slow host is not an imaginary one.
    """
    import requests

    for method in ("head", "get"):
        try:
            requests.request(
                method, url, timeout=timeout, allow_redirects=True,
                headers={"User-Agent": "Pintell/1.0 (+link check)"},
            )
            return True
        except requests.Timeout:
            return True
        except requests.RequestException:
            continue
    return False


def _extract_url(answer: str) -> str:
    text = (answer or "").strip()
    if not text or text.upper().startswith("NONE"):
        return ""

    match = _URL_RE.search(text)
    if match:
        return match.group(0).rstrip(".,);")

    # The model may answer with a bare domain; accept it if it looks like one.
    candidate = text.split()[0].strip().strip(".,);")
    if re.fullmatch(r"[a-z0-9.-]+\.[a-z]{2,}", candidate, re.IGNORECASE):
        return f"https://{candidate}"
    return ""


def is_plausible_company_url(url: str) -> bool:
    """Reject search engines, directories, social networks and junk schemes."""
    try:
        parsed = urlparse(url)
    except ValueError:
        return False

    if parsed.scheme not in {"http", "https"} or not parsed.netloc:
        return False

    host = parsed.netloc.lower()
    if any(fragment in host for fragment in _REJECTED_HOST_FRAGMENTS):
        return False
    # A host with no dot cannot be a public domain.
    return "." in host


def enrich_award(award: ContractAward, *, force: bool = False) -> WebsiteResult:
    """Find and store the winner's website for one award."""
    if award.supplier_website and not force:
        return WebsiteResult(url=award.supplier_website, source=award.supplier_website_source)

    result = find_company_website(
        award.supplier_name,
        country=award.supplier_country,
        context=award.notice.bid_description if award.notice_id else "",
    )

    if not result.checked:
        return result

    award.supplier_website = result.url
    award.supplier_website_source = result.source
    award.supplier_website_checked_at = timezone.now()
    award.save(
        update_fields=[
            "supplier_website", "supplier_website_source", "supplier_website_checked_at",
        ]
    )
    return result


def enrich_pending_awards(*, limit: int = 25, force: bool = False) -> dict[str, int]:
    """Look up websites for awards that have not been checked yet.

    Deliberately small by default: each lookup runs a live web search, so this
    is metered work rather than a bulk backfill.
    """
    if not search_enabled():
        logger.info("Website enrichment skipped — no web-search provider.")
        return {"checked": 0, "found": 0, "skipped": 0}

    queryset = ContractAward.objects.select_related("notice").exclude(supplier_name="")
    if not force:
        queryset = queryset.filter(supplier_website_checked_at__isnull=True)

    counters = {"checked": 0, "found": 0, "skipped": 0}
    # Newest first, with the undated awards genuinely last. Postgres sorts
    # NULLs first on a DESC, so the plain `-award_date` this used to carry
    # spent a metered, ten-per-quarter-hour budget on the awards whose date
    # upstream never published — the least identifiable records in the
    # archive — before reaching any recent winner.
    for award in queryset.order_by(F("award_date").desc(nulls_last=True))[:limit]:
        result = enrich_award(award, force=force)
        if not result.checked:
            counters["skipped"] += 1
            continue
        counters["checked"] += 1
        if result.found:
            counters["found"] += 1

    logger.info(
        "Website enrichment: %s checked, %s found, %s skipped",
        counters["checked"], counters["found"], counters["skipped"],
    )
    return counters
