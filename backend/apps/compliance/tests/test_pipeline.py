"""The stack runs cheapest first, drops what it cannot evaluate, and degrades.

Nothing here reaches a model. L2 and L3 are installed as scripted modules for
the duration of a test — the same lazy import the pipeline uses in production,
so the "layer is not installed" path is exercised for real rather than mocked
around.
"""

from __future__ import annotations

import io
import sys
from contextlib import contextmanager
from datetime import timedelta
from decimal import Decimal
from types import ModuleType

from django.core.management import call_command
from django.test import TestCase
from django.utils import timezone

import apps.compliance as compliance_package
from apps.compliance import grounding, pipeline
from apps.compliance.extraction import Extracted, LayerResult
from apps.compliance.models import ExtractionRun, TenderRequirement
from apps.tenders.models import HarvestedDocument, TenderNotice

#: Two clauses of a real Invitation for Bids, in the form the column holds them.
#: L1 reads a turnover figure and a bid security out of this and nothing else.
NOTICE_BODY = (
    "<p>Minimum average annual turnover of USD 28,000,000.00 (twenty-eight "
    "million United States Dollars), calculated as total certified payments "
    "received for contracts in progress and/or completed within past three "
    "years (2023-2025), divided by three.</p>"
    "<p>All Bids must be accompanied by a Bid Security of USD 280,000.00 (Two "
    "hundred and eighty thousand United States Dollars).</p>"
)

TURNOVER_TREE = {"kind": "scalar", "key": "annual_turnover_avg", "op": ">=", "value": 1}
EXPERTS_TREE = {"kind": "count", "entity": "experts", "op": ">=", "value": 3}

_ABSENT = object()


@contextmanager
def _layer(name: str, module: ModuleType | None):
    """Install a layer module for one test — or ``None`` to make it absent.

    ``None`` in ``sys.modules`` is what makes ``from . import l2`` raise, which
    is the state a deployment without the SDK is actually in. The package
    attribute is removed alongside it: once a submodule has been imported it is
    bound on the package, and ``from . import`` would then find it there and
    never consult ``sys.modules`` at all — leaving the test passing for the
    wrong reason, or not at all depending on test order.
    """
    dotted = f"apps.compliance.{name}"
    previous_module = sys.modules.get(dotted, _ABSENT)
    previous_attr = getattr(compliance_package, name, _ABSENT)

    sys.modules[dotted] = module
    if module is None:
        if previous_attr is not _ABSENT:
            delattr(compliance_package, name)
    else:
        setattr(compliance_package, name, module)
    try:
        yield module
    finally:
        if previous_module is _ABSENT:
            sys.modules.pop(dotted, None)
        else:
            sys.modules[dotted] = previous_module
        if previous_attr is _ABSENT:
            if hasattr(compliance_package, name):
                delattr(compliance_package, name)
        else:
            setattr(compliance_package, name, previous_attr)


class ScriptedLayer:
    """A layer that answers from a script and records what it was asked."""

    def __init__(self, *results, raises: Exception | None = None):
        self._results = list(results)
        self._raises = raises
        self.calls: list[dict] = []

    def __call__(
        self,
        subject,
        *,
        reference_year=None,
        exclude_keys=(),
        config=None,
        client=None,
        role_slugs=None,
    ):
        self.calls.append(
            {
                "subject": subject,
                "reference_year": reference_year,
                "exclude_keys": set(exclude_keys),
                "config": config,
                "role_slugs": role_slugs,
            }
        )
        if self._raises is not None:
            raise self._raises
        return self._results.pop(0) if self._results else LayerResult()

    def as_module(self, name: str) -> ModuleType:
        module = ModuleType(f"apps.compliance.{name}")
        module.extract = self
        return module


def scripted(name: str, *results, raises: Exception | None = None):
    layer = ScriptedLayer(*results, raises=raises)
    return layer, _layer(name, layer.as_module(name))


class PipelineTestCase(TestCase):
    def setUp(self) -> None:
        self.notice = TenderNotice.objects.create(
            notice_id="OP00456288",
            notice_type="Invitation for Bids",
            country="Uzbekistan",
            bid_description="Modernisation of four 220 kV substations",
            notice_text_sanitized=NOTICE_BODY,
            notice_date=timezone.now().date(),
            deadline_date=timezone.now() + timedelta(days=30),
        )

    def keys(self, run=None) -> set[str]:
        rows = TenderRequirement.objects.filter(notice=self.notice)
        if run is not None:
            rows = rows.filter(run=run)
        return set(rows.values_list("key", flat=True))


class LayerSetTests(TestCase):
    def test_a_requested_layer_set_is_ordered_cheapest_first(self):
        """Honouring the typed order would bill L2 for what L1 finds free."""
        self.assertEqual(pipeline.normalise_layers("L2,L1"), ("L1", "L2"))
        self.assertEqual(pipeline.normalise_layers(["l3", "L1"]), ("L1", "L3"))

    def test_an_unknown_layer_is_refused_rather_than_ignored(self):
        """A mislabelled run would silently corrupt the ablation grouping."""
        with self.assertRaises(pipeline.UnknownLayer):
            pipeline.normalise_layers("L1,L9")


class L1EndToEndTests(PipelineTestCase):
    def test_what_l1_reads_is_stored_with_its_quotes_verified(self):
        run = pipeline.extract_for_notice(self.notice)

        self.assertEqual(run.layers, "L1")
        self.assertEqual(run.status, ExtractionRun.Status.OK)
        self.assertEqual(self.keys(), {"annual_turnover_avg", "bid_security"})
        self.assertTrue(
            all(
                row.grounding == grounding.VERIFIED
                for row in TenderRequirement.objects.filter(notice=self.notice)
            ),
            "L1 copies its quotes out of the string it matched — none may miss",
        )

    def test_a_deterministic_layer_records_no_model_and_no_cost(self):
        """The point of putting L1 first: an answer with no API key at all."""
        run = pipeline.extract_for_notice(self.notice)
        self.assertEqual(run.model, "")
        self.assertEqual(run.cost_usd, Decimal("0"))
        self.assertEqual(run.input_tokens, 0)

    def test_the_time_window_is_read_against_the_notices_own_year(self):
        """A 2019 notice evaluated against today would exclude what qualified."""
        self.notice.notice_date = self.notice.notice_date.replace(year=2019)
        self.notice.save(update_fields=["notice_date"])
        pipeline.extract_for_notice(self.notice)
        self.assertTrue(TenderRequirement.objects.filter(notice=self.notice).exists())

    def test_a_notice_stating_no_criteria_completes_rather_than_fails(self):
        """Three notices in four say nothing; that is an answer, not a failure.

        Marking them failed would misreport the corpus and make the batch retry
        a settled result forever.
        """
        quiet = TenderNotice.objects.create(
            notice_id="OP00000001",
            notice_text_sanitized="<p>Expressions of interest are invited.</p>",
        )
        run = pipeline.extract_for_notice(quiet)
        self.assertEqual(run.status, ExtractionRun.Status.OK)
        self.assertEqual(run.error, "")
        self.assertEqual(TenderRequirement.objects.filter(notice=quiet).count(), 0)


class LayeringTests(PipelineTestCase):
    def test_a_deeper_layer_is_only_asked_for_what_is_still_missing(self):
        """The whole economic argument for the stack, in one assertion."""
        layer, installed = scripted("l2", LayerResult())
        with installed:
            pipeline.extract_for_notice(self.notice, layers="L1,L2")

        self.assertEqual(len(layer.calls), 1)
        self.assertEqual(
            layer.calls[0]["exclude_keys"], {"annual_turnover_avg", "bid_security"}
        )

    def test_a_later_layer_does_not_overwrite_what_an_earlier_one_found(self):
        """Depth breaks ties downwards: the free, rule-read answer is kept."""
        layer, installed = scripted(
            "l2",
            LayerResult(
                requirements=[
                    Extracted(
                        key="annual_turnover_avg",
                        expression=dict(TURNOVER_TREE, value=999),
                        evidence_quote="Minimum average annual turnover",
                        source="notice_body",
                    )
                ]
            ),
        )
        with installed:
            run = pipeline.extract_for_notice(self.notice, layers="L1,L2")

        rows = TenderRequirement.objects.filter(
            notice=self.notice, key="annual_turnover_avg"
        )
        self.assertEqual(rows.count(), 1)
        self.assertEqual(rows.first().layer, TenderRequirement.Layer.L1)
        self.assertEqual(rows.first().expression["value"], 28_000_000.0)
        self.assertEqual(run.layers, "L1,L2")

    def test_a_deeper_layer_fills_the_gaps_it_was_asked_about(self):
        layer, installed = scripted(
            "l2",
            LayerResult(
                requirements=[
                    Extracted(
                        key="key_experts_count",
                        expression=EXPERTS_TREE,
                        label="Key experts",
                        evidence_quote="All Bids must be accompanied by a Bid Security",
                        source="notice_body",
                    )
                ],
                model="claude-haiku-4-5",
            ),
        )
        with installed:
            pipeline.extract_for_notice(self.notice, layers="L1,L2")

        row = TenderRequirement.objects.get(notice=self.notice, key="key_experts_count")
        self.assertEqual(row.layer, TenderRequirement.Layer.L2)
        self.assertEqual(row.grounding, grounding.VERIFIED)

    def test_the_run_records_the_layer_set_model_tokens_and_cost(self):
        """Every column the D6 ablation table reads, written at the moment spent."""
        _, installed = scripted(
            "l2",
            LayerResult(
                model="claude-haiku-4-5",
                prompt_version="l2-v3",
                input_tokens=4200,
                output_tokens=380,
                cost_usd=Decimal("0.001240"),
                duration_ms=1500,
            ),
        )
        with installed:
            run = pipeline.extract_for_notice(self.notice, layers="L1,L2")

        self.assertEqual(run.layers, "L1,L2")
        self.assertEqual(run.model, "claude-haiku-4-5")
        self.assertEqual(run.prompt_version, "l2-v3")
        self.assertEqual(run.input_tokens, 4200)
        self.assertEqual(run.output_tokens, 380)
        self.assertEqual(run.cost_usd, Decimal("0.001240"))
        self.assertGreaterEqual(run.duration_ms, 1500)


class BoundaryRejectionTests(PipelineTestCase):
    def test_an_expression_the_engine_cannot_evaluate_is_dropped_and_counted(self):
        """A tree the engine refuses is a malformed extraction, not a crash."""
        _, installed = scripted(
            "l2",
            LayerResult(
                requirements=[
                    Extracted(
                        key="broken",
                        # An unknown comparator: `parse_requirement` refuses it.
                        expression={"kind": "scalar", "key": "x", "op": "≥", "value": 1},
                        evidence_quote="Minimum average annual turnover",
                    ),
                    Extracted(
                        key="also_broken",
                        expression={"kind": "all", "children": []},
                        evidence_quote="Minimum average annual turnover",
                    ),
                ]
            ),
        )
        with installed:
            stats = pipeline.extract_one(self.notice, layers="L1,L2")

        self.assertEqual(stats.unparseable, 2)
        self.assertEqual(self.keys(), {"annual_turnover_avg", "bid_security"})

    def test_a_key_too_long_for_the_column_is_malformed_rather_than_truncated(self):
        """A shortened key is a different taxonomy key, silently invented."""
        _, installed = scripted(
            "l2",
            LayerResult(
                requirements=[
                    Extracted(
                        key="x" * 200,
                        expression=EXPERTS_TREE,
                        evidence_quote="Minimum average annual turnover",
                    )
                ]
            ),
        )
        with installed:
            stats = pipeline.extract_one(self.notice, layers="L1,L2")

        self.assertEqual(stats.unparseable, 1)
        self.assertEqual(self.keys(), {"annual_turnover_avg", "bid_security"})

    def test_an_ungrounded_requirement_is_stored_and_withheld(self):
        """Deleting it would destroy the hallucination rate it exists to measure."""
        _, installed = scripted(
            "l2",
            LayerResult(
                requirements=[
                    Extracted(
                        key="liquid_assets",
                        expression={
                            "kind": "scalar", "key": "liquid_assets",
                            "op": ">=", "value": 5_600_000,
                        },
                        evidence_quote="bidders need about 5.6 million in liquid assets",
                        source="notice_body",
                    )
                ]
            ),
        )
        with installed:
            stats = pipeline.extract_one(self.notice, layers="L1,L2")

        row = TenderRequirement.objects.get(notice=self.notice, key="liquid_assets")
        self.assertEqual(row.grounding, grounding.NOT_FOUND)
        self.assertFalse(row.is_usable)
        self.assertEqual(stats.not_found, 1)


class DegradationTests(PipelineTestCase):
    def test_a_failing_layer_leaves_the_earlier_layers_work_in_place(self):
        _, installed = scripted("l2", LayerResult(error="no API key configured"))
        with installed:
            run = pipeline.extract_for_notice(self.notice, layers="L1,L2")

        self.assertEqual(run.status, ExtractionRun.Status.OK)
        self.assertIn("no API key configured", run.error)
        self.assertEqual(self.keys(), {"annual_turnover_avg", "bid_security"})

    def test_a_layer_that_raises_is_recorded_rather_than_propagated(self):
        """The contract says layers return errors; the pipeline does not rely on it."""
        _, installed = scripted("l2", raises=RuntimeError("connection reset"))
        with installed:
            run = pipeline.extract_for_notice(self.notice, layers="L1,L2")

        self.assertEqual(run.status, ExtractionRun.Status.OK)
        self.assertIn("connection reset", run.error)
        self.assertEqual(self.keys(), {"annual_turnover_avg", "bid_security"})

    def test_a_layer_that_is_not_installed_is_a_recorded_state(self):
        """A deployment without the SDK still gets everything L1 can read."""
        with _layer("l2", None):
            run = pipeline.extract_for_notice(self.notice, layers="L1,L2")

        self.assertEqual(run.layers, "L1", "only the layers that ran are recorded")
        self.assertIn("not available", run.error)
        self.assertEqual(self.keys(), {"annual_turnover_avg", "bid_security"})

    def test_a_pass_where_no_layer_could_run_fails_and_says_which_was_asked_for(self):
        """An empty ``layers`` column would hide the run from every ablation group."""
        with _layer("l3", None):
            run = pipeline.extract_for_notice(self.notice, layers="L3")

        self.assertEqual(run.status, ExtractionRun.Status.FAILED)
        self.assertEqual(run.layers, "L3")
        self.assertEqual(self.keys(), set())

    def test_one_bad_notice_costs_one_notice(self):
        stats = pipeline.extract_one(object(), layers="L1")
        self.assertEqual(stats.runs, 0)
        self.assertTrue(stats.errors)


class RerunTests(PipelineTestCase):
    def test_a_notice_already_read_with_this_layer_set_is_skipped(self):
        first = pipeline.extract_for_notice(self.notice)
        second = pipeline.extract_for_notice(self.notice)

        self.assertIsNone(second)
        self.assertEqual(ExtractionRun.objects.filter(notice=self.notice).count(), 1)
        self.assertEqual(TenderRequirement.objects.filter(run=first).count(), 2)

    def test_a_deeper_layer_set_is_not_blocked_by_a_shallower_run(self):
        pipeline.extract_for_notice(self.notice, layers="L1")
        _, installed = scripted("l2", LayerResult())
        with installed:
            run = pipeline.extract_for_notice(self.notice, layers="L1,L2")
        self.assertIsNotNone(run)

    def test_installing_a_missing_layer_unblocks_the_same_request(self):
        """The run records what ran, so the skip check cannot lock L2 out.

        A pass that asked for L1,L2 while L2 was absent recorded ``L1``; asking
        again once it is installed has to run, not report the notice as done.
        """
        with _layer("l2", None):
            pipeline.extract_for_notice(self.notice, layers="L1,L2")
        _, installed = scripted("l2", LayerResult())
        with installed:
            self.assertIsNotNone(pipeline.extract_for_notice(self.notice, layers="L1,L2"))

    def test_re_running_writes_a_new_run_and_leaves_the_old_one_alone(self):
        first = pipeline.extract_for_notice(self.notice)
        first_rows = set(
            TenderRequirement.objects.filter(run=first).values_list("id", flat=True)
        )

        second = pipeline.extract_for_notice(self.notice, force=True)

        self.assertNotEqual(first.pk, second.pk)
        first.refresh_from_db()
        self.assertEqual(first.status, ExtractionRun.Status.OK)
        self.assertEqual(
            set(TenderRequirement.objects.filter(run=first).values_list("id", flat=True)),
            first_rows,
            "an earlier run's rows are the ablation's evidence and are immutable",
        )
        self.assertEqual(TenderRequirement.objects.filter(notice=self.notice).count(), 4)


class DocumentLayerTests(PipelineTestCase):
    """L3 grounds against the document it read, not against the notice."""

    def setUp(self) -> None:
        super().setUp()
        self.document = HarvestedDocument.objects.create(
            url_hash="a" * 64,
            url="https://example.org/section-iii.pdf",
            kind=HarvestedDocument.Kind.BIDDING,
            status=HarvestedDocument.Status.FETCHED,
            text=(
                "Section III. Evaluation and Qualification Criteria. "
                "The Bidder shall have a minimum of three (3) key experts. "
                + "Filler describing the works. " * 20
            ),
            text_chars=800,
            has_text_layer=True,
        )
        self.document.notices.add(self.notice)

    def test_a_quote_from_a_mirrored_document_verifies_against_that_document(self):
        _, installed = scripted(
            "l3",
            LayerResult(
                requirements=[
                    Extracted(
                        key="key_experts_count",
                        expression=EXPERTS_TREE,
                        evidence_quote="The Bidder shall have a minimum of three (3) key experts.",
                        source="page 42",
                    )
                ],
                model="claude-sonnet-4-5",
            ),
        )
        with installed:
            pipeline.extract_for_notice(self.notice, layers="L1,L3")

        row = TenderRequirement.objects.get(key="key_experts_count")
        self.assertEqual(row.grounding, grounding.VERIFIED)
        self.assertEqual(row.source_document_id, self.document.pk)

    def test_a_quote_in_neither_the_document_nor_the_notice_is_withheld(self):
        _, installed = scripted(
            "l3",
            LayerResult(
                requirements=[
                    Extracted(
                        key="key_experts_count",
                        expression=EXPERTS_TREE,
                        evidence_quote="The Bidder shall employ at least eight engineers.",
                        source="page 42",
                    )
                ]
            ),
        )
        with installed:
            pipeline.extract_for_notice(self.notice, layers="L3")

        self.assertEqual(
            TenderRequirement.objects.get(key="key_experts_count").grounding,
            grounding.NOT_FOUND,
        )

    def test_a_notice_with_no_readable_document_records_an_empty_l3_pass(self):
        self.document.notices.clear()
        _, installed = scripted("l3", LayerResult())
        with installed:
            run = pipeline.extract_for_notice(self.notice, layers="L3")

        self.assertEqual(run.layers, "L3")
        self.assertEqual(run.status, ExtractionRun.Status.OK)


class BatchTests(PipelineTestCase):
    def test_the_batch_reads_notices_nobody_has_read_yet(self):
        other = TenderNotice.objects.create(
            notice_id="OP00456289",
            notice_type="Invitation for Bids",
            country="Uzbekistan",
            notice_text_sanitized=NOTICE_BODY,
            notice_date=timezone.now().date(),
            deadline_date=timezone.now() + timedelta(days=10),
        )
        pipeline.extract_for_notice(self.notice)

        stats = pipeline.extract_pending(limit=10)

        self.assertEqual(stats.notices, 1)
        self.assertEqual(stats.runs, 1)
        self.assertEqual(stats.requirements, 2)
        self.assertEqual(ExtractionRun.objects.filter(notice=other).count(), 1)

    def test_a_notice_with_no_body_is_not_read(self):
        TenderNotice.objects.create(
            notice_id="OP00456290",
            notice_type="Invitation for Bids",
            country="Uzbekistan",
            notice_text_sanitized="",
            deadline_date=timezone.now() + timedelta(days=10),
        )
        self.assertEqual(
            [n.pk for n in pipeline.select_pending(10)], [self.notice.pk]
        )

    def test_a_bad_layer_set_stops_the_batch_instead_of_being_counted_25_times(self):
        with self.assertRaises(pipeline.UnknownLayer):
            pipeline.extract_pending(limit=10, layers="L1,nope")


class SelectionTests(PipelineTestCase):
    """What the batch offers itself — the question the console's button asks.

    The selection has to agree with ``_extract``'s own skip rule, or the button
    reports work it never did: pressed with a corpus read at L1 while the
    deployment now runs L1,L2,L3, the older rule selected nothing, said so, and
    left the operator believing the deeper set had run.
    """

    DEEP = "L1,L2,L3"

    def _run(self, layers: str, status: str, *, times: int = 1) -> None:
        for _ in range(times):
            ExtractionRun.objects.create(
                notice=self.notice, layers=layers, status=status
            )

    def _selected(self, **kwargs) -> list[str]:
        return [notice.pk for notice in pipeline.select_pending(10, **kwargs)]

    def test_a_notice_read_at_l1_is_offered_again_when_the_batch_reads_deeper(self):
        """The bug this function exists to close: a key arrives, nothing reruns."""
        pipeline.extract_for_notice(self.notice)

        self.assertEqual(self._selected(layers=self.DEEP), [self.notice.pk])

    def test_a_notice_already_read_at_this_depth_is_not_offered_again(self):
        """Pressing the button twice must cost nothing the second time."""
        self._run(self.DEEP, ExtractionRun.Status.OK)

        self.assertEqual(self._selected(layers=self.DEEP), [])

    def test_a_failed_run_leaves_the_notice_in_the_queue(self):
        """The whole point of pressing again: retry what did not work."""
        self._run(self.DEEP, ExtractionRun.Status.FAILED)

        self.assertEqual(self._selected(layers=self.DEEP), [self.notice.pk])

    def test_a_notice_stops_being_offered_after_three_failures_at_one_depth(self):
        """Without the cap a broken notice bills a metered request every cycle."""
        self._run(self.DEEP, ExtractionRun.Status.FAILED, times=pipeline.MAX_ATTEMPTS)

        self.assertEqual(self._selected(layers=self.DEEP), [])

    def test_the_attempt_cap_is_counted_per_depth_not_per_notice(self):
        """Failing at L1 must not disqualify the notice from ever being read deeper."""
        self._run("L1", ExtractionRun.Status.FAILED, times=pipeline.MAX_ATTEMPTS)

        self.assertEqual(self._selected(layers="L1"), [])
        self.assertEqual(self._selected(layers=self.DEEP), [self.notice.pk])

    def test_force_offers_a_notice_the_layer_set_would_otherwise_skip(self):
        """The case a layer set cannot express: same depth, changed prompt."""
        self._run(self.DEEP, ExtractionRun.Status.OK)

        self.assertEqual(self._selected(layers=self.DEEP, force=True), [self.notice.pk])

    def test_force_reaches_past_the_attempt_cap(self):
        """Past the cap it takes a person deciding to spend again — this is that."""
        self._run(self.DEEP, ExtractionRun.Status.FAILED, times=pipeline.MAX_ATTEMPTS)

        self.assertEqual(self._selected(layers=self.DEEP, force=True), [self.notice.pk])

    def test_the_batch_writes_a_second_run_when_forced(self):
        """Selection and extraction have to agree, or ``force`` stops at the door."""
        pipeline.extract_for_notice(self.notice)

        stats = pipeline.extract_pending(limit=10, force=True)

        self.assertEqual(stats.runs, 1)
        self.assertEqual(stats.skipped, 0)
        self.assertEqual(ExtractionRun.objects.filter(notice=self.notice).count(), 2)


class LateDocumentTests(PipelineTestCase):
    """A document that arrives after the run must still be read.

    This is the failure the deployed server was in for four days: extraction is
    chained off the sync and the harvester runs on its own ten-minute schedule,
    so every new notice was read before its Terms of Reference existed. The run
    was ``ok`` — correctly, there was nothing to read — and the older rule read
    that as settled. Five of five notices mirrored since 2026-08-10 had their
    document arrive after their run, and the whole corpus held 13 L3
    requirements against 126 from L2.
    """

    DEEP = "L1,L2,L3"

    def _ok_run(self, layers: str = DEEP) -> ExtractionRun:
        return ExtractionRun.objects.create(
            notice=self.notice, layers=layers, status=ExtractionRun.Status.OK
        )

    def _document(self, *, fetched_at, chars: int = 800) -> HarvestedDocument:
        serial = HarvestedDocument.objects.count()
        document = HarvestedDocument.objects.create(
            url_hash=f"{serial:064d}",
            url=f"https://example.org/tor-{serial}.pdf",
            kind=HarvestedDocument.Kind.TOR,
            status=HarvestedDocument.Status.FETCHED,
            text="The Bidder shall have at least five (5) years of experience. " * 5,
            text_chars=chars,
            has_text_layer=True,
            fetched_at=fetched_at,
        )
        document.notices.add(self.notice)
        return document

    def _selected(self, **kwargs) -> list[str]:
        return [notice.pk for notice in pipeline.select_pending(10, **kwargs)]

    def test_a_tor_mirrored_after_the_run_puts_the_notice_back_in_the_queue(self):
        run = self._ok_run()
        self._document(fetched_at=run.created_at + timedelta(minutes=9))

        self.assertEqual(self._selected(layers=self.DEEP), [self.notice.pk])

    def test_the_per_notice_skip_agrees_with_the_selection(self):
        """A row the batch offers and ``_extract`` skips spends the limit on nothing."""
        run = self._ok_run()
        self._document(fetched_at=run.created_at + timedelta(minutes=9))

        stats = pipeline.extract_one(self.notice, layers=self.DEEP)

        self.assertEqual(stats.skipped, 0)
        self.assertEqual(stats.runs, 1)

    def test_a_document_the_run_already_saw_settles_the_notice(self):
        """Otherwise every cycle re-reads the same TOR at the same cost forever."""
        document = self._document(fetched_at=timezone.now() - timedelta(hours=2))
        self._ok_run()

        self.assertEqual(self._selected(layers=self.DEEP), [])
        self.assertEqual(
            pipeline.extract_one(self.notice, layers=self.DEEP).skipped, 1
        )
        self.assertTrue(document.fetched_at < ExtractionRun.objects.get().created_at)

    def test_an_unreadable_document_does_not_requeue_the_notice(self):
        """``usable()`` is the same line the harvester and L3 already draw."""
        run = self._ok_run()
        self._document(
            fetched_at=run.created_at + timedelta(minutes=9),
            chars=HarvestedDocument.MIN_USEFUL_CHARS - 1,
        )

        self.assertEqual(self._selected(layers=self.DEEP), [])

    def test_a_document_does_not_requeue_a_run_that_never_asked_for_l3(self):
        """A mirrored TOR says nothing about whether L1 read the notice body."""
        run = self._ok_run(layers="L1")
        self._document(fetched_at=run.created_at + timedelta(minutes=9))

        self.assertEqual(self._selected(layers="L1"), [])
        self.assertEqual(pipeline.extract_one(self.notice, layers="L1").skipped, 1)

    def test_three_mirrored_documents_are_not_three_attempts(self):
        """``Count`` over two joins multiplies; the cap would retire a fresh notice."""
        now = timezone.now()
        for offset in range(3):
            self._document(fetched_at=now - timedelta(hours=3 + offset))
        self._ok_run()
        ExtractionRun.objects.create(
            notice=self.notice, layers=self.DEEP, status=ExtractionRun.Status.FAILED
        )

        # One OK run, three documents, all older than it: settled, and the
        # attempt count must not have been inflated past the cap on the way.
        self.assertEqual(self._selected(layers=self.DEEP), [])
        self.assertEqual(self._selected(layers=self.DEEP, force=True), [self.notice.pk])

    def test_a_notice_is_offered_once_however_many_documents_it_has(self):
        run = self._ok_run()
        for offset in range(3):
            self._document(fetched_at=run.created_at + timedelta(minutes=9 + offset))

        self.assertEqual(self._selected(layers=self.DEEP), [self.notice.pk])


class ReportTests(PipelineTestCase):
    def test_the_report_counts_coverage_layers_grounding_and_cost(self):
        pipeline.extract_for_notice(self.notice)
        report = pipeline.corpus_report()

        self.assertEqual(report["notices_with_run"], 1)
        self.assertEqual(report["notices_with_requirements"], 1)
        self.assertEqual(report["by_layer"], {"L1": 2})
        self.assertEqual(report["by_layer_set"]["L1"]["runs"], 1)
        self.assertEqual(report["grounding"]["verified"], 2)
        self.assertEqual(report["grounding"]["rate"], 1.0)
        self.assertEqual(report["cost_usd"], Decimal("0"))
        self.assertEqual(report["coverage_rate"], 1.0)
        self.assertEqual(report["yield_rate"], 1.0)

    def test_coverage_is_measured_against_what_the_batch_actually_walks(self):
        """Measuring against the whole mirror would report a permanent 0%."""
        TenderNotice.objects.create(
            notice_id="OP00000099",
            notice_type="Contract Award",
            country="France",
            notice_text_sanitized=NOTICE_BODY,
        )
        pipeline.extract_for_notice(self.notice)
        report = pipeline.corpus_report()

        self.assertEqual(report["notices_total"], 2)
        self.assertEqual(report["notices_in_scope"], 1)
        self.assertEqual(report["coverage_rate"], 1.0)

    def test_a_notice_that_yields_nothing_lowers_the_yield_but_not_the_coverage(self):
        pipeline.extract_for_notice(self.notice)
        quiet = TenderNotice.objects.create(
            notice_id="OP00000098",
            notice_type="Invitation for Bids",
            country="Uzbekistan",
            notice_text_sanitized="<p>Expressions of interest are invited.</p>",
            deadline_date=timezone.now() + timedelta(days=5),
        )
        pipeline.extract_for_notice(quiet)
        report = pipeline.corpus_report()

        self.assertEqual(report["coverage_rate"], 1.0)
        self.assertEqual(report["yield_rate"], 0.5)

    def test_an_empty_corpus_reports_no_rate_rather_than_zero(self):
        TenderNotice.objects.all().delete()
        report = pipeline.corpus_report()
        self.assertIsNone(report["coverage_rate"])
        self.assertIsNone(report["yield_rate"])
        self.assertIsNone(report["grounding"]["rate"])


class CommandTests(PipelineTestCase):
    def test_status_reports_without_extracting_anything(self):
        out = io.StringIO()
        call_command("extract_requirements", "--status", stdout=out)

        self.assertIn("Extraction coverage", out.getvalue())
        self.assertEqual(ExtractionRun.objects.count(), 0)

    def test_one_notice_can_be_extracted_by_id(self):
        out = io.StringIO()
        call_command("extract_requirements", "--notice", self.notice.pk, stdout=out)

        self.assertEqual(ExtractionRun.objects.filter(notice=self.notice).count(), 1)
        self.assertIn("annual_turnover_avg", out.getvalue())

    def test_a_second_run_of_the_same_notice_says_so_instead_of_spending_again(self):
        call_command("extract_requirements", "--notice", self.notice.pk, stdout=io.StringIO())
        out = io.StringIO()
        call_command("extract_requirements", "--notice", self.notice.pk, stdout=out)

        self.assertIn("--force", out.getvalue())
        self.assertEqual(ExtractionRun.objects.filter(notice=self.notice).count(), 1)

    def test_an_unknown_layer_is_refused_before_anything_runs(self):
        from django.core.management.base import CommandError

        with self.assertRaises(CommandError):
            call_command("extract_requirements", "--layers", "L4", stdout=io.StringIO())
