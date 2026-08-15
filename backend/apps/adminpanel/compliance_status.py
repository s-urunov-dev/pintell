"""What the automatic extraction is doing, for the operator console.

`pipeline.corpus_report` already answers the *research* question — how much of
the corpus has been read, at what cost, with what grounding rate. This answers
the *operational* one, which is different and narrower: of the tenders a vendor
could bid on right now, how many have been read, when did that last happen, and
did anything fail.

The two are kept apart because their denominators differ, and conflating them
has already misled once (D16: coverage measured against the whole mirror
reported a permanent 0%). Here the denominator is the live set — around thirty
notices — so a number below 100% is a queue, not a research gap.
"""

from __future__ import annotations

from typing import Any

from django.conf import settings
from django.db.models import Count, Max, Q, Sum
from django.utils import timezone

from apps.compliance import llm, pipeline
from apps.compliance.models import (
    ExtractionRun,
    TenderExpertPosition,
    TenderRequirement,
)
from apps.compliance.tasks import available_layers
from apps.tenders.models import TenderNotice

#: Runs shown in the console's activity list. Enough to see the shape of the
#: last cycle without turning the endpoint into a paginated log.
RECENT_RUNS = 15


def compliance_status() -> dict[str, Any]:
    """A snapshot the console can poll.

    Every field is a count or a timestamp — nothing here recomputes an
    extraction or touches the network, so polling it every few seconds costs a
    handful of indexed queries.
    """
    active = TenderNotice.objects.in_country_group().bidding_open()
    active_ids = list(active.values_list("notice_id", flat=True))

    # "Read" means read *at the depth this deployment now runs*, not read at
    # all. The console's own button reads to that depth, so any other
    # denominator would put the operator in the position of watching a screen
    # that says 24/24 while the button in the corner is about to read 24 — which
    # is what the screen looked like before the layer set became part of the
    # question (D22).
    layers = available_layers()
    label = ",".join(layers)
    read_ids = set(
        ExtractionRun.objects.filter(
            notice_id__in=active_ids, layers=label, status=ExtractionRun.Status.OK
        ).values_list("notice_id", flat=True)
    )
    # Not recomputed here — asked of the function the batch itself calls, so the
    # number on the screen is the number of notices the next press will read,
    # including the attempt cap. A count derived independently would drift from
    # the selection the first time either changed.
    pending = len(
        pipeline.select_pending(len(active_ids) or 1, queryset=active, layers=layers)
    )
    with_requirements = set(
        TenderRequirement.objects.filter(notice_id__in=active_ids)
        .exclude(grounding=TenderRequirement.Grounding.NOT_FOUND)
        .values_list("notice_id", flat=True)
    )
    # Counted separately rather than folded into the line above: an operator
    # asking "how much of the open set have we got something for" needs to see
    # that a tender can carry a team and no threshold, or the reverse.
    with_experts = set(
        TenderExpertPosition.objects.filter(notice_id__in=active_ids)
        .exclude(grounding=TenderRequirement.Grounding.NOT_FOUND)
        .values_list("notice_id", flat=True)
    )

    totals = ExtractionRun.objects.aggregate(
        runs=Count("id"),
        cost=Sum("cost_usd"),
        last=Max("created_at"),
        failed=Count("id", filter=Q(status=ExtractionRun.Status.FAILED)),
    )

    return {
        # --- the switch, and what it would run ---------------------------
        "auto_extract": bool(settings.COMPLIANCE["AUTO_EXTRACT"]),
        "batch_size": settings.COMPLIANCE["AUTO_BATCH_SIZE"],
        "layers": label,
        # The distinction an operator needs before reading anything else: a
        # deployment with no key is not broken, it is running the free layer.
        "model_available": llm.is_available(),
        "model": settings.ANTHROPIC["MODEL"] if llm.is_available() else "",
        # --- the live set -------------------------------------------------
        "active_notices": len(active_ids),
        "active_read": len(read_ids),
        "active_pending": pending,
        # Neither read nor offered: attempted ``MAX_ATTEMPTS`` times at this
        # depth and never succeeded. Reported rather than folded into pending,
        # because "the button will not touch these" is the one thing an
        # operator cannot work out from a queue that has stopped draining.
        "active_stalled": max(len(active_ids) - len(read_ids) - pending, 0),
        "active_with_requirements": len(with_requirements),
        "active_with_experts": len(with_experts),
        # --- history ------------------------------------------------------
        "runs_total": totals["runs"] or 0,
        "runs_failed": totals["failed"] or 0,
        "last_run_at": totals["last"],
        "cost_usd": str(totals["cost"] or 0),
        "recent_runs": _recent_runs(),
        "checked_at": timezone.now(),
    }


def _recent_runs() -> list[dict[str, Any]]:
    """The last few runs, newest first, with what each produced.

    ``requirements`` is counted per run rather than summed from the report so
    that a run which found nothing is visibly distinct from one that failed —
    the two look identical in a total, and they mean opposite things about
    whether the stack is working.

    ``expert_positions`` is beside it for exactly that reason. A consulting REOI
    that states no financial threshold but names its whole team is a successful
    run with zero requirements, and without this column it reads in the console
    as a run that found nothing (D20).

    **Both counts are ``distinct``**, and they have to be: two ``Count`` calls
    over two different reverse relations join both tables into one query, so a
    run with 3 requirements and 2 positions would otherwise report 6 of each.
    """
    runs = (
        ExtractionRun.objects.select_related("notice")
        .annotate(
            found=Count("requirements", distinct=True),
            positions=Count("expert_positions", distinct=True),
        )
        .order_by("-created_at")[:RECENT_RUNS]
    )
    return [
        {
            "id": run.pk,
            "notice_id": run.notice_id,
            "title": (run.notice.bid_description or run.notice.project_name or "")[:120],
            "country": run.notice.country,
            "layers": run.layers,
            "status": run.status,
            "model": run.model,
            "requirements": run.found,
            "expert_positions": run.positions,
            "cost_usd": str(run.cost_usd),
            "duration_ms": run.duration_ms,
            "error": run.error,
            "created_at": run.created_at,
        }
        for run in runs
    ]
