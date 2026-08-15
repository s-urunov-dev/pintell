"""Measure whether the chosen passage actually distinguishes a notice.

    python manage.py eval_representative --sample 40
    python manage.py eval_representative --sample 40 --show

The change this measures replaced a centroid over a notice's first 24 chunks
with the single least-common chunk (`services/similarity.py`). The centroid
version was not wrong so much as uninformative: it put boilerplate at the
centre of every notice, so scores clustered in a band and the passage offered
as the reason a tender matched was a sentence about where to collect the
documents.

**The gold set here is the corpus, not a hand-labelled file.** A list of
"correct" representative passages would be one person's opinion written into a
fixture, and — worse for this codebase — a list of boilerplate phrases typed
from memory is precisely the kind of unsourced fact this codebase refuses to
write (docs/OPEN-QUESTIONS.md). So the
measurement is defined against something checkable instead: **how many other
notices carry a near-duplicate of the passage that was chosen.** A sentence
naming a 220 kV substation is shared with nobody; the paragraph about the
Procurement Regulations is shared with thousands. Lower is better, the number
comes from the archive, and the archive is the authority on what is common in
the archive.

Two figures are reported per strategy:

* **median duplicates** — the typical passage's commonness.
* **boilerplate rate** — the share of notices whose chosen passage still hits
  the duplicate ceiling. This is the honest headline: it is the proportion of
  panels where the reader is shown a sentence that says nothing specific.

The sample is drawn deterministically by primary key order so two runs over an
unchanged archive are comparable, and the command reads the store only — it
embeds nothing and costs nothing.
"""

from __future__ import annotations

import statistics

from django.core.management.base import BaseCommand, CommandError

from apps.rag_indexer.models import IndexedSource
from apps.rag_indexer.services.similarity import (
    CANDIDATE_CHUNKS,
    DUPLICATE_SCAN,
    SimilarityService,
)
from apps.rag_indexer.services.qdrant import QdrantUnavailable, get_qdrant_service


class Command(BaseCommand):
    help = "Measure how distinctive the chosen representative passages are."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--sample", type=int, default=30,
            help="Notices to measure (each costs a few Qdrant searches).",
        )
        parser.add_argument(
            "--show", action="store_true",
            help="Print the chosen passage for each notice.",
        )

    def handle(self, *args, **options):
        sample = max(options["sample"], 1)
        store = get_qdrant_service()
        service = SimilarityService(store=store)

        keys = list(
            IndexedSource.objects.filter(
                kind=IndexedSource.Kind.NOTICE,
                status=IndexedSource.Status.INDEXED,
                chunk_count__gte=3,
            )
            .order_by("source_key")
            .values_list("source_key", flat=True)[:sample]
        )
        if not keys:
            raise CommandError(
                "Nothing indexed to measure — run archive_to_qdrant first."
            )

        self.stdout.write(
            f"Measuring {len(keys)} notices "
            f"(up to {CANDIDATE_CHUNKS} candidates each, ceiling {DUPLICATE_SCAN})…"
        )

        chosen: list[int] = []
        baseline: list[int] = []
        shown = 0

        for key in keys:
            try:
                points = store.points_for_source(key, limit=CANDIDATE_CHUNKS)
            except QdrantUnavailable as exc:
                raise CommandError(str(exc)) from exc
            if not points:
                continue

            # The strategy this replaced, measured the same way: the centroid
            # is not a stored chunk, so it is stood in for by the *first*
            # chunk — which is what a centroid over a mostly-boilerplate
            # document lands nearest to, and what the old panel surfaced.
            first = points[0]
            baseline.append(service._count_duplicates(first["vector"], key))

            best = service.representative(key)
            if best is None:
                continue
            chosen.append(best.duplicates)

            if options["show"] and shown < 12:
                shown += 1
                self.stdout.write(
                    f"  {key}  dup={best.duplicates:>3}  {best.content[:110]}"
                )

        if not chosen:
            raise CommandError("No notice yielded a representative passage.")

        self.stdout.write("")
        self._report("first chunk (previous behaviour)", baseline)
        self._report("least common chunk (current)", chosen)

        improved = sum(1 for a, b in zip(baseline, chosen) if b < a)
        self.stdout.write("")
        self.stdout.write(
            f"  more distinctive on {improved}/{len(chosen)} notices "
            f"({improved / len(chosen):.0%})"
        )

    def _report(self, label: str, values: list[int]) -> None:
        boilerplate = sum(1 for value in values if value >= DUPLICATE_SCAN)
        self.stdout.write(self.style.MIGRATE_HEADING(label))
        self.stdout.write(f"  median duplicates  {statistics.median(values):>6.1f}")
        self.stdout.write(f"  mean duplicates    {statistics.fmean(values):>6.1f}")
        self.stdout.write(
            f"  boilerplate rate   {boilerplate / len(values):>6.0%}  "
            f"({boilerplate}/{len(values)} still at the ceiling)"
        )
