"""Work out every notice's award neighbours once, so no reader has to wait.

    python manage.py compute_similar_awards --status
    python manage.py compute_similar_awards --limit 500
    python manage.py compute_similar_awards --all          # the whole mirror
    python manage.py compute_similar_awards --recompute    # after a method change

The panel at the foot of a tender used to run the whole similarity search per
request. That is roughly fifteen Qdrant round trips for a block of five rows,
and the reader saw it as a panel that appeared a second late — every time,
including the fortieth time somebody opened the same notice.

This is the batch half of the fix: it walks the mirror and writes each notice's
neighbours down. What it does *not* do is decide which of them are awards with
a named winner — that join stays at read time, so a reparse that gives a
contract a winner reaches the panel without anything here running again (D42a).

Resumable by construction: a notice with current rows is skipped, so an
interrupted run is continued by starting it again. `--recompute` is the
opposite instruction and exists for the case the skip is wrong — a changed
`SIMILARITY_VERSION`, or a re-indexed archive whose passages have moved.
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError

from apps.rag_indexer import neighbours
from apps.rag_indexer.models import SIMILARITY_VERSION, SimilarAward
from apps.rag_indexer.services import QdrantUnavailable, get_qdrant_service
from apps.tenders.models import TenderNotice


class Command(BaseCommand):
    help = "Compute and store the award neighbours of each notice."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--status", action="store_true",
            help="Report how much of the mirror has neighbours, then exit.",
        )
        parser.add_argument(
            "--limit", type=int, default=200,
            help="Notices per run. Bounded by default so a first run is a decision.",
        )
        parser.add_argument(
            "--all", action="store_true",
            help="No limit — walk every notice still missing neighbours.",
        )
        parser.add_argument(
            "--focus", action="store_true", default=True,
            help="Only the countries the product is for (the default).",
        )
        parser.add_argument(
            "--everywhere", action="store_true",
            help="The whole mirror, including countries outside the focus group.",
        )
        parser.add_argument(
            "--recompute", action="store_true",
            help="Rewrite notices that already have rows, rather than skipping them.",
        )

    def handle(self, *args, **options):
        focus_only = not options["everywhere"]

        if options["status"]:
            return self._status(focus_only)

        try:
            stats = get_qdrant_service().stats()
            if not stats.connected:
                raise CommandError(f"Qdrant is unreachable: {stats.error}")
            if not stats.exists:
                raise CommandError(
                    "The collection does not exist — run archive_to_qdrant first."
                )
        except QdrantUnavailable as exc:
            raise CommandError(str(exc)) from exc

        if options["recompute"]:
            queryset = TenderNotice.objects.all()
            if focus_only:
                queryset = queryset.in_country_group()
            queryset = queryset.only("notice_id", "bid_description", "project_name")
        else:
            queryset = neighbours.pending(focus_only=focus_only)

        limit = None if options["all"] else max(1, options["limit"])
        rows = queryset.order_by("notice_id")
        if limit:
            rows = rows[:limit]

        started = time.monotonic()
        done = empty = 0
        for notice in rows.iterator(chunk_size=100):
            found = neighbours.compute(notice)
            if found:
                done += 1
            else:
                # Recorded but not written: a notice with no indexed chunks has
                # nothing to store, and writing an empty answer would cache a
                # gap that the next indexing pass was about to fill.
                empty += 1
            if (done + empty) % 50 == 0:
                self.stdout.write(f"  {done + empty} notices, {done} with neighbours…")

        elapsed = time.monotonic() - started
        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"  with neighbours  {done:>8}"))
        self.stdout.write(f"  nothing indexed  {empty:>8}")
        self.stdout.write(f"  elapsed          {elapsed:>8.1f}s")
        self._status(focus_only)

    def _status(self, focus_only: bool) -> None:
        notices = TenderNotice.objects.all()
        if focus_only:
            notices = notices.in_country_group()
        total = notices.count()
        computed = (
            SimilarAward.objects.filter(algo_version=SIMILARITY_VERSION)
            .values("notice_id")
            .distinct()
            .count()
        )
        self.stdout.write("")
        self.stdout.write(f"  notices in scope   {total:>8}")
        self.stdout.write(f"  with neighbours    {computed:>8}")
        self.stdout.write(f"  rows stored        {SimilarAward.objects.count():>8}")
        self.stdout.write(f"  algo version       {SIMILARITY_VERSION:>8}")
