"""Sync orchestration: pull pages from the World Bank API into the database.

Design notes
------------
* **Idempotent upsert.** Records are matched on the upstream ``id``. A record
  is only written when its content hash changed, so a re-run over unchanged
  data performs zero writes.
* **Fault tolerant.** A failed page is counted and skipped; the run continues
  and finishes with status ``partial``. Only a total failure (no page fetched
  at all) is reported as ``failed``.
* **Batched.** Reads and writes happen per page with ``bulk_create`` /
  ``bulk_update``, which keeps the run to a handful of queries per page.
"""

from __future__ import annotations

import logging
from dataclasses import asdict, dataclass, field
from typing import Any, Callable

from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import ProjectProfile, SyncRun, TenderNotice
from ..regions import canonical_country, group_countries
from .mapping import map_notice
from .worldbank import WorldBankAPIError, WorldBankClient

logger = logging.getLogger(__name__)

# Fields refreshed on an existing row (everything except the PK and created_at).
UPDATABLE_FIELDS: tuple[str, ...] = (
    "notice_type", "notice_status", "notice_language",
    "notice_date", "submission_date", "deadline_date", "deadline_time", "deadline_at",
    "country", "project_id", "project_name", "project_ref",
    "bid_reference_no", "bid_description",
    "procurement_group", "procurement_method_code", "procurement_method_name",
    "contact_name", "contact_organization", "contact_email", "contact_phone_no",
    "contact_address", "contact_country", "contact_web_url",
    "notice_text_sanitized", "notice_text_raw",
    "content_hash", "last_synced_at", "updated_at",
)


@dataclass
class SyncStats:
    pages_requested: int = 0
    pages_fetched: int = 0
    pages_failed: int = 0
    notices_seen: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    skipped: int = 0
    # Dropped for being outside the focus country group. Counted apart from
    # `skipped` (which means "upstream sent something unusable") because this
    # one is a deliberate scope decision, and a run where it is unexpectedly
    # zero — or unexpectedly everything — is the symptom of a misconfigured
    # group rather than of bad upstream data.
    out_of_scope: int = 0
    upstream_total: int | None = None
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return asdict(self)


def sync_notices(
    *,
    max_pages: int | None = None,
    rows_per_page: int | None = None,
    start_offset: int = 0,
    trigger: str = "manual",
    filters: dict[str, Any] | None = None,
    client: WorldBankClient | None = None,
    record_run: bool = True,
) -> SyncStats:
    """Fetch notices from the World Bank API and upsert them locally."""
    config = settings.WORLDBANK
    max_pages = max_pages or config["MAX_PAGES"]
    rows_per_page = rows_per_page or config["ROWS_PER_PAGE"]
    filters = filters or {}
    client = client or WorldBankClient()

    stats = SyncStats(pages_requested=max_pages)
    run = (
        SyncRun.objects.create(
            trigger=trigger, pages_requested=max_pages, status=SyncRun.Status.RUNNING
        )
        if record_run
        else None
    )

    logger.info(
        "Sync started (trigger=%s, pages=%s, rows=%s, filters=%s)",
        trigger, max_pages, rows_per_page, filters or "-",
    )

    try:
        for result in client.iter_pages(
            max_pages=max_pages,
            rows=rows_per_page,
            start_offset=start_offset,
            **filters,
        ):
            if isinstance(result, WorldBankAPIError):
                stats.pages_failed += 1
                stats.errors.append(str(result))
                continue

            stats.pages_fetched += 1
            if result.total is not None:
                stats.upstream_total = result.total
            stats.notices_seen += len(result.notices)
            upsert_payloads(result.notices, stats)

    except Exception as exc:  # noqa: BLE001 - a sync must never crash the worker
        logger.exception("Sync aborted unexpectedly")
        stats.errors.append(f"fatal: {exc}")
        _finish_run(run, stats, SyncRun.Status.FAILED)
        return stats

    status = _resolve_status(stats)
    _finish_run(run, stats, status)
    logger.info(
        "Sync finished [%s]: %s created, %s updated, %s unchanged, "
        "%s skipped, %s out of scope, %s/%s pages ok",
        status, stats.created, stats.updated, stats.unchanged,
        stats.skipped, stats.out_of_scope, stats.pages_fetched, stats.pages_requested,
    )
    return stats


def _resolve_status(stats: SyncStats) -> str:
    if stats.pages_fetched == 0:
        return SyncRun.Status.FAILED
    if stats.pages_failed:
        return SyncRun.Status.PARTIAL
    return SyncRun.Status.SUCCESS


def upsert_payloads(payloads: list[dict[str, Any]], stats: SyncStats) -> None:
    """Create/update one page worth of notices inside a single transaction.

    Shared by the incremental sync and the archive backfill; ``stats`` is
    updated in place.
    """
    in_scope = _scope_test()

    mapped: dict[str, dict[str, Any]] = {}
    for payload in payloads:
        values = map_notice(payload)
        if values is None:
            stats.skipped += 1
            continue
        if not in_scope(values.get("country") or ""):
            stats.out_of_scope += 1
            continue
        # A page can legitimately repeat an id; last one wins.
        mapped[values["notice_id"]] = values

    if not mapped:
        return

    _attach_project_refs(mapped)

    existing = {
        obj.pk: obj
        for obj in TenderNotice.objects.filter(pk__in=list(mapped)).only(
            "notice_id", "content_hash"
        )
    }

    to_create: list[TenderNotice] = []
    to_update: list[TenderNotice] = []
    now = timezone.now()

    for notice_id, values in mapped.items():
        current = existing.get(notice_id)
        if current is None:
            to_create.append(TenderNotice(**values))
            continue

        if current.content_hash == values["content_hash"]:
            stats.unchanged += 1
            continue

        for name, value in values.items():
            setattr(current, name, value)
        current.updated_at = now
        to_update.append(current)

    with transaction.atomic():
        if to_create:
            TenderNotice.objects.bulk_create(to_create, batch_size=200, ignore_conflicts=True)
            stats.created += len(to_create)
        if to_update:
            TenderNotice.objects.bulk_update(
                to_update, fields=list(UPDATABLE_FIELDS), batch_size=200
            )
            stats.updated += len(to_update)


def _scope_test() -> Callable[[str], bool]:
    """A predicate for "may this country be stored at all?".

    Resolved once per page rather than per notice: the country group is read
    from settings and its membership test builds a set, which is wasted work
    a hundred times over inside the loop.

    An unknown or empty group name would silently reject every notice, so it
    is treated as "no restriction" instead — a typo in the environment must
    not quietly empty the mirror.
    """
    if not getattr(settings, "INGEST_FOCUS_ONLY", False):
        return lambda country: True

    allowed = set(group_countries(settings.FOCUS_COUNTRY_GROUP))
    if not allowed:
        logger.warning(
            "INGEST_FOCUS_ONLY is on but country group %r is unknown — "
            "ingesting every country.",
            settings.FOCUS_COUNTRY_GROUP,
        )
        return lambda country: True

    return lambda country: canonical_country(country) in allowed


def _attach_project_refs(mapped: dict[str, dict[str, Any]]) -> None:
    """Fill in ``project_ref`` for the notices whose project is mirrored.

    One query per page, and it must not be skipped: ``project_ref`` is in
    ``UPDATABLE_FIELDS``, so leaving it out of ``values`` would blank the link
    on every notice the sync touches. A project that has not been mirrored yet
    leaves the key as ``None`` — ``services.projects.link_notices`` connects
    those the moment the project arrives.
    """
    project_ids = {values["project_id"] for values in mapped.values() if values.get("project_id")}
    known = (
        set(
            ProjectProfile.objects.filter(project_id__in=project_ids).values_list(
                "project_id", flat=True
            )
        )
        if project_ids
        else set()
    )
    for values in mapped.values():
        project_id = values.get("project_id")
        values["project_ref_id"] = project_id if project_id in known else None


def _finish_run(run: SyncRun | None, stats: SyncStats, status: str) -> None:
    if run is None:
        return
    run.finished_at = timezone.now()
    run.status = status
    run.pages_fetched = stats.pages_fetched
    run.pages_failed = stats.pages_failed
    run.notices_seen = stats.notices_seen
    run.created_count = stats.created
    run.updated_count = stats.updated
    run.unchanged_count = stats.unchanged
    run.skipped_count = stats.skipped
    run.out_of_scope_count = stats.out_of_scope
    run.upstream_total = stats.upstream_total
    # Keep the audit row small even if every page failed.
    run.error_message = "\n".join(stats.errors[:20])
    run.save(
        update_fields=[
            "finished_at", "status", "pages_fetched", "pages_failed", "notices_seen",
            "created_count", "updated_count", "unchanged_count", "skipped_count",
            "out_of_scope_count", "upstream_total", "error_message",
        ]
    )
