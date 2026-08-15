"""Scheduled extraction, bounded to the tenders a vendor could still bid on.

The mirror holds 25,000 notices; roughly thirty of them are open at any moment.
That ratio is the whole design of this module. Reading the archive would cost a
thousand times more than reading the live set and would answer a question
nobody has — a closed tender's qualification criteria are history. So the
schedule works on ``bidding_open()`` and nothing else, and the bill is bounded
by how many tenders exist rather than by how long the mirror has been running.

**The trigger is the sync, not the clock.** ``sync_procurement_notices`` calls
this at the end of its run, so a notice published at 10:03 is read at 10:03
rather than at whatever the next independent schedule happened to be. A beat
entry exists too, as the thing that catches up after a worker restart or a
failed sync.

That beat entry turned out to have a second job, and for four days it was not
doing it. Reading on the sync means reading *before* the harvester has mirrored
the notice's Terms of Reference — the two run on independent schedules and the
harvester lost that race on every notice measured. The catch-up pass is what
opens the document once it lands, and it can only do that because
``pipeline._already_read`` now asks whether a run saw the sources the notice
has now, rather than whether a run exists. The lag is one beat, not forever.

Nothing here decides what a tender requires. It decides *which* tenders get
read, and when.
"""

from __future__ import annotations

import logging
from typing import Any

from celery import shared_task
from django.conf import settings

from apps.tenders.models import TenderNotice

from . import llm, pipeline

logger = logging.getLogger(__name__)


@shared_task(
    name="apps.compliance.tasks.extract_active_requirements",
    time_limit=60 * 20,
    soft_time_limit=60 * 18,
)
def extract_active_requirements(
    limit: int | None = None,
    layers: Any = None,
    force: bool = False,
) -> dict[str, Any]:
    """Read the requirements of every open tender that has not been read yet.

    ``layers`` defaults to the deepest set the deployment can actually run:
    L1 alone with no key, L1+L2+L3 with one. That is deliberate — the point of
    configuring a key is that extraction gets deeper without anyone rerunning
    anything by hand — and it is safe because the layer stack itself degrades:
    a metered layer with no key records "no API key configured" and the run
    keeps whatever the free layer found (D16).

    Returns the batch counters so the operator console can show what the last
    cycle actually did, rather than only that it ran.
    """
    if not settings.COMPLIANCE["AUTO_EXTRACT"]:
        logger.info("Automatic extraction is switched off (COMPLIANCE_AUTO_EXTRACT).")
        return {"enabled": False}

    chosen = layers if layers is not None else available_layers()
    active = TenderNotice.objects.in_country_group().bidding_open()

    stats = pipeline.extract_pending(
        limit=limit or settings.COMPLIANCE["AUTO_BATCH_SIZE"],
        layers=chosen,
        queryset=active,
        force=force,
    )
    result = {
        "enabled": True,
        "layers": ",".join(pipeline.normalise_layers(chosen)),
        # Both numbers, because they answer different questions: how much work
        # is left, and how much of it this cycle got through.
        "active_notices": active.count(),
        **stats.as_dict(),
    }
    logger.info("Active-tender extraction: %s", result)
    return result


def available_layers() -> tuple[str, ...]:
    """The deepest layer set this deployment can pay for.

    Asking ``llm.is_available()`` rather than reading a setting: the question
    is not what someone configured but whether a request would actually be
    made, and that is the same test the layers themselves use. Without a key
    this returns L1, which needs no network — so the schedule is safe to leave
    on in a deployment that has no intention of spending anything.
    """
    if llm.is_available():
        return pipeline.LAYER_ORDER
    return pipeline.DEFAULT_LAYERS
