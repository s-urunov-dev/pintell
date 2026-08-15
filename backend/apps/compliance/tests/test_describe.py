"""Turning a stored requirement back into a sentence.

`Trace` explains a requirement after it has been evaluated against a bidder —
"why did this company fail". These cases cover the question asked before there
is a bidder at all: what does the tender demand? The operator console shows the
answer, and the alternative it replaces is raw JSON.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.compliance.expressions import All, ExpressionError, describe, parse_node


def described(payload: dict) -> str:
    return describe(parse_node(payload))


class ScalarTests(SimpleTestCase):
    def test_a_threshold_reads_as_a_comparison_with_its_unit(self):
        self.assertEqual(
            described(
                {
                    "kind": "scalar",
                    "key": "annual_turnover_avg",
                    "op": ">=",
                    "value": 5000000,
                    "unit": "USD",
                    "label": "Average annual turnover",
                }
            ),
            "Average annual turnover ≥ 5 000 000 USD",
        )

    def test_the_key_stands_in_when_there_is_no_label(self):
        self.assertEqual(
            described({"kind": "scalar", "key": "staff", "op": ">=", "value": 25}),
            "staff ≥ 25",
        )

    def test_a_boolean_reads_as_a_word_not_as_True(self):
        self.assertEqual(
            described({"kind": "scalar", "key": "is_jv", "op": "==", "value": True}),
            "is_jv = yes",
        )

    def test_a_fraction_keeps_its_decimal(self):
        self.assertEqual(
            described({"kind": "scalar", "key": "ratio", "op": ">=", "value": 1.5}),
            "ratio ≥ 1.5",
        )


class NumberFormattingTests(SimpleTestCase):
    """Money is grouped; a year is not."""

    def test_a_large_amount_is_grouped_so_the_magnitude_is_readable(self):
        """5000000 and 50000000 are one keystroke apart unseparated."""
        self.assertEqual(
            described({"kind": "scalar", "key": "t", "op": ">=", "value": 50000000}),
            "t ≥ 50 000 000",
        )

    def test_a_year_is_not_grouped(self):
        """'2 019' reads as a rendering bug, which is how this was found."""
        self.assertEqual(
            described(
                {
                    "kind": "count",
                    "entity": "similar_contracts",
                    "op": ">=",
                    "value": 2,
                    "where": [{"field": "year", "op": ">=", "value": 2019}],
                }
            ),
            "similar_contracts: count ≥ 2 where year ≥ 2019",
        )


class TreeTests(SimpleTestCase):
    def test_a_count_states_its_threshold_and_its_conditions(self):
        self.assertEqual(
            described(
                {
                    "kind": "count",
                    "entity": "similar_contracts",
                    "op": ">=",
                    "value": 2,
                    "where": [
                        {"field": "value", "op": ">=", "value": 1500000},
                        {"field": "year", "op": ">=", "value": 2019},
                    ],
                    "label": "Similar contracts",
                }
            ),
            "Similar contracts: count ≥ 2 where value ≥ 1 500 000, year ≥ 2019",
        )

    def test_exists_reads_as_at_least_one_because_that_is_what_it_evaluates_as(self):
        """`Exists` delegates to `Count >= 1`; the sentence should not invent a
        second concept for the operator to learn."""
        self.assertEqual(
            described(
                {
                    "kind": "exists",
                    "entity": "certificates",
                    "where": [{"field": "name", "op": "==", "value": "ISO 9001"}],
                }
            ),
            "at least one certificates where name = ISO 9001",
        )

    def test_nesting_is_parenthesised_so_and_or_cannot_be_misread(self):
        self.assertEqual(
            described(
                {
                    "kind": "all",
                    "children": [
                        {"kind": "scalar", "key": "liquid_assets", "op": ">=", "value": 750000},
                        {
                            "kind": "any",
                            "children": [
                                {"kind": "scalar", "key": "years", "op": ">=", "value": 5},
                                {"kind": "count", "entity": "projects", "op": ">", "value": 10},
                            ],
                        },
                    ],
                }
            ),
            "(liquid_assets ≥ 750 000) and ((years ≥ 5) or (projects: count > 10))",
        )

    def test_a_negation_says_so(self):
        self.assertEqual(
            described({"kind": "not", "child": {"kind": "exists", "entity": "debarments"}}),
            "not: at least one debarments",
        )


class MalformedTests(SimpleTestCase):
    def test_an_unknown_kind_is_refused_by_the_parser_not_described(self):
        """`describe` renders a tree; deciding what is a tree stays with
        `parse_node`, which raises rather than guessing."""
        with self.assertRaises(ExpressionError):
            described({"kind": "teleport", "key": "x"})

    def test_an_empty_group_never_reaches_describe_because_the_parser_rejects_it(self):
        with self.assertRaises(ExpressionError):
            described({"kind": "all", "children": []})

    def test_a_group_emptied_in_code_still_renders_something(self):
        """Unreachable through `parse_node`, kept reachable through the
        dataclass — and a blank console cell would read as "no requirement",
        which is the one meaning it must not have."""
        self.assertEqual(describe(All(children=())), "(and: nothing)")
