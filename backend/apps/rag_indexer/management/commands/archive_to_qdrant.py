"""Embed the archive into Qdrant, once, and be safe to run again.

    python manage.py archive_to_qdrant --status         # what is done, no work
    python manage.py archive_to_qdrant --dry-run        # what a run would do
    python manage.py archive_to_qdrant --limit 200      # a bounded first pass
    python manage.py archive_to_qdrant                  # the whole archive
    python manage.py archive_to_qdrant --kind notices   # bodies only
    python manage.py archive_to_qdrant --focus          # the CIS+ country group

This is the once-off migration of a corpus measured in tens of millions of
characters, so the flags that matter are the cautious ones. ``--status``
answers the question without spending anything. ``--dry-run`` walks the same
selection and reports what *would* be embedded, which is the only honest way to
find out how large the bill is before agreeing to it. ``--limit`` exists so the
first contact with the provider is two hundred sources rather than thirty
thousand.

**Running it twice is safe and cheap.** Sources whose fingerprint, embedding
model and pipeline version all match what is recorded are skipped before they
are parsed, and every point id is derived from its source and position — so a
re-run overwrites its own points instead of appending a second copy of the
archive. That property is what makes the interrupted run a non-event: kill it,
start it again, it resumes.

``tqdm`` is optional. It is a progress bar over a job whose length is known, so
a build without it prints a line every few hundred sources instead of failing —
the same rule the parsers and the AI clients in this project follow.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.rag_indexer.models import PIPELINE_VERSION, IndexedSource
from apps.rag_indexer.services import (
    EmbeddingUnavailable,
    IndexingService,
    QdrantUnavailable,
)

#: Sources between two lines of progress when there is no ``tqdm``. Chosen so
#: an hours-long run prints tens of lines rather than thousands.
LOG_EVERY = 200


class Command(BaseCommand):
    help = "Embed mirrored notices and documents into the Qdrant collection."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--status", action="store_true",
            help="Report coverage and the collection, then exit.",
        )
        parser.add_argument(
            "--dry-run", action="store_true",
            help="Walk the selection and report it without embedding anything.",
        )
        parser.add_argument(
            "--limit", type=int, default=0,
            help="Stop after this many sources (0 = the whole archive).",
        )
        parser.add_argument(
            "--kind", choices=["all", "notices", "documents"], default="all",
            help="Which sources to index.",
        )
        parser.add_argument(
            "--focus", action="store_true",
            help="Only the focus country group, rather than the whole mirror.",
        )
        parser.add_argument(
            "--no-progress", action="store_true",
            help="Plain log lines instead of a progress bar.",
        )

    def handle(self, *args, **options):
        service = IndexingService()

        if options["status"]:
            self._print_status(service)
            return

        kinds = _kinds(options["kind"])
        focus_only = options["focus"]
        limit = options["limit"] or None

        if options["dry_run"]:
            self._dry_run(service, kinds=kinds, focus_only=focus_only, limit=limit)
            return

        if not service.embedding.enabled():
            # A CommandError rather than a warning and a zero-length run: an
            # operator who typed this expects the archive to be embedded, and
            # "0 indexed" with an exit code of 0 reads as "already done".
            raise CommandError(
                "Embeddings are disabled. Set RAG_ENABLED=true and provide "
                "GEMINI_API_KEY (or RAG_EMBED_API_KEY)."
            )

        try:
            service.store.ensure_collection()
        except QdrantUnavailable as exc:
            raise CommandError(str(exc)) from exc

        total = self._expected(service, kinds=kinds, focus_only=focus_only, limit=limit)
        self.stdout.write(
            f"Indexing up to {total} sources into "
            f"{service.store.collection} with {service.embedding.model} "
            f"({service.embedding.dimensions}d, pipeline v{PIPELINE_VERSION})…"
        )

        progress = _progress(total, disabled=options["no_progress"], stream=self.stdout)
        last_logged = 0

        def on_progress(_candidate, stats) -> None:
            nonlocal last_logged
            if progress is not None:
                progress.update(1)
                progress.set_postfix(
                    indexed=stats.indexed, skipped=stats.empty, failed=stats.failed
                )
            elif stats.seen - last_logged >= LOG_EVERY:
                last_logged = stats.seen
                self.stdout.write(
                    f"  {stats.seen}/{total} — {stats.indexed} indexed, "
                    f"{stats.chunks} chunks, {stats.failed} failed"
                )

        try:
            stats = service.run(
                kinds=kinds, focus_only=focus_only, limit=limit, on_progress=on_progress
            )
        except (EmbeddingUnavailable, QdrantUnavailable) as exc:
            raise CommandError(str(exc)) from exc
        finally:
            if progress is not None:
                progress.close()

        self._print_run(stats)

    # -- reporting ----------------------------------------------------------
    def _print_status(self, service: IndexingService) -> None:
        counts = service.pending_count(focus_only=False)
        collection = service.store.stats()

        self.stdout.write(self.style.MIGRATE_HEADING("Archive"))
        done = counts["notices_indexed"] + counts["documents_indexed"]
        total = counts["sources_total"] or 1
        self.stdout.write(
            f"  notices      {counts['notices_indexed']:>8} / {counts['notices_total']}"
        )
        self.stdout.write(
            f"  documents    {counts['documents_indexed']:>8} / {counts['documents_total']}"
        )
        self.stdout.write(f"  coverage     {done / total:>8.1%}")
        self.stdout.write(f"  failed       {counts['failed']:>8}")

        self.stdout.write(self.style.MIGRATE_HEADING("Collection"))
        if not collection.connected:
            self.stdout.write(self.style.ERROR(f"  unreachable — {collection.error}"))
            return
        if not collection.exists:
            self.stdout.write(
                self.style.WARNING(f"  {service.store.collection} does not exist yet")
            )
            return
        self.stdout.write(f"  name         {service.store.collection}")
        self.stdout.write(f"  points       {collection.points:>8}")
        self.stdout.write(f"  indexed      {collection.indexed_vectors:>8}")
        self.stdout.write(f"  vector       {collection.vector_size}d {collection.distance}")
        self.stdout.write(f"  status       {collection.status}")

    def _dry_run(self, service: IndexingService, **selection) -> None:
        """Count and measure the selection without calling the provider.

        Characters, not tokens. A tokeniser would be a closer estimate and it
        would also be a second dependency and a second thing to be wrong
        about; characters divided by four is the arithmetic everyone doing this
        does in their head anyway, and the output says so rather than dressing
        it up as a quote.
        """
        sources = 0
        chars = 0
        for candidate in service.candidates(**selection):
            sources += 1
            if candidate.document is not None:
                chars += len(candidate.document.text or "")
            else:
                chars += len(candidate.notice.notice_text_sanitized or "")

        self.stdout.write(f"Would index {sources} sources, ~{chars:,} characters.")
        self.stdout.write(
            f"Rough order of magnitude: ~{chars // 4:,} tokens "
            f"(characters ÷ 4 — not a quote, and not a tokeniser)."
        )

    def _expected(self, service: IndexingService, **selection) -> int:
        """How many sources the run will see, for the progress bar's total.

        This walks the candidate stream once before the real pass walks it
        again, which is a second scan of the mirror. Worth it: without a total
        the bar is a spinner, and the number an operator actually wants at the
        start of a five-hour job is how long it will be.
        """
        return sum(1 for _ in service.candidates(**selection))

    def _print_run(self, stats) -> None:
        self.stdout.write("")
        self.stdout.write(self.style.MIGRATE_HEADING("Run"))
        self.stdout.write(f"  seen         {stats.seen:>8}")
        self.stdout.write(f"  indexed      {stats.indexed:>8}")
        self.stdout.write(f"  chunks       {stats.chunks:>8}")
        self.stdout.write(f"  characters   {stats.chars:>8,}")
        self.stdout.write(f"  no content   {stats.empty:>8}")
        if stats.failed:
            self.stdout.write(self.style.WARNING(f"  failed       {stats.failed:>8}"))
            for message in stats.errors:
                self.stdout.write(f"    {message}")
        else:
            self.stdout.write(self.style.SUCCESS("  failed              0"))


def _kinds(choice: str) -> tuple[str, ...]:
    if choice == "notices":
        return (IndexedSource.Kind.NOTICE,)
    if choice == "documents":
        return (IndexedSource.Kind.DOCUMENT,)
    return (IndexedSource.Kind.NOTICE, IndexedSource.Kind.DOCUMENT)


def _progress(total: int, *, disabled: bool, stream):
    """A tqdm bar, or ``None`` when there cannot be one. Never raises."""
    if disabled or not total:
        return None
    try:
        from tqdm import tqdm  # noqa: PLC0415 - optional, see the module docstring
    except ImportError:
        stream.write("(install tqdm for a progress bar)")
        return None
    return tqdm(total=total, unit="src", dynamic_ncols=True)
