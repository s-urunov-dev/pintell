"""Celery tasks for keeping the local mirror in sync."""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.conf import settings
from django.utils import timezone

from .models import SyncRun
from .regions import group_countries
from .services.ai.classification import classify_pending
from .services.ai.enrichment import enrich_pending_awards
from .services.ai.people import enrich_pending_team_leads
from .services.awards import parse_pending_awards
from .services.backfill import run_backfill_slice
from .services.harvest import (
    discover_from_notices,
    discover_from_project_documents,
    harvest_pending,
)
from .services.projects import (
    ProjectSyncStats,
    select_pending_projects,
    sync_pending_projects,
    sync_project,
)
from .services.sync import SyncStats, sync_notices

logger = logging.getLogger(__name__)


@shared_task(
    name="apps.tenders.tasks.sync_procurement_notices",
    bind=True,
    max_retries=2,
    default_retry_delay=120,
    time_limit=60 * 25,
    soft_time_limit=60 * 20,
    ignore_result=True,
)
def sync_procurement_notices(
    self,
    max_pages: int | None = None,
    rows_per_page: int | None = None,
    trigger: str = "celery-beat",
    filters: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Periodic sync (default: every 30 minutes, see CELERY_BEAT_SCHEDULE).

    Upstream errors are absorbed by :func:`sync_notices` — it reports them in
    the returned stats and in the ``SyncRun`` audit row rather than raising,
    so a bad upstream window never poisons the schedule. Only a run where no
    page at all could be fetched is retried.

    With ``INGEST_FOCUS_ONLY`` on the run is split per focus country instead of
    reading the newest pages of the global feed. The global read is not merely
    wasteful there — it is unsound: it sees the newest ~2,500 notices published
    anywhere, of which some 96% are discarded, so a busy day upstream can push
    a focus notice past the window before it is ever seen, and nothing would
    fetch it afterwards once the country partitions report complete. Ten small
    filtered requests always reach each country's newest page.
    """
    if filters is None and getattr(settings, "INGEST_FOCUS_ONLY", False):
        countries = group_countries(settings.FOCUS_COUNTRY_GROUP)
    else:
        countries = []

    if countries:
        stats = _sync_per_country(
            countries,
            max_pages=max_pages,
            rows_per_page=rows_per_page or settings.WORLDBANK["ROWS_PER_PAGE"],
            trigger=trigger,
        )
    else:
        stats = sync_notices(
            max_pages=max_pages or settings.WORLDBANK["MAX_PAGES"],
            rows_per_page=rows_per_page or settings.WORLDBANK["ROWS_PER_PAGE"],
            trigger=trigger,
            filters=filters,
        )

    if stats.pages_fetched == 0 and self.request.retries < self.max_retries:
        logger.warning(
            "Sync fetched no pages (attempt %s/%s) — retrying shortly.",
            self.request.retries + 1, self.max_retries,
        )
        raise self.retry(countdown=120)

    _queue_compliance_extraction(stats)
    return stats.as_dict()


def _queue_compliance_extraction(stats: SyncStats) -> None:
    """Read the newly-arrived open tenders, now rather than on the next tick.

    Queued as a separate task rather than called inline: extraction can spend
    minutes on a long bidding document, and holding the sync's slot open for it
    would let one slow document delay the next sync window.

    Only when the run actually wrote something. A sync that found nothing new
    has, by definition, nothing new to extract, and the half-hourly beat entry
    is there for anything the trigger misses.
    """
    if not settings.COMPLIANCE["AUTO_EXTRACT"]:
        return
    if not (stats.created or stats.updated):
        return

    # Imported here, not at module scope: `apps.tenders` must not depend on
    # `apps.compliance` to be importable — the aggregator ships without it.
    from apps.compliance.tasks import extract_active_requirements

    try:
        extract_active_requirements.delay()
    except Exception:  # noqa: BLE001 - a broker hiccup must not fail the sync
        logger.exception("Could not queue compliance extraction after the sync.")


def _sync_per_country(
    countries: list[str],
    *,
    max_pages: int | None,
    rows_per_page: int,
    trigger: str,
) -> SyncStats:
    """One filtered sync per country, reported as a single run.

    ``max_pages`` is the budget *per country* and is deliberately small: each
    country's feed is ordered newest first, so two pages reach a fortnight of
    notices for even the busiest of them. Catching up further back is the
    backfill's job, not this one's.

    The per-country runs are recorded with ``record_run=False`` and totalled
    into one audit row, because ten rows every half hour would bury the sync
    history under its own bookkeeping.
    """
    budget = max_pages or settings.WORLDBANK.get("FOCUS_PAGES_PER_COUNTRY", 2)
    combined = SyncStats(pages_requested=budget * len(countries))
    run = SyncRun.objects.create(
        trigger=trigger,
        pages_requested=combined.pages_requested,
        status=SyncRun.Status.RUNNING,
    )

    for country in countries:
        stats = sync_notices(
            max_pages=budget,
            rows_per_page=rows_per_page,
            trigger=trigger,
            filters={"project_ctry_name": country},
            record_run=False,
        )
        combined.pages_fetched += stats.pages_fetched
        combined.pages_failed += stats.pages_failed
        combined.notices_seen += stats.notices_seen
        combined.created += stats.created
        combined.updated += stats.updated
        combined.unchanged += stats.unchanged
        combined.skipped += stats.skipped
        combined.out_of_scope += stats.out_of_scope
        combined.errors.extend(f"{country}: {message}" for message in stats.errors)

    run.finished_at = timezone.now()
    run.status = (
        SyncRun.Status.FAILED
        if combined.pages_fetched == 0
        else SyncRun.Status.PARTIAL
        if combined.pages_failed
        else SyncRun.Status.SUCCESS
    )
    run.pages_fetched = combined.pages_fetched
    run.pages_failed = combined.pages_failed
    run.notices_seen = combined.notices_seen
    run.created_count = combined.created
    run.updated_count = combined.updated
    run.unchanged_count = combined.unchanged
    run.skipped_count = combined.skipped
    run.out_of_scope_count = combined.out_of_scope
    run.error_message = "\n".join(combined.errors[:20])
    run.save()

    logger.info(
        "Focus sync over %s countries: %s created, %s updated, %s unchanged",
        len(countries), combined.created, combined.updated, combined.unchanged,
    )
    return combined


@shared_task(
    name="apps.tenders.tasks.backfill_tender_archive",
    max_retries=0,
    time_limit=60 * 25,
    soft_time_limit=60 * 20,
    ignore_result=True,
)
def backfill_tender_archive(
    max_pages: int | None = None,
    rows_per_page: int | None = None,
    partition_key: str | None = None,
) -> dict[str, Any]:
    """Walk one bounded slice of the historical archive (see services/backfill).

    Scheduled every ``BACKFILL_INTERVAL_MINUTES``; each run advances the next
    partition's checkpoint and returns immediately once the whole archive has
    been walked, so leaving it scheduled costs a single query.
    """
    if not settings.BACKFILL_ENABLED:
        logger.info("Backfill disabled (BACKFILL_ENABLED=false) — skipping.")
        return {"idle": True, "reason": "disabled"}

    try:
        result = run_backfill_slice(
            max_pages=max_pages,
            rows_per_page=rows_per_page,
            partition_key=partition_key,
        )
    except Exception as exc:  # noqa: BLE001 - a slice must never kill the worker
        logger.exception("Backfill slice failed")
        return {"idle": False, "error": str(exc)}

    return result.as_dict()


@shared_task(
    name="apps.tenders.tasks.sync_project_profile",
    max_retries=0,
    time_limit=60 * 5,
    soft_time_limit=60 * 4,
    ignore_result=True,
)
def sync_project_profile(project_id: str) -> dict[str, Any]:
    """Mirror a single project on demand.

    Queued by :func:`apps.tenders.services.projects.request_project_sync` when
    a reader opens a notice whose project the periodic cycle has not reached —
    which, before this existed, meant a notice outside the focus feed showed an
    empty documents panel forever.

    Failures are recorded on the profile (error count + backoff) by
    ``sync_project`` itself, so there is nothing here worth retrying: the next
    enrichment cycle will pick the project up through the retry tier.
    """
    stats = ProjectSyncStats()
    try:
        sync_project(project_id, stats=stats)
    except Exception as exc:  # noqa: BLE001 - one project must not kill a worker
        logger.exception("On-demand project sync failed for %s", project_id)
        return {"project_id": project_id, "error": str(exc)}
    return {"project_id": project_id, **stats.as_dict()}


@shared_task(
    name="apps.tenders.tasks.harvest_notice_documents",
    max_retries=0,
    time_limit=60 * 25,
    soft_time_limit=60 * 20,
    ignore_result=True,
)
def harvest_notice_documents(
    discover_limit: int = 500,
    fetch_limit: int | None = None,
) -> dict[str, Any]:
    """Mirror the documents the focus feed links to.

    Scheduled on its own short interval rather than folded into
    :func:`enrich_focus_notices`, for one reason: these links expire. A TOR
    hosted on a borrower's Drive folder stops answering when the tender closes,
    so the window to collect it is measured in weeks — while classification or
    a project refresh can wait for the next cycle without losing anything.

    Two bounded halves. Discovery is cheap (it reads bodies already in the
    database) and keeps the queue stocked; fetching is the metered half, spaced
    per host by :func:`harvest_pending`.
    """
    summary: dict[str, Any] = {}

    try:
        discovery = discover_from_notices(limit=discover_limit)
        discover_from_project_documents(limit=discover_limit, stats=discovery)
        summary["discovery"] = discovery.as_dict()
    except Exception as exc:  # noqa: BLE001 - discovery must not block fetching
        logger.exception("Document discovery failed")
        summary["discovery"] = {"error": str(exc)}

    try:
        summary["harvest"] = harvest_pending(limit=fetch_limit).as_dict()
    except Exception as exc:  # noqa: BLE001 - a batch must never kill the worker
        logger.exception("Document harvest failed")
        summary["harvest"] = {"error": str(exc)}

    logger.info("Harvest cycle complete: %s", summary)
    return summary


@shared_task(
    name="apps.tenders.tasks.enrich_focus_notices",
    max_retries=0,
    time_limit=60 * 20,
    soft_time_limit=60 * 15,
    ignore_result=True,
)
def enrich_focus_notices(
    classify_limit: int | None = None,
    project_limit: int | None = None,
    award_limit: int = 200,
    website_limit: int | None = None,
    team_lead_limit: int | None = None,
) -> dict[str, Any]:
    """Keep the focus feed enriched.

    Five bounded, idempotent steps, in the order the product needs them:

    1. classify actionable notices by direction (rules, escalating to Claude),
    2. mirror the parent project's documents, financing and ESRS,
    3. parse contract awards into structured competitor rows,
    4. look up the winners' websites (AI, metered),
    5. look up the projects' World Bank team leads (AI, metered).

    Step 5 runs last on purpose: it is the weakest contact tier, so when the
    cycle runs out of time it is the one worth losing.

    Every step catches its own failures so one bad upstream response cannot
    stop the others.
    """
    from .models import TenderNotice

    config = settings.ANTHROPIC
    summary: dict[str, Any] = {}

    # 1. Direction classification for the actionable feed.
    try:
        summary["classification"] = classify_pending(
            limit=classify_limit or config["CLASSIFY_BATCH_SIZE"]
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Classification step failed")
        summary["classification"] = {"error": str(exc)}

    # 2. Project documents + ESRS: new projects first, then failed ones whose
    #    backoff has elapsed, then mirrors old enough to be worth refreshing.
    try:
        pending = select_pending_projects(
            TenderNotice.objects.focus()
            .exclude(project_id="")
            .values_list("project_id", flat=True)
            .distinct(),
            limit=project_limit or settings.PROJECTS["BATCH_SIZE"],
        )
        summary["projects"] = {
            **sync_pending_projects(pending.ids).as_dict(),
            "queue": pending.as_dict(),
        }
    except Exception as exc:  # noqa: BLE001
        logger.exception("Project enrichment step failed")
        summary["projects"] = {"error": str(exc)}

    # 3. Contract award parsing (deterministic, no AI).
    try:
        summary["awards"] = parse_pending_awards(limit=award_limit)
    except Exception as exc:  # noqa: BLE001
        logger.exception("Award parsing step failed")
        summary["awards"] = {"error": str(exc)}

    # 4. Competitor website discovery (AI + web search, metered).
    try:
        summary["websites"] = enrich_pending_awards(
            limit=website_limit or config["ENRICH_BATCH_SIZE"]
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Website enrichment step failed")
        summary["websites"] = {"error": str(exc)}

    # 5. Team lead profiles for the third contact tier (AI + web search).
    try:
        summary["team_leads"] = enrich_pending_team_leads(
            limit=team_lead_limit or config["ENRICH_BATCH_SIZE"]
        )
    except Exception as exc:  # noqa: BLE001
        logger.exception("Team lead enrichment step failed")
        summary["team_leads"] = {"error": str(exc)}

    logger.info("Enrichment cycle complete: %s", summary)
    return summary
