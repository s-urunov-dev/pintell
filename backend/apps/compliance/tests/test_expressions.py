"""Tests for the requirement expression engine.

Plain ``unittest`` — the evaluator has no Django dependency, so these run
without a database. The last class works through a real World Bank
qualification criterion end to end, because that is the shape the engine exists
to handle and the one a scalar model could not express.
"""

from __future__ import annotations

import unittest
from datetime import date

from apps.compliance.expressions import (
    All,
    Any_,
    AppliesTo,
    Assessment,
    Bid,
    Count,
    Exists,
    ExpressionError,
    Filter,
    Not,
    Portfolio,
    Requirement,
    Scalar,
    Verdict,
    assess,
    parse_node,
    parse_requirement,
)


class ScalarTests(unittest.TestCase):
    def test_a_declared_value_above_the_threshold_passes(self):
        portfolio = Portfolio(scalars={"annual_turnover_avg": 12_000_000})
        node = Scalar(key="annual_turnover_avg", op=">=", value=10_000_000)
        self.assertIs(node.evaluate(portfolio).verdict, Verdict.SATISFIED)

    def test_a_declared_value_below_the_threshold_fails(self):
        portfolio = Portfolio(scalars={"annual_turnover_avg": 8_000_000})
        node = Scalar(key="annual_turnover_avg", op=">=", value=10_000_000)
        self.assertIs(node.evaluate(portfolio).verdict, Verdict.FAILED)

    def test_an_undeclared_value_is_unknown_not_failed(self):
        """The rule the whole engine is built around."""
        node = Scalar(key="annual_turnover_avg", op=">=", value=10_000_000)
        self.assertIs(node.evaluate(Portfolio()).verdict, Verdict.UNKNOWN)

    def test_an_explicit_none_reads_the_same_as_absent(self):
        portfolio = Portfolio(scalars={"annual_turnover_avg": None})
        node = Scalar(key="annual_turnover_avg", op=">=", value=1)
        self.assertIs(node.evaluate(portfolio).verdict, Verdict.UNKNOWN)

    def test_numbers_written_as_strings_still_compare(self):
        """Declared values arrive from forms and spreadsheets, not from code."""
        portfolio = Portfolio(scalars={"annual_turnover_avg": "12,000,000"})
        node = Scalar(key="annual_turnover_avg", op=">=", value=10_000_000)
        self.assertIs(node.evaluate(portfolio).verdict, Verdict.SATISFIED)

    def test_booleans_compare_as_booleans_not_as_one_and_zero(self):
        """`iso_9001 >= 1` must not quietly succeed on a certificate question."""
        portfolio = Portfolio(scalars={"iso_9001": True})
        self.assertIs(
            Scalar(key="iso_9001", op="==", value=True).evaluate(portfolio).verdict,
            Verdict.SATISFIED,
        )
        self.assertIs(
            Scalar(key="iso_9001", op=">=", value=1).evaluate(portfolio).verdict,
            Verdict.UNKNOWN,
        )

    def test_dates_are_ordered_but_strings_are_not(self):
        portfolio = Portfolio(scalars={"licence_valid_until": date(2027, 1, 1)})
        self.assertIs(
            Scalar(key="licence_valid_until", op=">=", value=date(2026, 8, 10))
            .evaluate(portfolio).verdict,
            Verdict.SATISFIED,
        )
        text = Portfolio(scalars={"grade": "B"})
        self.assertIs(
            Scalar(key="grade", op=">=", value="A").evaluate(text).verdict,
            Verdict.UNKNOWN,
        )

    def test_the_trace_names_the_missing_value(self):
        node = Scalar(key="annual_turnover_avg", op=">=", value=10, label="Turnover")
        detail = node.evaluate(Portfolio()).trace.detail
        self.assertIn("Turnover", detail)
        self.assertIn("not declared", detail)


class CountTests(unittest.TestCase):
    def setUp(self):
        self.portfolio = Portfolio(
            collections={
                "contracts": [
                    {"value": 6_000_000, "completed": True, "year": 2024},
                    {"value": 7_500_000, "completed": True, "year": 2023},
                    {"value": 2_000_000, "completed": True, "year": 2024},
                ]
            }
        )

    def test_counts_only_the_records_matching_every_filter(self):
        node = Count(
            entity="contracts", op=">=", value=2,
            where=[Filter("value", ">=", 5_000_000), Filter("completed", "==", True)],
        )
        self.assertIs(node.evaluate(self.portfolio).verdict, Verdict.SATISFIED)

    def test_falls_short_when_too_few_match(self):
        node = Count(
            entity="contracts", op=">=", value=3,
            where=[Filter("value", ">=", 5_000_000)],
        )
        self.assertIs(node.evaluate(self.portfolio).verdict, Verdict.FAILED)

    def test_nothing_declared_is_unknown(self):
        node = Count(entity="contracts", op=">=", value=1)
        self.assertIs(node.evaluate(Portfolio()).verdict, Verdict.UNKNOWN)

    def test_declared_but_empty_is_a_real_failure(self):
        """Saying "I have no contracts" is an answer; saying nothing is not."""
        portfolio = Portfolio(collections={"contracts": []})
        node = Count(entity="contracts", op=">=", value=1)
        self.assertIs(node.evaluate(portfolio).verdict, Verdict.FAILED)

    def test_a_shortfall_with_unreadable_records_is_unknown(self):
        """The missing field might have been the one that qualified them."""
        portfolio = Portfolio(
            collections={"contracts": [
                {"value": 6_000_000},
                {"completed": True},  # no value declared
            ]}
        )
        node = Count(
            entity="contracts", op=">=", value=2, where=[Filter("value", ">=", 5_000_000)]
        )
        result = node.evaluate(portfolio)
        self.assertIs(result.verdict, Verdict.UNKNOWN)
        self.assertIn("missing data", result.trace.detail)

    def test_incomplete_records_do_not_rescue_a_genuine_shortfall(self):
        """If the matches alone already clear the bar, the verdict stands."""
        portfolio = Portfolio(
            collections={"contracts": [
                {"value": 6_000_000},
                {"value": 9_000_000},
                {"completed": True},
            ]}
        )
        node = Count(
            entity="contracts", op=">=", value=2, where=[Filter("value", ">=", 5_000_000)]
        )
        self.assertIs(node.evaluate(portfolio).verdict, Verdict.SATISFIED)

    def test_exists_is_count_at_least_one(self):
        node = Exists(entity="contracts", where=[Filter("value", ">=", 7_000_000)])
        self.assertIs(node.evaluate(self.portfolio).verdict, Verdict.SATISFIED)


class ThreeValuedLogicTests(unittest.TestCase):
    """Kleene semantics — an unknown must never decay into a failure."""

    KNOWN = Portfolio(scalars={"a": 10, "b": 1})

    def _scalar(self, key: str, op: str, value):
        return Scalar(key=key, op=op, value=value)

    def test_all_fails_on_any_definite_failure(self):
        node = All(children=[
            self._scalar("a", ">=", 5),
            self._scalar("b", ">=", 100),
            self._scalar("missing", ">=", 1),
        ])
        self.assertIs(node.evaluate(self.KNOWN).verdict, Verdict.FAILED)

    def test_all_is_unknown_when_only_doubt_remains(self):
        node = All(children=[
            self._scalar("a", ">=", 5),
            self._scalar("missing", ">=", 1),
        ])
        self.assertIs(node.evaluate(self.KNOWN).verdict, Verdict.UNKNOWN)

    def test_any_succeeds_on_a_single_definite_success(self):
        node = Any_(children=[
            self._scalar("b", ">=", 100),
            self._scalar("a", ">=", 5),
            self._scalar("missing", ">=", 1),
        ])
        self.assertIs(node.evaluate(self.KNOWN).verdict, Verdict.SATISFIED)

    def test_any_is_unknown_when_the_rest_failed_but_one_is_undeclared(self):
        node = Any_(children=[
            self._scalar("b", ">=", 100),
            self._scalar("missing", ">=", 1),
        ])
        self.assertIs(node.evaluate(self.KNOWN).verdict, Verdict.UNKNOWN)

    def test_not_leaves_unknown_alone(self):
        self.assertIs(
            Not(child=self._scalar("a", ">=", 5)).evaluate(self.KNOWN).verdict,
            Verdict.FAILED,
        )
        self.assertIs(
            Not(child=self._scalar("missing", ">=", 1)).evaluate(self.KNOWN).verdict,
            Verdict.UNKNOWN,
        )

    def test_the_trace_nests_so_the_reason_survives(self):
        node = All(children=[self._scalar("a", ">=", 5), self._scalar("b", ">=", 100)])
        lines = node.evaluate(self.KNOWN).trace.lines()
        self.assertEqual(len(lines), 3)
        self.assertTrue(lines[0].startswith("[failed]"))


class JointVentureTests(unittest.TestCase):
    """The qualification matrix the standard bidding documents publish."""

    def setUp(self):
        self.lead = Portfolio(
            name="Alpha", is_jv_lead=True, jv_share=60,
            scalars={"annual_turnover_avg": 8_000_000},
            collections={"contracts": [{"value": 6_000_000}]},
        )
        self.partner = Portfolio(
            name="Beta", jv_share=40,
            scalars={"annual_turnover_avg": 5_000_000},
            collections={"contracts": [{"value": 3_000_000}]},
        )
        self.jv = Bid(members=(self.lead, self.partner))

    def _requirement(self, applies_to):
        return Requirement(
            key="annual_turnover_avg",
            expression=Scalar(key="annual_turnover_avg", op=">=", value=10_000_000),
            applies_to=applies_to,
        )

    def test_combined_pools_numeric_scalars(self):
        """8M + 5M clears a 10M bar that neither partner clears alone."""
        result = self._requirement(AppliesTo.JV_COMBINED).evaluate(self.jv)
        self.assertIs(result.verdict, Verdict.SATISFIED)

    def test_each_party_is_judged_alone(self):
        result = self._requirement(AppliesTo.JV_EACH).evaluate(self.jv)
        self.assertIs(result.verdict, Verdict.FAILED)

    def test_at_least_one_party_needs_a_single_qualifier(self):
        requirement = Requirement(
            key="annual_turnover_avg",
            expression=Scalar(key="annual_turnover_avg", op=">=", value=6_000_000),
            applies_to=AppliesTo.JV_AT_LEAST_ONE,
        )
        self.assertIs(requirement.evaluate(self.jv).verdict, Verdict.SATISFIED)

    def test_combined_pools_collections_too(self):
        requirement = Requirement(
            key="similar_contracts_count",
            expression=Count(entity="contracts", op=">=", value=2),
            applies_to=AppliesTo.JV_COMBINED,
        )
        self.assertIs(requirement.evaluate(self.jv).verdict, Verdict.SATISFIED)

    def test_combined_drops_scalars_that_cannot_be_summed(self):
        """There is no meaningful combined answer to "do you hold ISO 9001"."""
        jv = Bid(members=(
            Portfolio(name="A", scalars={"iso_9001": True}),
            Portfolio(name="B", scalars={"iso_9001": False}),
        ))
        requirement = Requirement(
            key="iso_9001",
            expression=Scalar(key="iso_9001", op="==", value=True),
            applies_to=AppliesTo.JV_COMBINED,
        )
        self.assertIs(requirement.evaluate(jv).verdict, Verdict.UNKNOWN)

    def test_a_single_bidder_reduces_to_one_evaluation(self):
        """Every applies_to mode agrees when there is only one entity."""
        solo = Bid.single(self.lead)
        verdicts = {
            self._requirement(mode).evaluate(solo).verdict for mode in AppliesTo
        }
        self.assertEqual(verdicts, {Verdict.FAILED})

    def test_the_trace_names_each_member(self):
        result = self._requirement(AppliesTo.JV_EACH).evaluate(self.jv)
        text = "\n".join(result.trace.lines())
        self.assertIn("Alpha", text)
        self.assertIn("Beta", text)

    def test_an_empty_bid_is_rejected(self):
        with self.assertRaises(ExpressionError):
            Bid(members=())


class AssessmentTests(unittest.TestCase):
    def setUp(self):
        self.turnover = Requirement(
            key="annual_turnover_avg", label="Average annual turnover",
            expression=Scalar(key="annual_turnover_avg", op=">=", value=10_000_000),
        )
        self.iso = Requirement(
            key="iso_9001", label="ISO 9001",
            expression=Scalar(key="iso_9001", op="==", value=True),
            is_mandatory=False,
        )
        self.experience = Requirement(
            key="years_in_business", label="Years in business",
            expression=Scalar(key="years_in_business", op=">=", value=5),
        )

    def _assess(self, scalars):
        return assess(
            [self.turnover, self.iso, self.experience],
            Bid.single(Portfolio(scalars=scalars)),
        )

    def test_a_failed_mandatory_requirement_blocks(self):
        result = self._assess({"annual_turnover_avg": 1_000_000, "years_in_business": 10})
        self.assertEqual(result.status, "blocked")
        self.assertIs(result.hard_eligibility_pass, False)
        self.assertEqual(len(result.blockers), 1)

    def test_a_missing_mandatory_answer_is_incomplete_not_blocked(self):
        result = self._assess({"annual_turnover_avg": 12_000_000})
        self.assertEqual(result.status, "incomplete")
        self.assertIsNone(result.hard_eligibility_pass)

    def test_everything_satisfied_is_eligible(self):
        result = self._assess({
            "annual_turnover_avg": 12_000_000,
            "years_in_business": 10,
            "iso_9001": True,
        })
        self.assertEqual(result.status, "eligible")
        self.assertIs(result.hard_eligibility_pass, True)

    def test_an_unknown_preference_does_not_block_eligibility(self):
        """Only mandatory requirements gate the hard pass."""
        result = self._assess({"annual_turnover_avg": 12_000_000, "years_in_business": 10})
        self.assertEqual(result.status, "eligible")
        self.assertIs(result.hard_eligibility_pass, True)
        self.assertEqual(len(result.unknowns), 1)  # the ISO preference

    def test_a_failed_preference_does_not_block_either(self):
        result = self._assess({
            "annual_turnover_avg": 12_000_000,
            "years_in_business": 10,
            "iso_9001": False,
        })
        self.assertEqual(result.status, "eligible")
        self.assertEqual(result.blockers, [])

    def test_coverage_reports_how_much_was_settled(self):
        result = self._assess({"annual_turnover_avg": 12_000_000})
        self.assertEqual(result.coverage, round(1 / 3, 3))

    def test_no_requirements_means_unrated_not_eligible(self):
        empty = Assessment(results=[])
        self.assertEqual(empty.status, "unrated")
        self.assertEqual(empty.coverage, 0.0)

    def test_the_explanation_names_blockers_and_unknowns(self):
        result = self._assess({"annual_turnover_avg": 1_000_000})
        text = result.explanation()
        self.assertIn("Blocked by:", text)
        self.assertIn("Average annual turnover", text)
        self.assertIn("Not yet established:", text)
        self.assertIn("Years in business", text)

    def test_the_explanation_is_positive_when_nothing_is_outstanding(self):
        result = self._assess({
            "annual_turnover_avg": 12_000_000,
            "years_in_business": 10,
            "iso_9001": True,
        })
        self.assertIn("satisfied", result.explanation())


class SerialisationTests(unittest.TestCase):
    """Requirements live in JSONB and are written by an extraction pipeline."""

    PAYLOAD = {
        "key": "similar_contracts_count",
        "label": "Similar contracts",
        "applies_to": "jv_combined",
        "is_mandatory": True,
        "evidence_quote": "at least two (2) contracts within the last five (5) years",
        "source": "bidding_doc_p42",
        "expression": {
            "kind": "count",
            "entity": "contracts",
            "op": ">=",
            "value": 2,
            "where": [
                {"field": "value", "op": ">=", "value": 5000000},
                {"field": "completed", "op": "==", "value": True},
            ],
        },
    }

    def test_round_trip_preserves_the_tree(self):
        requirement = parse_requirement(self.PAYLOAD)
        self.assertEqual(requirement.applies_to, AppliesTo.JV_COMBINED)
        self.assertEqual(requirement.to_dict()["expression"], self.PAYLOAD["expression"])

    def test_a_parsed_requirement_evaluates(self):
        requirement = parse_requirement(self.PAYLOAD)
        bid = Bid.single(Portfolio(collections={"contracts": [
            {"value": 6_000_000, "completed": True},
            {"value": 8_000_000, "completed": True},
        ]}))
        self.assertIs(requirement.evaluate(bid).verdict, Verdict.SATISFIED)

    def test_nested_trees_round_trip(self):
        payload = {
            "kind": "any",
            "children": [
                {"kind": "count", "entity": "contracts", "op": ">=", "value": 2},
                {"kind": "all", "children": [
                    {"kind": "scalar", "key": "years_in_business", "op": ">=", "value": 10},
                    {"kind": "not", "child": {
                        "kind": "scalar", "key": "debarred", "op": "==", "value": True,
                    }},
                ]},
            ],
        }
        self.assertEqual(parse_node(payload).to_dict(), payload)

    def test_an_unknown_node_kind_is_rejected(self):
        with self.assertRaises(ExpressionError):
            parse_node({"kind": "regex", "pattern": ".*"})

    def test_an_unknown_comparator_is_rejected(self):
        with self.assertRaises(ExpressionError):
            parse_node({"kind": "scalar", "key": "a", "op": "~=", "value": 1})

    def test_an_unknown_applies_to_is_rejected(self):
        with self.assertRaises(ExpressionError):
            parse_requirement({**self.PAYLOAD, "applies_to": "jv_mostly"})

    def test_an_empty_conjunction_is_rejected_rather_than_vacuously_true(self):
        """A bidder must never be passed by an extraction bug."""
        with self.assertRaises(ExpressionError):
            parse_node({"kind": "all", "children": []})

    def test_a_missing_expression_is_rejected(self):
        payload = {k: v for k, v in self.PAYLOAD.items() if k != "expression"}
        with self.assertRaises(ExpressionError):
            parse_requirement(payload)


class RealCriterionTests(unittest.TestCase):
    """One World Bank qualification criterion, end to end.

    From Section III of the standard bidding document for works:

        Participation as contractor or JV member in at least 2 contracts within
        the last 5 years, each with a value of at least US$5,000,000, that have
        been successfully completed — All Parties Combined.

    A scalar model stores this as ``similar_contracts_count >= 2`` and loses
    the value floor, the time window, the completion condition, and the JV
    column. This is the test that the tree does not.
    """

    REQUIREMENT = Requirement(
        key="similar_contracts_count",
        label="Similar contracts (last 5 years, ≥ US$5m, completed)",
        applies_to=AppliesTo.JV_COMBINED,
        evidence_quote=(
            "participation as contractor or JV member in at least two (2) contracts "
            "within the last five (5) years, each with a value of at least "
            "US$5,000,000, that have been successfully completed"
        ),
        source="bidding_doc_p42",
        expression=Count(
            entity="contracts", op=">=", value=2,
            where=[
                Filter("value_usd", ">=", 5_000_000),
                Filter("completed_year", ">=", 2021),
                Filter("successfully_completed", "==", True),
            ],
        ),
    )

    def test_a_qualifying_joint_venture_passes(self):
        jv = Bid(members=(
            Portfolio(name="Alpha", is_jv_lead=True, collections={"contracts": [
                {"value_usd": 6_000_000, "completed_year": 2024, "successfully_completed": True},
            ]}),
            Portfolio(name="Beta", collections={"contracts": [
                {"value_usd": 9_000_000, "completed_year": 2023, "successfully_completed": True},
            ]}),
        ))
        self.assertIs(self.REQUIREMENT.evaluate(jv).verdict, Verdict.SATISFIED)

    def test_contracts_that_are_too_old_do_not_count(self):
        jv = Bid.single(Portfolio(collections={"contracts": [
            {"value_usd": 6_000_000, "completed_year": 2024, "successfully_completed": True},
            {"value_usd": 9_000_000, "completed_year": 2015, "successfully_completed": True},
        ]}))
        self.assertIs(self.REQUIREMENT.evaluate(jv).verdict, Verdict.FAILED)

    def test_contracts_below_the_value_floor_do_not_count(self):
        jv = Bid.single(Portfolio(collections={"contracts": [
            {"value_usd": 6_000_000, "completed_year": 2024, "successfully_completed": True},
            {"value_usd": 900_000, "completed_year": 2024, "successfully_completed": True},
        ]}))
        self.assertIs(self.REQUIREMENT.evaluate(jv).verdict, Verdict.FAILED)

    def test_an_undeclared_completion_status_yields_unknown_not_rejection(self):
        """The bidder is asked for the missing field, not turned away."""
        jv = Bid.single(Portfolio(collections={"contracts": [
            {"value_usd": 6_000_000, "completed_year": 2024, "successfully_completed": True},
            {"value_usd": 9_000_000, "completed_year": 2023},  # status not declared
        ]}))
        result = self.REQUIREMENT.evaluate(jv)
        self.assertIs(result.verdict, Verdict.UNKNOWN)

    def test_the_verdict_comes_with_its_working(self):
        jv = Bid.single(Portfolio(collections={"contracts": []}))
        result = self.REQUIREMENT.evaluate(jv)
        text = "\n".join(result.trace.lines())
        self.assertIn("Similar contracts", text)
        self.assertIn("mandatory", text)
        self.assertIn("0 matching", text)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
