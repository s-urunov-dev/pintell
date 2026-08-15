"""Historical archive backfill.

Why this exists
---------------
The incremental sync only ever looks at the newest pages, so on its own the
mirror would never hold the ~413 000 notices the World Bank has published since
1998. Walking the whole result set with ``os`` is impossible in one query: the
upstream search engine rejects any offset above 100 000 ("Value must be between
0 and 100000. Parameter name: $skip").

So history is fetched in **partitions**, each small enough to walk end to end:

* ``recent`` — one unfiltered walk covering the newest 100 000 notices. Only
  when the ingest scope gate is off; see ``ensure_partitions`` for why.
* ``country:<name>`` — one walk per ``project_ctry_name``. The largest single
  country (India, ~47 000) is well under the cap, and the union of countries
  covers the archive.
* ``country:<name>|method:<code>`` — created automatically if a country ever
  exceeds the cap, so the design does not silently lose rows as the archive
  grows.

Each partition row is a checkpoint (``next_offset``), and every run consumes a
bounded number of pages. The walk therefore survives restarts, spreads load
over time, and — because every notice is upserted by its upstream id —
tolerates the overlap between partitions and the incremental sync.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass
from typing import Any, Iterable

from django.conf import settings
from django.core.cache import cache
from django.db import transaction
from django.db.models import Q
from django.utils import timezone

from ..models import BackfillPartition, SyncRun, TenderNotice
from ..regions import group_countries
from .sync import SyncStats, upsert_payloads
from .worldbank import WorldBankAPIError, WorldBankClient

logger = logging.getLogger(__name__)

RECENT_KEY = "recent"

# Only one slice may walk the archive at a time: the scheduled task, a second
# Celery worker and a manual `backfill_tenders` run would otherwise re-fetch the
# same offsets and hammer a public API for no extra data.
LOCK_KEY = "tenders:backfill:slice-lock"
# The lock is a short-lived lease refreshed after every page, not a long hold:
# if a slice is killed (container restart, OOM) the `finally` release never
# runs, and a long TTL would stall every later slice until it expired. With a
# lease, the archive walk resumes on its own within LOCK_TTL seconds.
LOCK_TTL = 120

# Procurement method codes used to split a country that outgrows the offset cap.
SUBDIVISION_METHODS = (
    "RFB", "RFQ", "ICB", "NCB", "QCBS", "CQS", "INDV", "LCS", "DIR", "FBS", "SSS",
)


@dataclass
class BackfillSlice:
    """Outcome of one bounded slice of work."""

    partition_key: str = ""
    pages_done: int = 0
    pages_failed: int = 0
    created: int = 0
    updated: int = 0
    unchanged: int = 0
    finished_partition: bool = False
    idle: bool = False  # nothing left to do
    idle_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "partition": self.partition_key,
            "pages_done": self.pages_done,
            "pages_failed": self.pages_failed,
            "created": self.created,
            "updated": self.updated,
            "unchanged": self.unchanged,
            "finished_partition": self.finished_partition,
            "idle": self.idle,
            "idle_reason": self.idle_reason,
        }


# ---------------------------------------------------------------------------
# Partition planning
# ---------------------------------------------------------------------------
def ensure_partitions(extra_countries: Iterable[str] = ()) -> int:
    """Create any missing partitions; returns how many were added.

    The country list is discovered from the notices already mirrored — the
    unfiltered ``recent`` walk alone surfaces every country that appears in the
    archive — and is re-checked on every call, so countries that show up later
    are picked up automatically.

    Discovery alone is not enough once ``INGEST_FOCUS_ONLY`` bounds the mirror,
    and the failure is silent: a focus country with nothing stored yet gets no
    partition, so it is never fetched, so it stays empty. Belarus and the
    Russian Federation sat at zero that way while upstream held 1,783 notices
    between them. When the gate is on, the focus group is therefore seeded
    outright — the scope defines the partitions, not the other way round.

    Which is also why ``recent`` is not created in that mode. Both of its jobs
    belong to the unbounded mirror: it walks the newest 100 000 notices of the
    *global* feed, and it is what discovers the country list in the first place.
    With the gate on, the countries are already known, and the walk stores only
    the ~6% of rows that fall inside the group — every one of which that
    country's own partition fetches anyway. It is 1 000 upstream requests for
    nothing, and it is picked first, so it delays the partitions that do carry
    the corpus. Turn the gate off and it comes back, because then it is the only
    thing that knows which countries exist.
    """
    created = 0
    focus_only = getattr(settings, "INGEST_FOCUS_ONLY", False)

    if not focus_only:
        _, was_created = BackfillPartition.objects.get_or_create(
            key=RECENT_KEY,
            defaults={
                "kind": BackfillPartition.Kind.RECENT,
                "label": "Newest notices (unfiltered)",
                "filters": {},
            },
        )
        created += int(was_created)

    countries = set(
        TenderNotice.objects.exclude(country="")
        .values_list("country", flat=True)
        .distinct()
    )
    countries.update(name.strip() for name in extra_countries if name and name.strip())
    if getattr(settings, "INGEST_FOCUS_ONLY", False):
        countries.update(group_countries(settings.FOCUS_COUNTRY_GROUP))

    known = set(
        BackfillPartition.objects.filter(
            kind=BackfillPartition.Kind.COUNTRY
        ).values_list("key", flat=True)
    )

    new_partitions = [
        BackfillPartition(
            key=f"country:{country}",
            kind=BackfillPartition.Kind.COUNTRY,
            label=country,
            filters={"project_ctry_name": country},
        )
        for country in sorted(countries)
        if f"country:{country}" not in known
    ]
    if new_partitions:
        BackfillPartition.objects.bulk_create(new_partitions, ignore_conflicts=True)
        created += len(new_partitions)
        logger.info("Backfill: registered %s new country partition(s).", len(new_partitions))

    return created


def _subdivide(partition: BackfillPartition) -> int:
    """Split a partition that reports more rows than the offset cap can reach."""
    if partition.kind != BackfillPartition.Kind.COUNTRY:
        # 'recent' is expected to hit the cap: the country partitions cover the
        # rest of the archive, so there is nothing to split.
        return 0

    country = partition.filters.get("project_ctry_name", partition.label)
    children = [
        BackfillPartition(
            key=f"country:{country}|method:{code}",
            kind=BackfillPartition.Kind.COUNTRY_METHOD,
            label=f"{country} · {code}",
            filters={"project_ctry_name": country, "procurement_method_code": code},
        )
        for code in SUBDIVISION_METHODS
    ]
    BackfillPartition.objects.bulk_create(children, ignore_conflicts=True)
    logger.warning(
        "Backfill: %s exceeds the offset cap (%s rows) — split into %s method partitions.",
        partition.key, partition.upstream_total, len(children),
    )
    return len(children)


def next_partition() -> BackfillPartition | None:
    """Pick the next partition to work on.

    ``recent`` goes first (it gives the widest coverage the fastest and seeds
    the country list), then partitions already in progress, then untouched
    ones — smallest known total first, so short partitions retire quickly.

    Unless the ingest scope gate is on, in which case ``recent`` is skipped for
    the reasons ``ensure_partitions`` gives. Skipped rather than deleted, and
    that distinction is the whole point: this deployment had a ``recent`` row
    left from before the gate was turned on, sitting at offset 30 000 of
    414 197 and ahead of nine untouched country partitions in this very
    ordering. It would have held the walk for 3 800 more pages while the
    countries the product is about stayed empty. Dropping the row would fix
    that and throw away the checkpoint; leaving it and not choosing it fixes it
    and keeps the walk resumable exactly where it stopped, so turning the gate
    off costs nothing.
    """
    pending = Q(status=BackfillPartition.Status.PENDING) | Q(
        status=BackfillPartition.Status.RUNNING
    )
    focus_only = getattr(settings, "INGEST_FOCUS_ONLY", False)

    # The exclusion has to apply to every branch below, not just the first one.
    # A left-over ``recent`` is RUNNING with a non-zero offset, which is exactly
    # what the in-progress branch looks for — filtering only the first query
    # would move the starvation one branch down and change nothing.
    candidates = BackfillPartition.objects.all()
    if focus_only:
        candidates = candidates.exclude(key=RECENT_KEY)

    if not focus_only:
        recent = candidates.filter(pending, key=RECENT_KEY).first()
        if recent is not None:
            return recent

    in_progress = (
        candidates.filter(
            status=BackfillPartition.Status.RUNNING, next_offset__gt=0
        )
        .order_by("updated_at")
        .first()
    )
    if in_progress is not None:
        return in_progress

    return candidates.filter(pending).order_by("upstream_total", "key").first()


# ---------------------------------------------------------------------------
# Execution
# ---------------------------------------------------------------------------
def run_backfill_slice(
    *,
    max_pages: int | None = None,
    rows_per_page: int | None = None,
    partition_key: str | None = None,
    client: WorldBankClient | None = None,
    page_delay: float | None = None,
    trigger: str = "backfill",
) -> BackfillSlice:
    """Fetch a bounded number of pages for one partition and checkpoint it."""
    config = settings.WORLDBANK
    max_pages = max_pages or config["BACKFILL_PAGES_PER_RUN"]
    rows_per_page = rows_per_page or config["ROWS_PER_PAGE"]
    page_delay = config["BACKFILL_PAGE_DELAY"] if page_delay is None else page_delay
    max_offset = config["MAX_OFFSET"]
    client = client or WorldBankClient()

    # ``add`` only succeeds when the key is absent — a cheap distributed mutex.
    if not cache.add(LOCK_KEY, timezone.now().isoformat(), LOCK_TTL):
        logger.info("Backfill: another slice is already running — skipping this run.")
        return BackfillSlice(idle=True, idle_reason="locked")

    try:
        return _run_slice_locked(
            max_pages=max_pages,
            rows_per_page=rows_per_page,
            partition_key=partition_key,
            client=client,
            page_delay=page_delay,
            max_offset=max_offset,
            trigger=trigger,
        )
    finally:
        cache.delete(LOCK_KEY)


def _run_slice_locked(
    *,
    max_pages: int,
    rows_per_page: int,
    partition_key: str | None,
    client: WorldBankClient,
    page_delay: float,
    max_offset: int,
    trigger: str,
) -> BackfillSlice:
    ensure_partitions()

    if partition_key:
        partition = BackfillPartition.objects.filter(key=partition_key).first()
        if partition is None:
            raise ValueError(f"Unknown backfill partition: {partition_key!r}")
    else:
        partition = next_partition()

    if partition is None or partition.is_done:
        logger.info("Backfill: nothing left to do — archive walk is complete.")
        return BackfillSlice(idle=True, idle_reason="complete")

    result = BackfillSlice(partition_key=partition.key)
    stats = SyncStats()
    run = SyncRun.objects.create(
        trigger=f"{trigger}:{partition.key}"[:32],
        pages_requested=max_pages,
        status=SyncRun.Status.RUNNING,
    )

    if partition.started_at is None:
        partition.started_at = timezone.now()
    partition.status = BackfillPartition.Status.RUNNING
    partition.save(update_fields=["status", "started_at", "updated_at"])

    logger.info(
        "Backfill slice: partition=%s offset=%s pages=%s rows=%s",
        partition.key, partition.next_offset, max_pages, rows_per_page,
    )

    offset = partition.next_offset
    completed = False

    for page_index in range(max_pages):
        if offset >= max_offset:
            # Everything this query can expose has been walked.
            completed = True
            break

        try:
            page = client.fetch_page(offset=offset, rows=rows_per_page, **partition.filters)
        except WorldBankAPIError as exc:
            result.pages_failed += 1
            stats.errors.append(f"offset={offset}: {exc}")
            partition.pages_failed += 1
            partition.last_error = str(exc)[:2000]
            logger.warning("Backfill page failed (%s, offset=%s): %s", partition.key, offset, exc)
            # Leave the checkpoint where it is and let the next run retry it.
            break

        if page.total is not None:
            partition.upstream_total = page.total

        if page.is_empty:
            completed = True
            break

        upsert_payloads(page.notices, stats)

        offset += rows_per_page
        result.pages_done += 1
        partition.pages_done += 1
        # Renew the lease: proof this slice is still alive and working.
        cache.set(LOCK_KEY, timezone.now().isoformat(), LOCK_TTL)

        if page.total is not None and offset >= page.total:
            completed = True
            break

        if page_delay:
            time.sleep(page_delay)

    partition.next_offset = offset
    partition.created_count += stats.created
    partition.updated_count += stats.updated
    partition.unchanged_count += stats.unchanged

    if completed:
        if (
            partition.upstream_total is not None
            and partition.upstream_total > max_offset
            and _subdivide(partition)
        ):
            partition.status = BackfillPartition.Status.SUBDIVIDED
        else:
            partition.status = BackfillPartition.Status.COMPLETED
        partition.finished_at = timezone.now()
        result.finished_partition = True
        logger.info(
            "Backfill: partition %s %s at offset %s (total %s).",
            partition.key, partition.status, offset, partition.upstream_total,
        )
    elif result.pages_done == 0 and result.pages_failed:
        partition.status = BackfillPartition.Status.PENDING

    with transaction.atomic():
        partition.save()
        run.finished_at = timezone.now()
        run.status = (
            SyncRun.Status.PARTIAL
            if result.pages_failed and result.pages_done
            else SyncRun.Status.FAILED
            if result.pages_failed
            else SyncRun.Status.SUCCESS
        )
        run.pages_fetched = result.pages_done
        run.pages_failed = result.pages_failed
        run.notices_seen = stats.notices_seen
        run.created_count = stats.created
        run.updated_count = stats.updated
        run.unchanged_count = stats.unchanged
        run.skipped_count = stats.skipped
        run.out_of_scope_count = stats.out_of_scope
        run.upstream_total = partition.upstream_total
        run.error_message = "\n".join(stats.errors[:20])
        run.save()

    result.created = stats.created
    result.updated = stats.updated
    result.unchanged = stats.unchanged
    logger.info(
        "Backfill slice done: %s pages=%s failed=%s created=%s updated=%s unchanged=%s",
        partition.key, result.pages_done, result.pages_failed,
        result.created, result.updated, result.unchanged,
    )
    return result


def backfill_progress(notices_stored: int | None = None) -> dict[str, Any]:
    """Coverage summary for the API and the CLI status table.

    Progress is measured in **partitions**, not rows: a partition's row count is
    unknown until its first page is fetched, so a row-based percentage would
    read 100% while most partitions had not even started.

    ``notices_stored`` lets a caller that has already counted the notices hand
    the number over instead of paying for a second COUNT of the whole table.
    """
    # Only the checkpoint columns are read; the JSON filters and the stored
    # error text of every partition would be dead weight here.
    partitions = list(
        BackfillPartition.objects.only("key", "status", "next_offset", "upstream_total")
    )
    done = [p for p in partitions if p.is_done]
    walked = sum(min(p.next_offset, p.reachable_total or p.next_offset) for p in partitions)
    # Only partitions that have reported a total contribute a known row target.
    reachable_known = sum(p.reachable_total or 0 for p in partitions)

    recent = next((p for p in partitions if p.key == RECENT_KEY), None)

    return {
        "enabled": settings.BACKFILL_ENABLED,
        "partitions_total": len(partitions),
        "partitions_completed": len(done),
        "partitions_pending": len(partitions) - len(done),
        # Share of partitions finished — the honest 0→100 progression.
        "percent": round(len(done) / len(partitions) * 100, 1) if partitions else 0.0,
        "rows_walked": walked,
        "rows_reachable_known": reachable_known,
        "notices_stored": (
            notices_stored if notices_stored is not None else TenderNotice.objects.count()
        ),
        # What upstream says the whole archive holds (from the unfiltered query).
        "upstream_total": recent.upstream_total if recent else None,
        "complete": bool(partitions) and len(done) == len(partitions),
    }
