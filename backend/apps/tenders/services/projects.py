"""Project enrichment: documents, financing and the ESRS summary.

Two upstream endpoints, both keyed by the notice's ``project_id``:

* ``/api/v2/projects``  → project name, status, financing, implementing agency
* ``/api/v3/wds``       → every published document (title + PDF url), and the
  Appraisal Environmental and Social Review Summary (ESRS) among them

Both are fetched once per project and mirrored locally, so opening a notice
never triggers an upstream round-trip. What a request *can* do is ask for a
project it needs — see :func:`request_project_sync` — which queues the work and
returns immediately rather than making the page wait on two upstream calls.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta
from decimal import Decimal, InvalidOperation
from typing import Any, Iterable

import requests
from django.conf import settings
from django.core.cache import cache
from django.db.models import F
from django.utils import timezone
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

from ..models import ProjectDocument, ProjectProfile
from .mapping import clean_str, parse_date

logger = logging.getLogger(__name__)

# Upstream labels the ESRS document type exactly like this.
ESRS_DOC_TYPE = "Environmental and Social Review Summary"


class ProjectAPIError(RuntimeError):
    """Raised when a project or document page cannot be fetched."""


@dataclass
class ProjectSyncStats:
    projects_seen: int = 0
    projects_updated: int = 0
    documents_created: int = 0
    documents_updated: int = 0
    esrs_found: int = 0
    # Notices whose `project_ref` this run connected — they arrived before
    # their project did, which is the usual order.
    notices_linked: int = 0
    failed: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "projects_seen": self.projects_seen,
            "projects_updated": self.projects_updated,
            "documents_created": self.documents_created,
            "documents_updated": self.documents_updated,
            "esrs_found": self.esrs_found,
            "notices_linked": self.notices_linked,
            "failed": self.failed,
            "errors": self.errors[:20],
        }


@dataclass
class PendingProjects:
    """What the next batch is made of, and why each entry is in it."""

    new: list[str] = field(default_factory=list)
    retry: list[str] = field(default_factory=list)
    stale: list[str] = field(default_factory=list)

    @property
    def ids(self) -> list[str]:
        """New projects first: a notice nobody can research yet outranks a
        refresh of one that is merely a fortnight old."""
        return self.new + self.retry + self.stale

    def as_dict(self) -> dict[str, int]:
        return {"new": len(self.new), "retry": len(self.retry), "stale": len(self.stale)}


class WorldBankProjectClient:
    """Client for the projects and documents (WDS) endpoints."""

    def __init__(self, session: requests.Session | None = None) -> None:
        config = settings.WORLDBANK
        self.projects_url: str = config["PROJECTS_API_URL"]
        self.documents_url: str = config["DOCUMENTS_API_URL"]
        self.timeout: int = config["HTTP_TIMEOUT"]
        self.session = session or self._build_session(config["USER_AGENT"])

    @staticmethod
    def _build_session(user_agent: str) -> requests.Session:
        session = requests.Session()
        retry = Retry(
            total=3, connect=3, read=3, backoff_factor=1.5,
            status_forcelist=(429, 500, 502, 503, 504),
            allowed_methods=frozenset({"GET"}), raise_on_status=False,
        )
        adapter = HTTPAdapter(max_retries=retry, pool_maxsize=10)
        session.mount("https://", adapter)
        session.mount("http://", adapter)
        session.headers.update({"User-Agent": user_agent, "Accept": "application/json"})
        return session

    # -- raw fetches -------------------------------------------------------
    def _get_json(self, url: str, params: dict[str, Any]) -> dict[str, Any]:
        try:
            response = self.session.get(url, params=params, timeout=self.timeout)
        except requests.RequestException as exc:
            raise ProjectAPIError(f"request failed: {exc}") from exc

        if response.status_code != 200:
            raise ProjectAPIError(f"unexpected status {response.status_code}")

        try:
            payload = response.json()
        except ValueError as exc:
            raise ProjectAPIError(f"invalid JSON: {exc}") from exc

        if not isinstance(payload, dict):
            raise ProjectAPIError(f"unexpected payload {type(payload).__name__}")
        return payload

    def fetch_project(self, project_id: str) -> dict[str, Any] | None:
        """Project metadata, or ``None`` when upstream knows no such project."""
        payload = self._get_json(self.projects_url, {"format": "json", "id": project_id})
        projects = payload.get("projects") or {}
        if not isinstance(projects, dict):
            return None
        # Keyed by project id; take the entry regardless of key casing.
        for value in projects.values():
            if isinstance(value, dict):
                return value
        return None

    def fetch_documents(self, project_id: str, rows: int = 100) -> list[dict[str, Any]]:
        """Every published document for a project."""
        payload = self._get_json(
            self.documents_url,
            {
                "format": "json",
                "projectid": project_id,
                "rows": rows,
                "fl": "docna,docdt,pdfurl,txturl,display_title,docty,projectid,lang,guid,url",
            },
        )
        documents = payload.get("documents") or {}
        if not isinstance(documents, dict):
            return []
        return [
            value
            for key, value in documents.items()
            if key != "facets" and isinstance(value, dict)
        ]


# ---------------------------------------------------------------------------
# Mapping helpers
# ---------------------------------------------------------------------------
def _decimal(value: Any) -> Decimal | None:
    """Parse upstream money strings such as ``"20,000,000"``."""
    text = clean_str(value).replace(",", "").replace("$", "")
    if not text:
        return None
    try:
        return Decimal(text)
    except (InvalidOperation, ValueError):
        return None


def _date(value: Any) -> date | None:
    return parse_date(value)


def _names(value: Any) -> list[str]:
    """Flatten upstream's ``[{"Name": "..."}]`` shape into a list of strings."""
    if not isinstance(value, list):
        return []
    names = []
    for item in value:
        if isinstance(item, dict):
            name = clean_str(item.get("Name") or item.get("name"))
            if name:
                names.append(name)
        elif item:
            names.append(clean_str(item))
    return names


def map_project(payload: dict[str, Any]) -> dict[str, Any]:
    return {
        "name": clean_str(payload.get("project_name")),
        "country": clean_str(payload.get("countryshortname"), 255),
        "status": clean_str(payload.get("status"), 64),
        "lending_instrument": clean_str(payload.get("lendinginstr"), 255),
        "implementing_agency": clean_str(payload.get("impagency")),
        "team_lead": clean_str(payload.get("teamleadname")),
        "sectors": _names(payload.get("sector")),
        "themes": _names(payload.get("theme") or payload.get("theme_list")),
        "total_amount_display": clean_str(payload.get("totalamt"), 64),
        "total_amount_usd": _decimal(payload.get("totalamt")),
        "commitment_amount_usd": _decimal(payload.get("totalcommamt")),
        "board_approval_date": _date(payload.get("boardapprovaldate")),
        "closing_date": _date(payload.get("closingdate")),
        "project_url": clean_str(payload.get("url"), 500),
        "fetched_at": timezone.now(),
    }


def map_document(payload: dict[str, Any], project_id: str) -> dict[str, Any] | None:
    guid = clean_str(payload.get("guid") or payload.get("id"), 64)
    if not guid:
        return None
    return {
        "guid": guid,
        "project_id": project_id,
        "title": clean_str(payload.get("display_title") or payload.get("docna")),
        "doc_type": clean_str(payload.get("docty"), 255),
        "doc_date": _date(payload.get("docdt")),
        "language": clean_str(payload.get("lang"), 64),
        "pdf_url": clean_str(payload.get("pdfurl"), 1000),
        "text_url": clean_str(payload.get("txturl"), 1000),
        "page_url": clean_str(payload.get("url"), 1000),
        "fetched_at": timezone.now(),
    }


def pick_esrs(documents: list[dict[str, Any]]) -> dict[str, Any] | None:
    """The Appraisal ESRS among a project's documents, newest first.

    Matches on the document type first (upstream's own label) and falls back
    to a title match, because a few projects file it under a generic type.
    """
    candidates = [
        doc for doc in documents
        if ESRS_DOC_TYPE.lower() in (doc.get("doc_type") or "").lower()
        or "environmental and social review summary" in (doc.get("title") or "").lower()
    ]
    if not candidates:
        return None
    appraisal = [d for d in candidates if "appraisal" in (d.get("title") or "").lower()]
    pool = appraisal or candidates
    return sorted(pool, key=lambda d: (d.get("doc_date") is None, d.get("doc_date")), reverse=True)[0]


# ---------------------------------------------------------------------------
# Orchestration
# ---------------------------------------------------------------------------
def _retry_delay(error_count: int) -> timedelta:
    """Backoff after ``error_count`` consecutive failures.

    Doubles each time from ``RETRY_BASE_MINUTES`` and stops at
    ``RETRY_MAX_DAYS``, so a project upstream has never heard of costs two
    requests a week instead of two every quarter of an hour. The exponent is
    clamped before the shift — an unbounded ``2 ** n`` on a long-broken row
    would overflow into a date the database cannot store.
    """
    config = settings.PROJECTS
    exponent = min(max(error_count - 1, 0), 16)
    minutes = config["RETRY_BASE_MINUTES"] * (2**exponent)
    return min(timedelta(minutes=minutes), timedelta(days=config["RETRY_MAX_DAYS"]))


def _mark_failure(profile: ProjectProfile, message: object, *, now: datetime | None = None) -> None:
    now = now or timezone.now()
    profile.error_count += 1
    profile.next_retry_at = now + _retry_delay(profile.error_count)
    profile.last_error = str(message)[:2000]


def _mark_success(profile: ProjectProfile) -> None:
    profile.error_count = 0
    profile.next_retry_at = None
    profile.last_error = ""


def sync_project(
    project_id: str,
    *,
    client: WorldBankProjectClient | None = None,
    with_documents: bool = True,
    stats: ProjectSyncStats | None = None,
) -> ProjectProfile | None:
    """Fetch and mirror one project, its documents, and its ESRS.

    Every exit path records the attempt: a clean run clears the error state, a
    failed one arms the backoff. Nothing here raises — the caller is a batch
    that must survive one bad project.
    """
    project_id = clean_str(project_id, 32)
    if not project_id:
        return None

    client = client or WorldBankProjectClient()
    stats = stats if stats is not None else ProjectSyncStats()
    stats.projects_seen += 1

    profile, _ = ProjectProfile.objects.get_or_create(project_id=project_id)
    profile.last_attempt_at = timezone.now()

    try:
        payload = client.fetch_project(project_id)
    except ProjectAPIError as exc:
        stats.failed += 1
        stats.errors.append(f"{project_id}: {exc}")
        _mark_failure(profile, exc)
        profile.save(
            update_fields=[
                "last_attempt_at", "last_error", "error_count",
                "next_retry_at", "updated_at",
            ]
        )
        # The row exists even though the fetch did not, and it is the row a
        # reader would be shown, so the notices point at it either way.
        stats.notices_linked += link_notices(profile.project_id)
        logger.warning("Project %s metadata failed: %s", project_id, exc)
        return profile

    failure: str | None = None
    if payload:
        for name, value in map_project(payload).items():
            setattr(profile, name, value)
        stats.projects_updated += 1
    else:
        # Not an outage: upstream simply has no such project. Treated as a
        # failure anyway so the backoff stops it being asked for every cycle.
        failure = "no project returned upstream"

    if with_documents:
        failure = _sync_documents(profile, client, stats) or failure

    if failure:
        _mark_failure(profile, failure)
    else:
        _mark_success(profile)

    profile.save()
    # The profile row now exists, so any notice still holding only the raw key
    # can be joined to it.
    stats.notices_linked += link_notices(profile.project_id)
    return profile


def _sync_documents(
    profile: ProjectProfile,
    client: WorldBankProjectClient,
    stats: ProjectSyncStats,
) -> str | None:
    """Mirror a project's documents. Returns an error message, or ``None``."""
    try:
        raw_documents = client.fetch_documents(profile.project_id)
    except ProjectAPIError as exc:
        stats.failed += 1
        stats.errors.append(f"{profile.project_id} documents: {exc}")
        logger.warning("Project %s documents failed: %s", profile.project_id, exc)
        return f"documents: {exc}"

    mapped = [m for m in (map_document(d, profile.project_id) for d in raw_documents) if m]

    existing = set(
        ProjectDocument.objects.filter(project=profile).values_list("guid", flat=True)
    )
    to_create = [ProjectDocument(**values) for values in mapped if values["guid"] not in existing]
    to_update = [ProjectDocument(**values) for values in mapped if values["guid"] in existing]

    if to_create:
        ProjectDocument.objects.bulk_create(to_create, batch_size=200, ignore_conflicts=True)
        stats.documents_created += len(to_create)
    if to_update:
        ProjectDocument.objects.bulk_update(
            to_update,
            fields=["title", "doc_type", "doc_date", "language", "pdf_url",
                    "text_url", "page_url", "fetched_at"],
            batch_size=200,
        )
        stats.documents_updated += len(to_update)

    profile.documents_count = len(mapped)
    profile.documents_fetched_at = timezone.now()

    esrs = pick_esrs(mapped)
    if esrs:
        profile.esrs_title = esrs.get("title") or ""
        profile.esrs_pdf_url = esrs.get("pdf_url") or ""
        profile.esrs_page_url = esrs.get("page_url") or ""
        profile.esrs_date = esrs.get("doc_date")
        profile.esrs_report_no = _extract_report_no(esrs.get("title") or "")
        stats.esrs_found += 1

    return None


def _extract_report_no(title: str) -> str:
    """Pull a report number such as ``ESRSA02670`` out of a document title."""
    import re

    match = re.search(r"\b(ESRS[A-Z]?\d{3,})\b", title or "", re.IGNORECASE)
    return match.group(1).upper() if match else ""


def link_notices(project_id: str) -> int:
    """Point this project's notices at it, and return how many were linked.

    The other half of the relation. ``services.sync`` sets ``project_ref``
    whenever the profile already exists, which covers the common case; this
    covers the other order — a project mirrored after its notices, which is
    what actually happens most of the time, since a notice is what makes anyone
    ask for the project in the first place.
    """
    from ..models import TenderNotice

    if not project_id:
        return 0
    return (
        TenderNotice.objects.filter(project_id=project_id)
        .exclude(project_ref_id=project_id)
        .update(project_ref_id=project_id)
    )


def sync_lock_key(project_id: str) -> str:
    return f"project-sync:{project_id}"


def request_project_sync(project_id: str) -> bool:
    """Queue a mirror of one project, at most once per lock window.

    Called from the read path: a notice outside the focus feed has a project
    nobody scheduled, and the periodic cycle would never reach it. Rather than
    fetch inline — two upstream calls the visitor would wait on — the work is
    handed to Celery and the page renders as pending.

    The lock is what makes this safe to call on every request: it is taken
    *before* the task is queued, so a hundred readers of the same notice
    produce one job. It is deliberately left to expire instead of being
    released on completion, which also rate-limits a project that keeps
    failing to one attempt per window.

    Returns True when this call is the one that queued the work.
    """
    project_id = clean_str(project_id, 32)
    if not project_id:
        return False

    key = sync_lock_key(project_id)
    if not cache.add(key, True, settings.PROJECTS["ONDEMAND_LOCK_SECONDS"]):
        return False

    from ..tasks import sync_project_profile  # local: tasks imports this module

    try:
        sync_project_profile.delay(project_id)
    except Exception as exc:  # noqa: BLE001 - a dead broker must not 500 a read
        # Drop the lock so the next reader can try again once the broker is up.
        cache.delete(key)
        logger.warning("Could not queue project %s: %s", project_id, exc)
        return False

    logger.info("Queued on-demand sync for project %s", project_id)
    return True


def select_pending_projects(
    candidate_ids: Iterable[str],
    *,
    limit: int | None = None,
    now: datetime | None = None,
) -> PendingProjects:
    """Choose the next batch of projects to mirror, in priority order.

    Three tiers, filled in this order until ``limit`` is reached:

    1. **new** — a ``project_id`` seen on a notice that has no profile at all.
       A user opening that notice today sees nothing, so these come first.
    2. **retry** — a profile whose last attempt failed and whose backoff has
       elapsed. Without this tier a single upstream hiccup froze a project out
       permanently, because a failed attempt still left a row behind.
    3. **stale** — a healthy profile older than ``PROJECT_REFRESH_DAYS``,
       oldest first. Projects gain documents for years after their first
       notice; the ESRS in particular often lands later.

    ``candidate_ids`` only feeds tier 1. Tiers 2 and 3 are read from the
    mirrored profiles themselves, so a project stays maintained even after its
    notices drop out of the focus feed.
    """
    now = now or timezone.now()
    limit = limit if limit is not None else settings.PROJECTS["BATCH_SIZE"]
    pending = PendingProjects()
    if limit <= 0:
        return pending

    candidates: list[str] = []
    seen: set[str] = set()
    for raw in candidate_ids:
        project_id = clean_str(raw, 32)
        if project_id and project_id not in seen:
            seen.add(project_id)
            candidates.append(project_id)

    if candidates:
        known = set(
            ProjectProfile.objects.filter(project_id__in=candidates).values_list(
                "project_id", flat=True
            )
        )
        pending.new = [pid for pid in candidates if pid not in known][:limit]

    remaining = limit - len(pending.new)
    if remaining > 0:
        pending.retry = list(
            ProjectProfile.objects.retry_due(now)
            .order_by("next_retry_at")
            .values_list("project_id", flat=True)[:remaining]
        )

    remaining = limit - len(pending.new) - len(pending.retry)
    if remaining > 0:
        pending.stale = list(
            ProjectProfile.objects.stale(now)
            .order_by(F("fetched_at").asc(nulls_first=True))
            .values_list("project_id", flat=True)[:remaining]
        )

    logger.info("Project queue: %s", pending.as_dict())
    return pending


def sync_pending_projects(
    project_ids: list[str],
    *,
    client: WorldBankProjectClient | None = None,
    limit: int | None = None,
) -> ProjectSyncStats:
    """Mirror a batch of projects, never letting one failure stop the rest."""
    client = client or WorldBankProjectClient()
    stats = ProjectSyncStats()

    for project_id in project_ids[:limit] if limit else project_ids:
        try:
            sync_project(project_id, client=client, stats=stats)
        except Exception as exc:  # noqa: BLE001 - one bad project is not fatal
            stats.failed += 1
            stats.errors.append(f"{project_id}: {exc}")
            logger.exception("Unexpected failure syncing project %s", project_id)

    logger.info(
        "Project sync: %s seen, %s updated, %s docs created, %s ESRS, %s failed",
        stats.projects_seen, stats.projects_updated,
        stats.documents_created, stats.esrs_found, stats.failed,
    )
    return stats
