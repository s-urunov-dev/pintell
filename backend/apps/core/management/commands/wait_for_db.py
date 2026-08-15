"""Block until the database accepts connections (used by entrypoint.sh)."""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand, CommandError
from django.db import connections
from django.db.utils import OperationalError


class Command(BaseCommand):
    help = "Wait until the default database is reachable."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--timeout", type=int, default=60, help="Seconds to wait.")
        parser.add_argument("--interval", type=float, default=1.5)

    def handle(self, *args, **options):
        deadline = time.monotonic() + options["timeout"]
        attempt = 0
        while True:
            attempt += 1
            try:
                connections["default"].ensure_connection()
            except OperationalError as exc:
                if time.monotonic() >= deadline:
                    raise CommandError(
                        f"Database still unreachable after {options['timeout']}s: {exc}"
                    ) from exc
                self.stdout.write(f"Database unavailable (attempt {attempt}) — waiting…")
                time.sleep(options["interval"])
            else:
                self.stdout.write(self.style.SUCCESS("Database is ready."))
                return
