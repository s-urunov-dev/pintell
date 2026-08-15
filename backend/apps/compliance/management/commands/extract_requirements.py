"""Run the extraction stack by hand.

    python manage.py extract_requirements --status              # corpus report
    python manage.py extract_requirements --limit 25            # one L1 batch
    python manage.py extract_requirements --notice OP00456288   # one notice
    python manage.py extract_requirements --layers L1,L2 --force --notice OP00456288

``--status`` is the one to reach for first. It answers, over the whole corpus
rather than over whatever was last run by hand, how much of it has been read at
all, how many requirements each layer contributed, what share of their quotes
were found in the source, and what the reading has cost so far. Those are the
columns of the DECISIONS.md D6 ablation table.

Everything else is metered except the default. ``--layers L1`` needs no API key
and no network; anything deeper spends money per notice, so nothing here runs a
paid layer unless it was named.
"""

from __future__ import annotations

from django.core.management.base import BaseCommand, CommandError

from apps.compliance import pipeline
from apps.compliance.models import ExtractionRun, TenderRequirement
from apps.tenders.models import TenderNotice


class Command(BaseCommand):
    help = "Extract qualification requirements from notices, layer by layer."

    def add_arguments(self, parser) -> None:
        parser.add_argument(
            "--limit", type=int, default=0,
            help=f"Notices to process (default {pipeline.DEFAULT_BATCH_SIZE} with --all).",
        )
        parser.add_argument(
            "--notice", type=str, default="",
            help="Extract one notice by its id, whatever its run history.",
        )
        parser.add_argument(
            "--layers", type=str, default=",".join(pipeline.DEFAULT_LAYERS),
            help="Comma-separated layer set, cheapest first (L1,L2,L3).",
        )
        parser.add_argument(
            "--force", action="store_true",
            help="Re-run a notice that already has a run for this layer set.",
        )
        parser.add_argument("--status", action="store_true", help="Print the corpus report.")
        parser.add_argument(
            "--sample", type=int, default=0,
            help="Show N recently extracted requirements with their grounding.",
        )

    def handle(self, *args, **options):
        if options["status"]:
            self._print_status()
            return
        if options["sample"]:
            self._print_sample(options["sample"])
            return

        try:
            layers = pipeline.normalise_layers(options["layers"])
        except pipeline.UnknownLayer as exc:
            # A typo here would quietly produce a run labelled with a different
            # layer set, which is worse than refusing: the ablation groups on
            # that label.
            raise CommandError(str(exc)) from exc

        if options["notice"]:
            self._extract_one(options["notice"], layers, force=options["force"])
        else:
            limit = options["limit"] or pipeline.DEFAULT_BATCH_SIZE
            self.stdout.write(
                f"Extracting {','.join(layers)} from up to {limit} notices with no run yet…"
            )
            stats = pipeline.extract_pending(
                limit=limit, layers=layers, force=options["force"]
            )
            self._print_stats(stats)

        self.stdout.write("")
        self._print_status()

    # -- actions ------------------------------------------------------------
    def _extract_one(self, notice_id: str, layers, *, force: bool) -> None:
        try:
            notice = TenderNotice.objects.get(pk=notice_id)
        except TenderNotice.DoesNotExist as exc:
            raise CommandError(f"no notice {notice_id!r}") from exc

        self.stdout.write(f"Extracting {','.join(layers)} from {notice_id}…")
        # `--notice` deliberately does *not* imply `--force`. Naming one notice
        # is often how a metered layer gets tried, and an implicit re-run would
        # spend money the operator did not ask to spend twice.
        stats = pipeline.extract_one(notice, layers=layers, force=force)
        if stats.skipped:
            self.stdout.write(self.style.WARNING(
                "  already has a run for this layer set — pass --force to re-run."
            ))
            return

        self._print_stats(stats)
        # ``ExtractionRun`` orders newest first, so this is the run just written.
        run = ExtractionRun.objects.filter(notice=notice).first()
        if run is not None and run.error:
            self.stdout.write(self.style.WARNING(f"  {run.error[:300]}"))

        for row in TenderRequirement.objects.filter(run=run).order_by("layer", "key"):
            flag = "" if row.is_usable else "  <- quote not found, withheld"
            self.stdout.write(
                f"  [{row.layer}] {row.key:<28} {row.grounding:<10}{flag}"
            )

    # -- output -------------------------------------------------------------
    def _print_stats(self, stats: pipeline.PipelineStats) -> None:
        self.stdout.write(
            f"  notices={stats.notices} runs={stats.runs} skipped={stats.skipped} "
            f"failed={stats.failed}"
        )
        self.stdout.write(
            f"  requirements={stats.requirements} verified={stats.verified} "
            f"not_found={stats.not_found}"
        )
        self.stdout.write(
            f"  dropped: unparseable={stats.unparseable} "
            f"already_found={stats.superseded} layers_unavailable={stats.unavailable}"
        )
        if stats.input_tokens or stats.output_tokens or stats.cost_usd:
            self.stdout.write(
                f"  tokens in/out={stats.input_tokens:,}/{stats.output_tokens:,} "
                f"cost=${stats.cost_usd}"
            )
        for message in stats.errors[:5]:
            self.stdout.write(self.style.WARNING(f"  - {message}"))

    def _print_status(self) -> None:
        report = pipeline.corpus_report()

        self.stdout.write("Extraction coverage")
        self.stdout.write(f"  notices mirrored    : {report['notices_total']:,}")
        self.stdout.write(
            f"  in the focus scope  : {report['notices_in_scope']:,}"
            "   <- what the batch walks"
        )
        self.stdout.write(f"  read at least once  : {report['notices_with_run']:,}")
        self.stdout.write(
            f"  yielded ≥1 criterion: {report['notices_with_requirements']:,}"
        )
        coverage = report["coverage_rate"]
        self.stdout.write(
            f"  coverage rate       : {coverage:.1%} of the focus scope"
            if coverage is not None
            else "  coverage rate       : (nothing in scope)"
        )
        # A low yield is not a fault: most notices genuinely state no criteria,
        # and L1 alone should land near 23% (l1.py). This is the number the
        # deeper layers exist to move — and a suspiciously high one means the
        # rules are matching what they should not.
        yield_rate = report["yield_rate"]
        self.stdout.write(
            f"  yield rate          : {yield_rate:.1%} of the notices read"
            if yield_rate is not None
            else "  yield rate          : (nothing read yet)"
        )

        self.stdout.write("")
        self.stdout.write("Requirements by layer")
        labels = dict(TenderRequirement.Layer.choices)
        by_layer = report["by_layer"]
        if not by_layer:
            self.stdout.write("  (none extracted yet)")
        for layer in pipeline.LAYER_ORDER:
            if layer in by_layer:
                self.stdout.write(
                    f"  {layer} {labels.get(layer, ''):<38}: {by_layer[layer]:,}"
                )

        self.stdout.write("")
        self.stdout.write("Grounding — quotes found in the source they cite")
        ground = report["grounding"]
        self.stdout.write(f"  verified            : {ground['verified']:,}")
        self.stdout.write(
            f"  not found (withheld): {ground['not_found']:,}"
            "   <- the hallucination signal"
        )
        self.stdout.write(f"  unchecked           : {ground['unchecked']:,}")
        self.stdout.write(
            f"  grounding rate      : {ground['rate']:.1%} of {ground['checked']:,} checked"
            if ground["rate"] is not None
            else "  grounding rate      : (nothing checked yet)"
        )

        self.stdout.write("")
        self.stdout.write("Runs")
        self.stdout.write(f"  total               : {report['runs']:,}")
        self.stdout.write(f"  failed              : {report['failed_runs']:,}")
        for layer_set, row in sorted(
            report["by_layer_set"].items(), key=lambda kv: -kv[1]["runs"]
        ):
            self.stdout.write(
                f"  {layer_set or '(none)':<20}: {row['runs']:,} runs, ${row['cost']}"
            )
        self.stdout.write(
            f"  tokens in/out       : {report['input_tokens']:,}/"
            f"{report['output_tokens']:,}"
        )
        self.stdout.write(f"  spent so far        : ${report['cost_usd']}")

    def _print_sample(self, limit: int) -> None:
        rows = (
            TenderRequirement.objects.select_related("run")
            .order_by("-created_at")[:limit]
        )
        if not rows:
            self.stdout.write("Nothing extracted yet — run without --status first.")
            return
        for row in rows:
            self.stdout.write(
                f"[{row.layer}] {row.grounding:<10} {row.notice_id:<14} {row.key}"
            )
            if row.evidence_quote:
                self.stdout.write(f"        “{row.evidence_quote[:140]}”")
