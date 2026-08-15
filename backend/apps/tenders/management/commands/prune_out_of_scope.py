"""Delete everything mirrored for a country outside the focus group.

    python manage.py prune_out_of_scope              # report only
    python manage.py prune_out_of_scope --apply

The mirror was built before the product scope narrowed to one country group,
so most of what it holds is for countries the product will never serve. This
command brings the stored data in line with `settings.FOCUS_COUNTRY_GROUP`.

It is a one-way operation and it is not the mechanism that keeps the scope:
`settings.INGEST_FOCUS_ONLY` is, by refusing out-of-scope notices at write
time. Pruning without that flag on would be undone by the next sync, so the
command says so and stops.

What goes, in dependency order:

* notices outside the group — and with them, by cascade, their parsed contract
  awards and their links to mirrored documents;
* mirrored documents left attached to no notice at all, including the blob on
  disk when no other row shares that content hash;
* project profiles no notice references any more, and their document lists;
* backfill partitions for the departed countries, so the archive walk does not
  simply fetch them again.

Deletes run in bounded batches rather than one statement: a single
``DELETE ... WHERE country NOT IN (...)`` over hundreds of thousands of rows
holds locks for minutes and takes the API down with it.
"""

from __future__ import annotations

from pathlib import Path

from django.conf import settings
from django.core.management.base import BaseCommand
from django.db import transaction

from apps.tenders.models import (
    BackfillPartition,
    HarvestedDocument,
    ProjectProfile,
    TenderNotice,
)
from apps.tenders.regions import canonical_country, group_countries

BATCH_SIZE = 2_000


class Command(BaseCommand):
    help = "Delete mirrored data for countries outside the focus country group."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--apply", action="store_true",
            help="Actually delete. Without it the command only reports.",
        )
        parser.add_argument(
            "--keep-blobs", action="store_true",
            help="Leave harvested files on disk even when their row is deleted.",
        )

    def handle(self, *args, **options):
        allowed = group_countries(settings.FOCUS_COUNTRY_GROUP)
        if not allowed:
            self.stderr.write(
                self.style.ERROR(
                    f"Unknown country group {settings.FOCUS_COUNTRY_GROUP!r} — "
                    "refusing to run: an empty group would delete everything."
                )
            )
            return

        apply = options["apply"]

        self.stdout.write(f"Focus group : {settings.FOCUS_COUNTRY_GROUP}")
        self.stdout.write(f"Keeping     : {', '.join(allowed)}")
        self.stdout.write(
            f"Ingest gate : INGEST_FOCUS_ONLY="
            f"{getattr(settings, 'INGEST_FOCUS_ONLY', False)}"
        )
        self.stdout.write("")

        if apply and not getattr(settings, "INGEST_FOCUS_ONLY", False):
            self.stderr.write(
                self.style.ERROR(
                    "INGEST_FOCUS_ONLY is off — the next sync would restore what "
                    "this deletes. Turn it on first, or run without --apply."
                )
            )
            return

        # Aliases are resolved here rather than in the query so a notice stored
        # under an alternative upstream spelling ("Kyrgyzstan") is not deleted
        # as out-of-scope while its canonical twin is kept.
        stored = set(
            TenderNotice.objects.exclude(country="")
            .values_list("country", flat=True)
            .distinct()
        )
        doomed_countries = sorted(
            name for name in stored if canonical_country(name) not in set(allowed)
        )

        notices = TenderNotice.objects.filter(country__in=doomed_countries)
        notice_count = notices.count()
        self.stdout.write(
            f"Countries to drop : {len(doomed_countries)}"
        )
        self.stdout.write(f"Notices to delete : {notice_count:,}")

        if not apply:
            self.stdout.write("")
            self.stdout.write(
                self.style.WARNING("Dry run — nothing deleted. Re-run with --apply.")
            )
            return

        deleted = self._delete_notices(doomed_countries)
        self.stdout.write(f"  notices deleted     : {deleted:,}")

        documents, blobs = self._delete_orphan_documents(
            remove_blobs=not options["keep_blobs"]
        )
        self.stdout.write(f"  orphan documents    : {documents:,} ({blobs:,} files removed)")

        projects = self._delete_orphan_projects()
        self.stdout.write(f"  orphan projects     : {projects:,}")

        partitions = BackfillPartition.objects.filter(
            key__in=[f"country:{name}" for name in doomed_countries]
        ).delete()[0]
        self.stdout.write(f"  backfill partitions : {partitions:,}")

        self.stdout.write("")
        self.stdout.write(self.style.SUCCESS("Prune complete."))
        self.stdout.write(
            "Run VACUUM ANALYZE on the database to return the space to the "
            "filesystem — PostgreSQL will not do it on its own after a delete "
            "this large."
        )

    # -- steps --------------------------------------------------------------
    def _delete_notices(self, countries: list[str]) -> int:
        """Delete in id batches, letting cascades take awards and doc links."""
        total = 0
        while True:
            batch = list(
                TenderNotice.objects.filter(country__in=countries).values_list(
                    "pk", flat=True
                )[:BATCH_SIZE]
            )
            if not batch:
                return total
            with transaction.atomic():
                TenderNotice.objects.filter(pk__in=batch).delete()
            total += len(batch)
            self.stdout.write(f"    … {total:,} deleted", ending="\r")
            self.stdout.flush()

    def _delete_orphan_documents(self, *, remove_blobs: bool) -> tuple[int, int]:
        """Drop documents no surviving notice points at.

        The blob is only unlinked when no other row shares its hash: storage is
        content-addressed, so two documents can legitimately be the same file.
        """
        orphans = HarvestedDocument.objects.filter(notices__isnull=True)
        paths: list[tuple[str, str]] = list(
            orphans.exclude(stored_path="").values_list("sha256", "stored_path")
        )
        count = orphans.count()
        orphans.delete()

        removed = 0
        if remove_blobs:
            for digest, path in paths:
                if digest and HarvestedDocument.objects.filter(sha256=digest).exists():
                    continue
                try:
                    Path(path).unlink(missing_ok=True)
                    removed += 1
                except OSError as exc:  # noqa: PERF203 - one bad file is not fatal
                    self.stderr.write(f"    could not remove {path}: {exc}")
        return count, removed

    def _delete_orphan_projects(self) -> int:
        """Drop project profiles nothing references any more.

        Matched on the ``project_id`` column rather than the ``project_ref``
        foreign key: a notice keeps its project id as text even when the
        profile was never mirrored, and that text is what makes a profile still
        wanted.
        """
        referenced = set(
            TenderNotice.objects.exclude(project_id="")
            .values_list("project_id", flat=True)
            .distinct()
        )
        orphans = ProjectProfile.objects.exclude(project_id__in=referenced)
        # delete() counts cascaded rows too; report the profiles themselves.
        _, per_model = orphans.delete()
        return per_model.get("tenders.ProjectProfile", 0)
