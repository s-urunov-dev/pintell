"""Run a World Bank sync from the command line.

    python manage.py sync_tenders --pages 5
    python manage.py sync_tenders --country Uzbekistan --pages 3
    python manage.py sync_tenders --async          # hand it to a Celery worker
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.tenders.services.sync import sync_notices
from apps.tenders.tasks import sync_procurement_notices


class Command(BaseCommand):
    help = "Fetch procurement notices from the World Bank API into the local database."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--pages", type=int, default=None,
            help="How many upstream pages to pull (default: SYNC_MAX_PAGES).",
        )
        parser.add_argument(
            "--rows", type=int, default=None,
            help="Rows per upstream page (default: SYNC_ROWS_PER_PAGE).",
        )
        parser.add_argument(
            "--offset", type=int, default=0,
            help="Upstream offset to start from (default: 0).",
        )
        parser.add_argument(
            "--country", type=str, default=None,
            help="Restrict to one country (upstream project_ctry_name).",
        )
        parser.add_argument(
            "--method", type=str, default=None,
            help="Restrict to one procurement method code, e.g. RFB, ICB, QCBS.",
        )
        parser.add_argument(
            "--trigger", type=str, default="manual",
            help="Label recorded on the SyncRun audit row.",
        )
        parser.add_argument(
            "--async", action="store_true", dest="run_async",
            help="Queue the sync as a Celery task instead of running inline.",
        )

    def handle(self, *args, **options):
        filters: dict[str, str] = {}
        if options["country"]:
            filters["project_ctry_name"] = options["country"]
        if options["method"]:
            filters["procurement_method_code"] = options["method"]

        if options["run_async"]:
            try:
                result = sync_procurement_notices.delay(
                    max_pages=options["pages"],
                    rows_per_page=options["rows"],
                    trigger=options["trigger"],
                    filters=filters or None,
                )
            except Exception as exc:  # noqa: BLE001 - broker may be down
                raise CommandError(f"Could not queue the sync task: {exc}") from exc
            self.stdout.write(self.style.SUCCESS(f"Queued sync task {result.id}"))
            return

        self.stdout.write("Syncing procurement notices from the World Bank API…")
        stats = sync_notices(
            max_pages=options["pages"],
            rows_per_page=options["rows"],
            start_offset=options["offset"],
            trigger=options["trigger"],
            filters=filters or None,
        )

        self.stdout.write("")
        self.stdout.write(f"  pages fetched : {stats.pages_fetched}/{stats.pages_requested}")
        self.stdout.write(f"  pages failed  : {stats.pages_failed}")
        self.stdout.write(f"  notices seen  : {stats.notices_seen}")
        self.stdout.write(f"  created       : {stats.created}")
        self.stdout.write(f"  updated       : {stats.updated}")
        self.stdout.write(f"  unchanged     : {stats.unchanged}")
        self.stdout.write(f"  skipped       : {stats.skipped}")
        self.stdout.write(f"  out of scope  : {stats.out_of_scope}")
        self.stdout.write(f"  upstream total: {stats.upstream_total}")

        if stats.errors:
            self.stdout.write("")
            self.stdout.write(self.style.WARNING("Errors:"))
            for message in stats.errors[:10]:
                self.stdout.write(f"  - {message}")

        if stats.pages_fetched == 0:
            raise CommandError("No page could be fetched — upstream unreachable?")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Sync complete."))
