"""Read models and operations behind the operator console.

Kept out of the views so the aggregation logic stays testable and the views
remain thin. Nothing here mutates tender data: the console can trigger the
same background jobs an operator would run from the CLI, and re-run the
sanitiser, but it never edits mirrored records by hand.
"""

from __future__ import annotations

import logging
from datetime import timedelta
from typing import Any

from django.conf import settings
from django.core.cache import cache
from django.db import connection
from django.db.models import Count, Max, Min, Q
from django.db.models.functions import ExtractYear
from django.utils import timezone

from apps.tenders.models import BackfillPartition, SyncRun, TenderNotice
from apps.tenders.sanitizers import sanitize_html
from apps.tenders.services.backfill import backfill_progress

logger = logging.getLogger(__name__)

TOP_N = 10


class TaskDispatchError(RuntimeError):
    """The broker refused the job (worker/Redis down)."""


# ---------------------------------------------------------------------------
# Dashboard
# ---------------------------------------------------------------------------
def dashboard_overview() -> dict[str, Any]:
    now = timezone.now()

    # One pass over the table for every headline number: conditional counts
    # instead of four separate scans of the same rows.
    totals = TenderNotice.objects.aggregate(
        total=Count("notice_id"),
        earliest_notice_date=Min("notice_date"),
        latest_notice_date=Max("notice_date"),
        last_synced_at=Max("last_synced_at"),
        open_count=Count("notice_id", filter=Q(deadline_date__gte=now)),
        closing_soon=Count(
            "notice_id",
            filter=Q(deadline_date__gte=now, deadline_date__lte=now + timedelta(days=7)),
        ),
        undated=Count("notice_id", filter=Q(notice_date__isnull=True)),
    )

    open_count = totals["open_count"]
    closing_soon = totals["closing_soon"]
    undated = totals["undated"]

    last_synced_at = totals["last_synced_at"]
    minutes_since_sync = (
        round((now - last_synced_at).total_seconds() / 60, 1) if last_synced_at else None
    )

    return {
        "generated_at": now,
        "notices": {
            "total": totals["total"],
            "open": open_count,
            "closing_within_7_days": closing_soon,
            "without_notice_date": undated,
            "earliest_notice_date": totals["earliest_notice_date"],
            "latest_notice_date": totals["latest_notice_date"],
            "countries": (
                TenderNotice.objects.exclude(country="").values("country").distinct().count()
            ),
        },
        "freshness": {
            "last_synced_at": last_synced_at,
            "minutes_since_sync": minutes_since_sync,
            # The scheduled sync runs every SYNC_INTERVAL_MINUTES; twice that
            # without a write means something is wrong.
            "stale": (
                minutes_since_sync is not None
                and minutes_since_sync > settings.SYNC_INTERVAL_MINUTES * 2
            ),
        },
        "notices_per_year": notices_per_year(),
        "top_countries": _facet("country", TOP_N),
        "procurement_methods": _facet("procurement_method_code", TOP_N),
        "notice_types": _facet("notice_type", TOP_N),
        "sync_health": sync_health(),
        "archive": backfill_progress(notices_stored=totals["total"]),
    }


def notices_per_year() -> list[dict[str, int]]:
    """Notice counts grouped by publication year (undated rows excluded)."""
    rows = (
        TenderNotice.objects.filter(notice_date__isnull=False)
        .annotate(year=ExtractYear("notice_date"))
        .values("year")
        .annotate(count=Count("notice_id"))
        .order_by("year")
    )
    return [{"year": row["year"], "count": row["count"]} for row in rows]


def _facet(field: str, limit: int) -> list[dict[str, Any]]:
    rows = (
        TenderNotice.objects.exclude(**{field: ""})
        .values(field)
        .annotate(count=Count("notice_id"))
        .order_by("-count", field)[:limit]
    )
    return [{"value": row[field], "count": row["count"]} for row in rows]


def sync_health(window_hours: int = 24) -> dict[str, Any]:
    since = timezone.now() - timedelta(hours=window_hours)
    window = SyncRun.objects.filter(started_at__gte=since)
    by_status = {
        row["status"]: row["count"]
        for row in window.values("status").annotate(count=Count("id"))
    }
    last_run = SyncRun.objects.order_by("-started_at").first()
    last_success = (
        SyncRun.objects.filter(status=SyncRun.Status.SUCCESS).order_by("-started_at").first()
    )

    return {
        "window_hours": window_hours,
        # Every run carries a status, so the grouping above already holds the
        # total — no second count of the same window.
        "runs_in_window": sum(by_status.values()),
        "by_status": by_status,
        "failures_in_window": by_status.get(SyncRun.Status.FAILED, 0)
        + by_status.get(SyncRun.Status.PARTIAL, 0),
        "last_run_at": last_run.started_at if last_run else None,
        "last_run_status": last_run.status if last_run else None,
        "last_success_at": last_success.started_at if last_success else None,
    }


# ---------------------------------------------------------------------------
# System health
# ---------------------------------------------------------------------------
def system_status() -> dict[str, Any]:
    return {
        "database": _database_status(),
        "cache": _cache_status(),
        "celery": _celery_status(),
        "configuration": _configuration_summary(),
    }


def _database_status() -> dict[str, Any]:
    try:
        with connection.cursor() as cursor:
            cursor.execute("SELECT 1")
            cursor.fetchone()
    except Exception as exc:  # noqa: BLE001 - status probes never raise
        return {"ok": False, "detail": str(exc)[:200]}
    return {"ok": True, "vendor": connection.vendor, "detail": "reachable"}


def _cache_status() -> dict[str, Any]:
    probe_key = "adminpanel:cache-probe"
    try:
        cache.set(probe_key, "ok", 10)
        value = cache.get(probe_key)
    except Exception as exc:  # noqa: BLE001
        return {"ok": False, "detail": str(exc)[:200]}
    return {"ok": value == "ok", "detail": "read/write ok" if value == "ok" else "probe failed"}


def _celery_status() -> dict[str, Any]:
    """Ping the workers. A missing worker is a status, not an error."""
    try:
        from config.celery import app as celery_app

        replies = celery_app.control.ping(timeout=1.0) or []
    except Exception as exc:  # noqa: BLE001 - broker down is expected here
        return {"ok": False, "workers": [], "detail": str(exc)[:200]}

    workers = [name for reply in replies for name in reply]
    return {
        "ok": bool(workers),
        "workers": workers,
        "detail": f"{len(workers)} worker(s) responding" if workers else "no workers responded",
    }


def _configuration_summary() -> dict[str, Any]:
    """Non-secret settings an operator needs while debugging."""
    worldbank = settings.WORLDBANK
    return {
        "debug": settings.DEBUG,
        "time_zone": settings.TIME_ZONE,
        "upstream_url": worldbank["API_URL"],
        "rows_per_page": worldbank["ROWS_PER_PAGE"],
        "sync_max_pages": worldbank["MAX_PAGES"],
        "sync_interval_minutes": settings.SYNC_INTERVAL_MINUTES,
        "max_offset": worldbank["MAX_OFFSET"],
        "backfill_enabled": settings.BACKFILL_ENABLED,
        "backfill_interval_minutes": settings.BACKFILL_INTERVAL_MINUTES,
        "backfill_pages_per_run": worldbank["BACKFILL_PAGES_PER_RUN"],
        "backfill_page_delay": worldbank["BACKFILL_PAGE_DELAY"],
        "anon_throttle": settings.REST_FRAMEWORK["DEFAULT_THROTTLE_RATES"].get("anon"),
        "attribution": worldbank["ATTRIBUTION"],
    }


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
def dispatch_task(task, **kwargs) -> str:
    """Queue a Celery task, turning broker failures into a clear error."""
    try:
        result = task.apply_async(kwargs=kwargs)
    except Exception as exc:  # noqa: BLE001 - surfaced to the operator as 503
        logger.warning("Console could not queue %s: %s", task.name, exc)
        # Only the underlying cause: the view wraps it in the localised
        # "could not queue the job" sentence.
        raise TaskDispatchError(str(exc) or exc.__class__.__name__) from exc
    return result.id


def resanitize_notice(notice: TenderNotice) -> dict[str, Any]:
    """Re-run the sanitiser over the stored raw HTML.

    Useful after the allow-list changes: it repairs existing rows without
    re-fetching anything from upstream.
    """
    before = notice.notice_text_sanitized or ""
    after = sanitize_html(notice.notice_text_raw)
    changed = after != before

    if changed:
        notice.notice_text_sanitized = after
        notice.save(update_fields=["notice_text_sanitized", "updated_at"])

    return {
        "notice_id": notice.notice_id,
        "changed": changed,
        "chars_before": len(before),
        "chars_after": len(after),
    }


def reset_partition(partition: BackfillPartition) -> BackfillPartition:
    """Send a partition back to offset 0 so it is walked again."""
    partition.next_offset = 0
    partition.status = BackfillPartition.Status.PENDING
    partition.pages_done = 0
    partition.pages_failed = 0
    partition.started_at = None
    partition.finished_at = None
    partition.last_error = ""
    partition.save(
        update_fields=[
            "next_offset", "status", "pages_done", "pages_failed",
            "started_at", "finished_at", "last_error", "updated_at",
        ]
    )
    return partition
