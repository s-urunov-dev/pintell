"""Fill ``search_vector`` for the rows that existed before the trigger did.

    python manage.py backfill_search_vector --status      # how far along
    python manage.py backfill_search_vector               # run it
    python manage.py backfill_search_vector --batch 500   # smaller bites
    python manage.py backfill_search_vector --limit 5000  # stop early

The column arrives empty (migration 0023) and the trigger (0024) only fires on
writes, so every row already in the table stays NULL until something fills it.
Until it *is* filled, `RAG_LEXICAL_STORED_VECTOR` must stay off: a search
reading a half-filled column silently returns fewer notices rather than slower
ones, which is the failure mode worth going to some trouble to avoid.

**Batched because the alternative is a lock.** One ``UPDATE`` over 25,463 rows
holds row locks on the whole table for the length of the write and bloats it by
a full table's worth of dead tuples in a single transaction. A thousand rows at
a time commits, releases, and lets autovacuum keep up — the run takes longer in
wall-clock and never blocks a reader.

**Raw SQL, not ``SearchVector``.** The value has to be byte-identical to what
the trigger writes, or a row's rank would depend on which of the two last
touched it. Django renders the text-search configuration as a bind parameter
where the trigger states it as ``'english'::regconfig``; the resulting tsvectors
are in fact the same, but "in fact the same" is a thing to assert in a test
rather than to rely on in a data migration. So this issues the trigger's own
expression, and ``test_search_vector`` asserts the two agree.

Re-runnable and interruptible: it only ever selects ``search_vector IS NULL``,
so stopping it and starting it again resumes rather than repeats.
"""

from __future__ import annotations

import time

from django.core.management.base import BaseCommand
from django.db import connection

from apps.tenders.models import TenderNotice

#: The trigger's expression, verbatim. See the module docstring.
FILL_SQL = """
UPDATE tenders_tendernotice
SET search_vector = to_tsvector(
        'english'::regconfig,
        COALESCE(bid_description, '') || ' ' || COALESCE(notice_text_sanitized, '')
    )
WHERE notice_id IN (
    SELECT notice_id FROM tenders_tendernotice
    WHERE search_vector IS NULL
    LIMIT %s
);
"""


class Command(BaseCommand):
    help = "Fill TenderNotice.search_vector for rows written before the trigger existed."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--batch", type=int, default=1000,
            help="Rows per transaction (default 1000).",
        )
        parser.add_argument(
            "--limit", type=int, default=0,
            help="Stop after this many rows. 0 = until nothing is left.",
        )
        parser.add_argument(
            "--status", action="store_true",
            help="Report coverage and exit, changing nothing.",
        )

    def handle(self, *args, **options) -> None:
        if connection.vendor != "postgresql":
            self.stdout.write("Not PostgreSQL — there is no tsvector column to fill.")
            return

        total = TenderNotice.objects.count()
        remaining = TenderNotice.objects.filter(search_vector__isnull=True).count()
        done = total - remaining

        if options["status"]:
            pct = (100.0 * done / total) if total else 100.0
            self.stdout.write(f"notices:   {total}")
            self.stdout.write(f"filled:    {done} ({pct:.1f}%)")
            self.stdout.write(f"remaining: {remaining}")
            # The cutover is a setting, and this is the sentence that says
            # whether flipping it is safe yet.
            if remaining:
                self.stdout.write(
                    self.style.WARNING(
                        "Not complete — keep RAG_LEXICAL_STORED_VECTOR off."
                    )
                )
            else:
                self.stdout.write(
                    self.style.SUCCESS(
                        "Complete — RAG_LEXICAL_STORED_VECTOR is safe to switch on."
                    )
                )
            return

        if not remaining:
            self.stdout.write(self.style.SUCCESS("Nothing to do: every row is filled."))
            return

        batch = max(options["batch"], 1)
        limit = max(options["limit"], 0)
        written = 0
        started = time.monotonic()

        self.stdout.write(f"filling {remaining} rows, {batch} at a time…")

        while True:
            size = batch if not limit else min(batch, limit - written)
            if size <= 0:
                break
            # Its own transaction per batch: committed and released before the
            # next one starts, which is the whole reason this is a loop.
            with connection.cursor() as cursor:
                cursor.execute(FILL_SQL, [size])
                affected = cursor.rowcount
            if not affected:
                break
            written += affected
            elapsed = time.monotonic() - started
            rate = written / elapsed if elapsed else 0.0
            self.stdout.write(f"  {written}/{remaining}  ({rate:.0f} rows/s)")

        left = TenderNotice.objects.filter(search_vector__isnull=True).count()
        self.stdout.write(
            self.style.SUCCESS(
                f"filled {written} rows in {time.monotonic() - started:.1f}s; {left} still null"
            )
        )
        if left:
            self.stdout.write(
                "Run again to continue — RAG_LEXICAL_STORED_VECTOR must stay off until this is 0."
            )
