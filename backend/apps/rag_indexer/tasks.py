"""Keeping the semantic index current between hand-run imports.

`archive_to_qdrant` was the only way a notice ever reached the collection, and
it is run by hand. That was deliberate — D43 held that the first pass over
25,000 notices should be watched and costed before anything embedded
unattended — but the first pass has since happened, and what was left was a
gap nobody watches: **a notice synced after the last import is not searchable,
not answerable in chat, and has no similar-awards panel**, with nothing in any
screen saying so.

Measured on the deployed server on 2026-08-11: of twenty-two open tenders,
three had zero points in the collection. Their panels were empty for that
reason and no other — the vector search was working perfectly and had nothing
of theirs to match on.

**What this will and will not do without a human.**

* **Notices only.** Their first pass is complete (25,062 of 25,062), so an
  unattended run is a handful of new bodies against a known cost per body.
  Mirrored documents are *not* included: 609 of 619 have never been indexed at
  all, and quietly starting that first pass on a schedule is exactly what D43
  ruled out. `archive_to_qdrant --kinds document` remains the way in, watched.
* **Bounded per run**, so a sync that lands a thousand notices costs one
  bounded run and then another, rather than one surprise.
* **Degrades, never breaks.** No key, no Qdrant, no collection: the run is
  recorded as skipped and the beat schedule tries again later. Nothing in the
  product waits on this — the index is a cache (D43).

The award flags are stamped in the same task for the notices it indexed. They
are payload-only and cost no embedding, and without them a freshly indexed
award is invisible to the awards search, which filters on `is_award` inside the
store (D48). A *reparse* that gives an existing award a winner is still a
`sync_award_flags` run: that is a Postgres event about points this task never
touched, and scanning the whole collection every twenty minutes to catch it
would be work in the wrong place.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.conf import settings

logger = logging.getLogger(__name__)


@shared_task(
    name="apps.rag_indexer.tasks.index_new_notices",
    bind=True,
    # No retry. A failure here is either a dead dependency or an exhausted
    # quota, and both are still true a minute later; the schedule is the retry.
    max_retries=0,
    time_limit=60 * 20,
)
def index_new_notices(self, limit: int | None = None) -> dict[str, Any]:
    """Embed the notices that have appeared since the last run.

    Returns a dict rather than raising, so the beat log reads as a series of
    outcomes: a run that could not start says why in the same shape as one that
    indexed forty sources.
    """
    config = settings.RAG
    if not config["ENABLED"] or not config["AUTO_INDEX"]:
        return {"status": "disabled"}

    # Imported inside the task because the service constructs its clients
    # eagerly, and a worker that never runs this task should not pay for an
    # embedding client and a Qdrant connection at import time.
    from .models import IndexedSource  # noqa: PLC0415
    from .services import EmbeddingUnavailable, QdrantUnavailable  # noqa: PLC0415
    from .services.indexing import IndexingService  # noqa: PLC0415

    bound = limit or config["AUTO_INDEX_LIMIT"]

    # Which sources the run touched, collected as it goes. `RunStats` counts
    # rather than names them — right for a console reporting a 25,000-source
    # import, not enough to stamp the flags of the forty this run added.
    touched: list[str] = []

    try:
        service = IndexingService()
        stats = service.run(
            kinds=(IndexedSource.Kind.NOTICE,),
            # The countries the product is for. The archive walk is a separate,
            # watched job; this one keeps the feed a vendor actually reads
            # searchable, and bounding it that way keeps a backfill slice from
            # turning into an unattended import.
            focus_only=True,
            limit=bound,
            on_progress=lambda candidate, _stats: touched.append(candidate.source_key),
        )
    except (EmbeddingUnavailable, QdrantUnavailable) as exc:
        # Info, not error: on a deployment where the index was never built this
        # is the steady state, and one error line every twenty minutes buries
        # the ones that mean something.
        logger.info("Scheduled indexing skipped: %s", exc)
        return {"status": "unavailable", "reason": str(exc)}
    except Exception as exc:  # noqa: BLE001 - a cache refresh never breaks a worker
        logger.warning("Scheduled indexing failed: %s", exc)
        return {"status": "failed", "reason": str(exc)}

    result = {"status": "ok", "limit": bound, **stats.as_dict()}
    result["neighbours"] = _refresh_neighbours(touched)
    if touched:
        # Every source the run attempted, not only the ones that produced
        # chunks. Stamping a notice that turned out to be empty writes nothing
        # — the store matches on points, and it has none — so the cheaper
        # bookkeeping is to not track the difference.
        result["flagged"] = _stamp_award_flags(touched)
        logger.info(
            "Indexed %s of %s notices (%s chunks); %s award flags written",
            stats.indexed, len(touched), stats.chunks, result["flagged"],
        )
    return result


def _stamp_award_flags(source_keys: list[str]) -> int:
    """Write `is_award` for the notices this run indexed. Payload only.

    Scoped to the run's own keys rather than the whole collection: the full
    sweep is what `sync_award_flags` is for, and repeating it every twenty
    minutes would send hundreds of requests to correct nothing.

    Never fatal. A notice indexed without its flag is a notice missing from the
    awards panel until the next full sync — worse than getting it right, better
    than losing the indexing run that already paid for its embeddings.
    """
    from apps.tenders.models import ContractAward  # noqa: PLC0415

    from .services import QdrantUnavailable, get_qdrant_service  # noqa: PLC0415

    notice_ids = [
        key.split(":", 1)[1] for key in source_keys if key.startswith("notice:")
    ]
    if not notice_ids:
        return 0

    # "Award" means what it means in the panel: a parsed contract with a name
    # on it (D42a). Read from Postgres, so the two definitions cannot drift.
    awards = set(
        ContractAward.objects.exclude(supplier_name="")
        .filter(notice_id__in=notice_ids)
        .values_list("notice_id", flat=True)
    )
    others = [notice_id for notice_id in notice_ids if notice_id not in awards]

    store = get_qdrant_service()
    written = 0
    try:
        if awards:
            written += store.stamp_payload(list(awards), {"is_award": True})
        if others:
            # Stamped `False` rather than left absent, for the reason
            # `sync_award_flags` gives: absent and false filter the same but
            # read differently to whoever debugs the next empty panel.
            written += store.stamp_payload(others, {"is_award": False})
    except QdrantUnavailable as exc:
        logger.info("Award flags not written for this run: %s", exc)
    return written


#: How long a stored neighbour list is trusted for an *open* tender.
#:
#: Nothing in a stored row can notice that a newly parsed award would have been
#: a better neighbour, so the lists go stale silently. A full recomputation of
#: the archive to catch that would be hours of work for a handful of rows; the
#: notices where it matters are the ones a vendor is reading, and there are a
#: few dozen of those. So open tenders are refreshed on this cadence and the
#: archive is recomputed by hand when the method changes.
OPEN_REFRESH_HOURS = 24

#: Notices given neighbours per run, on top of the ones this run indexed. The
#: search is about fifteen Qdrant round trips per notice — cheap enough to do
#: unattended, expensive enough to bound.
NEIGHBOUR_BUDGET = 40


def _refresh_neighbours(indexed_keys: list[str]) -> int:
    """Give the notices this run touched their award neighbours, and refresh
    the open ones whose lists have aged.

    Runs after indexing rather than beside it, because a notice has no
    neighbours to find until its own chunks are in the store — computing first
    would write an empty answer for exactly the notices this task just added.

    Never fatal: a panel that fills on the next run is a panel; failing the
    indexing run that already paid for its embeddings would not be.
    """
    from datetime import timedelta  # noqa: PLC0415

    from django.utils import timezone  # noqa: PLC0415

    from apps.tenders.models import TenderNotice  # noqa: PLC0415

    from . import neighbours  # noqa: PLC0415

    computed = 0
    try:
        fresh_ids = [
            key.split(":", 1)[1] for key in indexed_keys if key.startswith("notice:")
        ]
        wanted = list(
            TenderNotice.objects.filter(notice_id__in=fresh_ids).only(
                "notice_id", "bid_description", "project_name"
            )
        )

        # Then whatever budget is left: open tenders with no list, or one that
        # predates the cutoff.
        room = max(0, NEIGHBOUR_BUDGET - len(wanted))
        if room:
            cutoff = timezone.now() - timedelta(hours=OPEN_REFRESH_HOURS)
            stale = (
                neighbours.pending(focus_only=True, stale_before=cutoff)
                .filter(notice_id__in=_open_ids())
                .exclude(notice_id__in=fresh_ids)
                .order_by("notice_id")[:room]
            )
            wanted.extend(stale)

        for notice in wanted:
            if neighbours.compute(notice):
                computed += 1
    except Exception as exc:  # noqa: BLE001
        logger.info("Neighbour refresh skipped: %s", exc)
    return computed


def _open_ids() -> list[str]:
    """The notices still taking bids, which are the ones a reader is looking at."""
    from apps.tenders.models import TenderNotice  # noqa: PLC0415

    return list(
        TenderNotice.objects.in_country_group()
        .bidding_open()
        .values_list("notice_id", flat=True)
    )


@shared_task(
    name="apps.rag_indexer.tasks.warm_index",
    ignore_result=True,
    # A warm-up that piles up is worse than one that is skipped: each run only
    # matters because it happened recently, so a queued backlog of them is
    # noise. Expiry is just under the schedule interval.
    expires=240,
)
def warm_index() -> dict[str, Any]:
    """Touch the vector store often enough that no reader finds it cold.

    Measured on the deployed server (D63): the first search after an idle
    period took **2.1 s** and every one after it took 32 ms. Nothing was wrong
    — 76,015 points over a 593 MB collection, and a box that is mostly idle,
    so the kernel reclaims the pages Qdrant memory-maps and the next reader
    pays to fault them back in. On a low-traffic deployment that reader is
    almost always a person opening the product for the first time that day.

    **A constant vector, never an embedding.** The only thing this needs is a
    graph traversal that touches pages; what it searches *for* is irrelevant.
    Calling the embedding provider here would spend quota on a query nobody
    asked, every few minutes, forever — which is how a warm-up becomes a bill.

    Several probes rather than one, spread across the space: a single query
    walks one region of the HNSW graph and warms that region. The vectors are
    deterministic so successive runs touch the same pages and keep them
    resident rather than cycling through the whole collection.

    Degrades like everything else that talks to Qdrant: a dead store makes this
    a recorded no-op, never an error. It is a cache being kept warm — there is
    nothing here worth waking anyone for.
    """
    if not settings.RAG["ENABLED"]:
        return {"warmed": 0, "skipped": "rag disabled"}

    from .services.qdrant import QdrantUnavailable, get_qdrant_service

    size = settings.RAG["VECTOR_SIZE"]
    # Three fixed directions. Unit-ish and cheap to build; the values carry no
    # meaning beyond being different from each other.
    probes = [
        [1.0 if i % 3 == n else 0.0 for i in range(size)]
        for n in range(3)
    ]

    warmed = 0
    try:
        store = get_qdrant_service()
        for vector in probes:
            store.search(vector=vector, limit=8)
            warmed += 1
    except QdrantUnavailable as exc:
        logger.info("Index warm-up skipped: %s", exc)
        return {"warmed": warmed, "skipped": str(exc)}
    except Exception as exc:  # noqa: BLE001 - a warm-up must never raise
        logger.info("Index warm-up failed: %s", exc)
        return {"warmed": warmed, "error": str(exc)}

    return {"warmed": warmed}
