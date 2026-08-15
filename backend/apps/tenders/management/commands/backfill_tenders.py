"""Walk the historical archive (older notices) into the local database.

    python manage.py backfill_tenders --status              # progress table
    python manage.py backfill_tenders --pages 50            # one slice
    python manage.py backfill_tenders --loop                # until complete
    python manage.py backfill_tenders --partition country:India
    python manage.py backfill_tenders --reset country:India # re-walk from 0
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError

from apps.tenders.models import BackfillPartition
from apps.tenders.services.backfill import (
    LOCK_TTL,
    backfill_progress,
    ensure_partitions,
    run_backfill_slice,
)


class Command(BaseCommand):
    help = "Backfill historical World Bank procurement notices, partition by partition."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--pages", type=int, default=None, help="Pages per slice.")
        parser.add_argument("--rows", type=int, default=None, help="Rows per upstream page.")
        parser.add_argument(
            "--partition", type=str, default=None,
            help="Work on one specific partition key, e.g. 'country:India'.",
        )
        parser.add_argument(
            "--loop", action="store_true",
            help="Keep running slices until every partition is complete.",
        )
        parser.add_argument(
            "--max-slices", type=int, default=0,
            help="With --loop: stop after this many slices (0 = unlimited).",
        )
        parser.add_argument(
            "--delay", type=float, default=None,
            help="Seconds to pause between upstream pages (default from settings).",
        )
        parser.add_argument("--status", action="store_true", help="Print progress and exit.")
        parser.add_argument(
            "--reset", type=str, default=None,
            help="Reset a partition ('all' for every partition) back to offset 0.",
        )

    def handle(self, *args, **options):
        ensure_partitions()

        if options["reset"]:
            self._reset(options["reset"])
            return

        if options["status"]:
            self._print_status()
            return

        slices = 0
        while True:
            result = run_backfill_slice(
                max_pages=options["pages"],
                rows_per_page=options["rows"],
                partition_key=options["partition"],
                page_delay=options["delay"],
                trigger="manual",
            )
            slices += 1

            if result.idle and result.idle_reason == "locked":
                # Another slice (scheduled task or a second shell) holds the
                # lease. That is not "done" — wait for it rather than exiting
                # with a misleading success message.
                self.stdout.write(
                    self.style.WARNING(
                        "Another backfill slice is running; its lease expires within "
                        f"{LOCK_TTL}s."
                    )
                )
                if not options["loop"]:
                    break
                time.sleep(min(LOCK_TTL, 30))
                continue

            if result.idle:
                self.stdout.write(self.style.SUCCESS("Archive walk is complete."))
                break

            self.stdout.write(
                f"[{slices:>4}] {result.partition_key:<44} "
                f"pages={result.pages_done:<3} failed={result.pages_failed:<2} "
                f"created={result.created:<5} updated={result.updated:<4} "
                f"unchanged={result.unchanged:<5}"
                + ("  ✓ partition done" if result.finished_partition else "")
            )

            if not options["loop"]:
                break
            if options["partition"] and result.finished_partition:
                break
            if options["max_slices"] and slices >= options["max_slices"]:
                self.stdout.write(f"Reached --max-slices={options['max_slices']}; stopping.")
                break
            if result.pages_done == 0 and result.pages_failed:
                raise CommandError(
                    f"Upstream refused every page for {result.partition_key}; stopping. "
                    "Re-run later — progress is checkpointed."
                )

        self.stdout.write("")
        self._print_status()

    # -- helpers -----------------------------------------------------------
    def _reset(self, key: str) -> None:
        queryset = BackfillPartition.objects.all()
        if key != "all":
            queryset = queryset.filter(key=key)
            if not queryset.exists():
                raise CommandError(f"No partition with key {key!r}.")

        updated = queryset.update(
            next_offset=0,
            status=BackfillPartition.Status.PENDING,
            pages_done=0,
            pages_failed=0,
            started_at=None,
            finished_at=None,
            last_error="",
        )
        self.stdout.write(self.style.SUCCESS(f"Reset {updated} partition(s)."))

    def _print_status(self) -> None:
        progress = backfill_progress()
        upstream = progress["upstream_total"]
        self.stdout.write(
            f"Archive walk: {progress['partitions_completed']}/"
            f"{progress['partitions_total']} partitions done "
            f"({progress['percent']}%)"
        )
        self.stdout.write(
            f"Stored locally: {progress['notices_stored']:,} notices"
            + (f" of {upstream:,} upstream" if upstream else "")
            + f" · {progress['rows_walked']:,} upstream rows walked"
        )

        pending = (
            BackfillPartition.objects.exclude(
                status__in=[
                    BackfillPartition.Status.COMPLETED,
                    BackfillPartition.Status.SUBDIVIDED,
                ]
            )
            .order_by("-next_offset")[:10]
        )
        if pending:
            self.stdout.write("")
            self.stdout.write("In progress / pending (top 10 by rows walked):")
            for partition in pending:
                total = partition.upstream_total
                self.stdout.write(
                    f"  {partition.key:<46} {partition.next_offset:>7,} / "
                    f"{total if total is not None else '?':>7} "
                    f"[{partition.status}]"
                )
