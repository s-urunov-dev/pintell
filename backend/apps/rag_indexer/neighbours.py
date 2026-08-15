"""Computing a notice's award neighbours once, and reading them back cheaply.

The similarity search itself is `SimilarityService` and is unchanged: three
distinctive chunks of the notice (D45), searched against award points only
(D48). What changed is *when* it runs. It used to run on every request for a
tender page, which is about fifteen Qdrant round trips to draw one panel — a
second of latency the reader watched arrive after everything around it, paid
again on every view, for an answer that does not change between them.

So this module is the seam between the expensive computation and the cheap
read. Three callers write through it — the archive command, the scheduled task,
and the first reader of a notice nobody has asked for yet — and they all write
the same rows in the same way.

**What is deliberately *not* cached is the panel.** These rows are candidate
neighbours; whether one is an award with a named winner is a fact about
`ContractAward` that a reparse can change (D42a), so that join stays at read
time in `award_feed`. The consequence is the useful one: a winner appearing in
a reparse shows up in the panel immediately, with nothing recomputed.
"""

from __future__ import annotations

import logging

from django.db import transaction
from django.utils import timezone

from apps.tenders.models import TenderNotice

from .models import SIMILARITY_VERSION, SimilarAward
from .services import QdrantUnavailable

logger = logging.getLogger(__name__)

#: Neighbours stored per notice. More than the panel shows (five), because the
#: read-time join discards every candidate that is not an award with a named
#: winner — and a notice whose first four neighbours all fail that test should
#: still fill a panel rather than show a short one.
KEEP = 20

#: Notices scanned per query chunk inside the store. Generous for the same
#: reason `SimilarityService.similar_award_notices` documents: the caller
#: discards most of what comes back.
SCAN = 200


def cached_for(notice: TenderNotice) -> list[SimilarAward]:
    """The stored neighbours of one notice, best first. Never computes."""
    return list(
        SimilarAward.objects.filter(
            notice_id=notice.pk, algo_version=SIMILARITY_VERSION
        ).order_by("rank")
    )


def compute(notice: TenderNotice) -> list[SimilarAward]:
    """Search for this notice's neighbours and write them down.

    Returns the stored rows. An empty list is a real answer — a notice with no
    indexed chunks has no neighbours to find — and it is **not** written as
    rows, so the next run tries again rather than caching a gap that a later
    indexing pass would have filled.

    Never raises. A dead store means the panel is empty for now, which is what
    every other failure path in this feature already does.
    """
    # Resolved at call time, from the module rather than from a name bound at
    # import: that is what makes the service swappable in a test, and it is the
    # same reason `award_feed` imports this module inside its function.
    from .services import get_similarity_service  # noqa: PLC0415

    service = get_similarity_service()
    title = notice.bid_description or notice.project_name or ""
    try:
        found = service.similar_award_notices(
            f"notice:{notice.pk}", limit=KEEP, scan=SCAN, title=title
        )
    except QdrantUnavailable as exc:
        logger.info("No neighbours computed for %s: %s", notice.pk, exc)
        return []
    except Exception as exc:  # noqa: BLE001 - a panel, never a page
        logger.warning("Neighbour search failed for %s: %s", notice.pk, exc)
        return []

    rows = [
        SimilarAward(
            notice_id=notice.pk,
            award_notice_id=neighbour_id,
            rank=rank,
            score=score,
            match_passage=passage,
            algo_version=SIMILARITY_VERSION,
            computed_at=timezone.now(),
        )
        for rank, (neighbour_id, score, passage) in enumerate(found[:KEEP])
    ]
    if not rows:
        return []

    # Replaced rather than merged: a recomputation is a new answer, and rows
    # from the previous one would otherwise linger at ranks the new list does
    # not use. One transaction so a reader never sees the gap between them.
    with transaction.atomic():
        SimilarAward.objects.filter(notice_id=notice.pk).delete()
        SimilarAward.objects.bulk_create(rows)
    return rows


def ensure(notice: TenderNotice) -> list[SimilarAward]:
    """The neighbours, computing them if this notice has never been asked for.

    The read-through is what keeps the panel from disappearing while the batch
    catches up: a notice synced ten minutes ago is answered by computing, once,
    for whoever opens it first. Every reader after that gets the stored rows.
    """
    rows = cached_for(notice)
    if rows:
        return rows
    return compute(notice)


def pending(*, focus_only: bool = True, stale_before=None):
    """Notices whose neighbours are missing or out of date.

    ``stale_before`` refreshes what has already been computed — the reason it
    exists is that a *new award* can be a better neighbour of an old notice,
    and nothing in a stored row can notice that on its own. The scheduled task
    passes a cutoff for open tenders only, because those are the notices a
    vendor is reading and the archive would be a full recomputation.
    """
    queryset = TenderNotice.objects.all()
    if focus_only:
        queryset = queryset.in_country_group()

    current = SimilarAward.objects.filter(algo_version=SIMILARITY_VERSION)
    if stale_before is not None:
        current = current.filter(computed_at__gte=stale_before)
    return queryset.exclude(
        notice_id__in=current.values_list("notice_id", flat=True)
    ).only("notice_id", "bid_description", "project_name")
