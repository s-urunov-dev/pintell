"""Kick off the first sync at container start-up.

Called from ``entrypoint.sh`` so a fresh ``docker compose up`` shows real data
within a minute instead of after the first 30-minute beat tick. The task is
queued on the broker; if the broker is not up yet the command warns and exits
successfully, because failing here must never block the web server.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.tenders.models import TenderNotice
from apps.tenders.tasks import sync_procurement_notices


class Command(BaseCommand):
    help = "Queue an initial sync task unless the database is already populated."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--force", action="store_true",
            help="Queue the sync even when notices already exist.",
        )
        parser.add_argument("--pages", type=int, default=None)

    def handle(self, *args, **options):
        if not settings.INITIAL_SYNC_ENABLED and not options["force"]:
            self.stdout.write("INITIAL_SYNC_ENABLED is false — skipping initial sync.")
            return

        existing = TenderNotice.objects.count()
        if existing and not options["force"]:
            self.stdout.write(
                f"Database already holds {existing} notices — skipping initial sync."
            )
            return

        try:
            result = sync_procurement_notices.apply_async(
                kwargs={
                    "max_pages": options["pages"] or settings.WORLDBANK["MAX_PAGES"],
                    "trigger": "startup",
                },
                # Do not pile up start-up syncs if the worker is slow to appear.
                expires=60 * 30,
            )
        except Exception as exc:  # noqa: BLE001 - broker not ready is not fatal
            self.stdout.write(
                self.style.WARNING(
                    f"Could not queue the initial sync ({exc}). "
                    "The scheduled task will pick it up instead."
                )
            )
            return

        self.stdout.write(self.style.SUCCESS(f"Initial sync queued (task {result.id})."))
