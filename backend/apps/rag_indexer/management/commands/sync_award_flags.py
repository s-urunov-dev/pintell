"""Stamp ``is_award`` onto points that were indexed before the field existed.

    python manage.py sync_award_flags --status
    python manage.py sync_award_flags

The archive was embedded without this flag, and re-running the import to
introduce it would mean paying for 74,000 embeddings to write one boolean.
Qdrant sets payload by filter, so the same change is a few hundred requests
over vectors that never move: seconds, and no metered call.

**Why the flag has to exist at all.** An award notice and a request for
expressions of interest are the same subject written in two different genres —
one is prose about the work, the other a table of bid prices and company names.
Embedding similarity is dominated by that difference, so a search from an open
consulting tender returns other open consulting tenders and no contracts.
Measured on `OP00460945`: 426 nearest neighbours, **all** of them opportunity
notices, from an archive holding 13,255 awards. The similar-awards panel was
empty on 13 of the 21 open tenders because of it.

Filtering inside the store fixes that — but only if the store knows which
points belong to an award, which is what this writes.

Run it after any archive import, and after a reparse that gives awards a winner
they did not have (`PARSER_VERSION`, D42a): a notice becomes an award in the
index when `ContractAward` gains a name for it, which is a Postgres event the
collection cannot see.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.rag_indexer.services import QdrantUnavailable, get_qdrant_service
from apps.tenders.models import ContractAward, TenderNotice


class Command(BaseCommand):
    help = "Mark the indexed points of contract awards, without re-embedding."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--status", action="store_true",
            help="Report what is flagged and what would change, then exit.",
        )

    def handle(self, *args, **options):
        store = get_qdrant_service()

        # "Award" means the same thing here as it does in the panel: a parsed
        # contract with a name on it. An anonymised individual consultant is
        # not a company and never reaches `supplier_name` (D42a), so it is not
        # something to offer as "who won work like this".
        award_ids = list(
            ContractAward.objects.exclude(supplier_name="").values_list(
                "notice_id", flat=True
            )
        )
        other_ids = list(
            TenderNotice.objects.exclude(notice_id__in=award_ids).values_list(
                "notice_id", flat=True
            )
        )

        try:
            stats = store.stats()
            if not stats.connected:
                raise CommandError(f"Qdrant is unreachable: {stats.error}")
            if not stats.exists:
                raise CommandError(
                    f"Collection {store.collection} does not exist — run "
                    "archive_to_qdrant first."
                )

            self.stdout.write(
                f"{len(award_ids)} awards with a named winner, "
                f"{len(other_ids)} other notices, "
                f"{stats.points} points in {store.collection}."
            )
            if options["status"]:
                return

            # The collection has to know the field before it can be filtered
            # on; `ensure_collection` adds the index and is idempotent.
            store.ensure_collection()

            self.stdout.write("Marking award points…")
            marked = store.stamp_payload(award_ids, {"is_award": True})

            # The rest are stamped `False` rather than left absent. A missing
            # key and `false` filter identically today, but a point with no
            # `is_award` is indistinguishable from one this command has never
            # seen — and that is the difference between "not an award" and
            # "unknown", which the next person debugging an empty panel will
            # want.
            self.stdout.write("Marking the rest…")
            cleared = store.stamp_payload(other_ids, {"is_award": False})
        except QdrantUnavailable as exc:
            raise CommandError(str(exc)) from exc

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS(f"  awards      {marked:>8}"))
        self.stdout.write(f"  others      {cleared:>8}")
        self.stdout.write(
            "  (payload only — no vector was recomputed and nothing was billed)"
        )
