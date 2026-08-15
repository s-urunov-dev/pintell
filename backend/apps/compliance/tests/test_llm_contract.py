"""The schema we hand the model must speak the engine's own grammar.

This file exists because of a bug it would have caught. ``llm.REQUIREMENT_SCHEMA``
was written with ``{"type": ..., "field": ...}`` while ``expressions`` serialises
``{"kind": ..., "key": ...}``. Every layer test passed — they assert on the
``Extracted`` rows, which carry the tree unparsed — and every extraction would
have been discarded at the pipeline boundary as malformed. The API key was
empty, so no request was ever made and nothing revealed it.

The gap was structural: two modules agreed on a vocabulary in prose and nothing
compared them. These tests compare them. They are not about the model, the
prompt or the network — they take the enum values out of the schema, build the
smallest instance of each, and require the engine to parse it.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.compliance import llm
from apps.compliance.expressions import ExpressionError, parse_node, parse_requirement

_EXPRESSION = llm.REQUIREMENT_SCHEMA["properties"]["requirements"]["items"]["properties"][
    "expression"
]

#: The smallest tree of each kind the schema allows, in the schema's own words.
_SMALLEST = {
    "scalar": {"kind": "scalar", "key": "annual_turnover_avg", "op": ">=", "value": 1},
    "count": {
        "kind": "count",
        "entity": "contract",
        "op": ">=",
        "value": 2,
        "where": [{"field": "value", "op": ">=", "value": 1}],
    },
    "exists": {
        "kind": "exists",
        "entity": "certification",
        "where": [{"field": "name", "op": "==", "value": "ISO 9001"}],
    },
}


class TheSchemaAndTheEngineAgree(SimpleTestCase):
    def test_every_node_kind_the_schema_offers_is_one_the_engine_parses(self):
        """A tree the model is invited to build must be one we can evaluate."""
        for kind in _EXPRESSION["properties"]["kind"]["enum"]:
            with self.subTest(kind=kind):
                self.assertIn(kind, _SMALLEST, "no sample tree for an offered kind")
                parse_node(_SMALLEST[kind])

    def test_the_discriminator_is_the_engine_s_own_word(self):
        """``type`` instead of ``kind`` produces trees that look right and never parse."""
        self.assertIn("kind", _EXPRESSION["properties"])
        self.assertNotIn("type", _EXPRESSION["properties"])
        with self.assertRaises(ExpressionError):
            parse_node({"type": "scalar", "field": "x", "op": ">=", "value": 1})

    def test_a_whole_requirement_in_schema_vocabulary_round_trips(self):
        """The boundary parses ``as_payload()``'s shape, so that is what is checked."""
        requirement = parse_requirement(
            {
                "key": "annual_turnover_avg",
                "label": "Average annual turnover",
                "expression": _SMALLEST["scalar"],
                "applies_to": "single",
                "is_mandatory": True,
                "evidence_quote": "an average annual turnover of at least USD 1",
                "source": "notice_body",
            }
        )
        self.assertEqual(requirement.key, "annual_turnover_avg")
        self.assertEqual(requirement.expression.to_dict()["kind"], "scalar")

    def test_the_engine_s_own_output_satisfies_the_schema_s_field_names(self):
        """Serialise, then check the keys are ones the schema declares.

        The direction that matters: a node the engine produces must be
        describable by the schema, or the model can never be asked for it.
        """
        allowed = set(_EXPRESSION["properties"])
        for kind, payload in _SMALLEST.items():
            with self.subTest(kind=kind):
                produced = parse_node(payload).to_dict()
                self.assertLessEqual(set(produced), allowed, f"{kind} emits unknown keys")


class TheSchemaIsUsableAsAStructuredOutputFormat(SimpleTestCase):
    def test_every_object_forbids_properties_it_did_not_declare(self):
        """``additionalProperties: false`` is required throughout, or the API rejects it."""
        missing: list[str] = []

        def walk(node, path="$"):
            if isinstance(node, dict):
                if node.get("type") == "object" and node.get("additionalProperties") is not False:
                    missing.append(path)
                for name, child in node.items():
                    walk(child, f"{path}.{name}")
            elif isinstance(node, list):
                for index, child in enumerate(node):
                    walk(child, f"{path}[{index}]")

        walk(llm.REQUIREMENT_SCHEMA)
        self.assertEqual(missing, [])

    def test_the_quote_is_required_of_every_requirement(self):
        """A row with no quote is a claim nobody can check — D4."""
        item = llm.REQUIREMENT_SCHEMA["properties"]["requirements"]["items"]
        self.assertIn("evidence_quote", item["required"])

    def test_the_prompt_version_is_recorded_so_a_change_is_attributable(self):
        self.assertTrue(llm.PROMPT_VERSION)

    def test_the_system_prompt_never_interpolates_anything(self):
        """An interpolated byte at the front of the prefix voids the cache for every call."""
        self.assertNotIn("{", llm.SYSTEM_PROMPT.replace('{"', "").replace('"}', ""))


class CostIsRecordedNotGuessed(SimpleTestCase):
    def test_an_unknown_model_costs_zero_rather_than_a_guess(self):
        """A guessed price would quietly corrupt the ablation table."""
        self.assertEqual(
            llm.estimate_cost("some-model-we-do-not-price", input_tokens=1000, output_tokens=1000),
            0,
        )

    def test_cache_reads_are_billed_at_a_tenth_of_the_input_rate(self):
        read_only = llm.estimate_cost(
            "claude-haiku-4-5", input_tokens=0, output_tokens=0, cached_tokens=1_000_000
        )
        fresh = llm.estimate_cost("claude-haiku-4-5", input_tokens=1_000_000, output_tokens=0)
        self.assertEqual(read_only * 10, fresh)


class EffortIsSentOnlyWhereItIsAccepted(SimpleTestCase):
    """Switching the model tier must not be able to break the request.

    The tier is a measured axis of the evaluation (D6), so it gets changed —
    and pointing `AI_MODEL` at Haiku to save money during tuning returned 400
    "This model does not support the effort parameter" on every single call.
    The schema has to travel; the effort knob does not.
    """

    def build(self, model: str) -> dict:
        return llm.build_request("source", "instruction", llm.LLMConfig(model=model))

    def test_a_model_that_supports_effort_is_sent_it(self):
        output_config = self.build("claude-opus-5")["output_config"]

        self.assertEqual(output_config["effort"], "low")

    def test_a_model_that_does_not_support_effort_is_not_sent_it(self):
        output_config = self.build("claude-haiku-4-5")["output_config"]

        self.assertNotIn("effort", output_config)

    def test_the_schema_travels_either_way(self):
        """Effort is a knob; the schema is the thing that makes the answer
        parseable, and it must never be the part that gets dropped."""
        for model in ("claude-opus-5", "claude-haiku-4-5"):
            with self.subTest(model=model):
                output_config = self.build(model)["output_config"]
                self.assertEqual(output_config["format"]["type"], "json_schema")
                self.assertIn("properties", output_config["format"]["schema"])

    def test_an_unknown_model_is_assumed_not_to_support_effort(self):
        """The safe direction: an unlisted model loses a knob rather than
        losing every call."""
        output_config = self.build("some-model-we-have-not-seen")["output_config"]

        self.assertNotIn("effort", output_config)


class ADatedModelIdIsStillPriced(SimpleTestCase):
    """`AI_MODEL` is an alias; the API answers with the snapshot it resolved to.

    `ExtractionRun.model` records what actually ran, so the cost lookup sees
    `claude-haiku-4-5-20251001` where the table is keyed `claude-haiku-4-5`.
    Strict keying priced every successful run at zero — "$0 spent" over a page
    of real extractions, which reads exactly like the failure it is not.
    """

    def test_a_dated_snapshot_is_priced_as_its_family(self):
        dated = llm.estimate_cost(
            "claude-haiku-4-5-20251001", input_tokens=1_000_000, output_tokens=0
        )
        alias = llm.estimate_cost(
            "claude-haiku-4-5", input_tokens=1_000_000, output_tokens=0
        )

        self.assertEqual(dated, alias)
        self.assertGreater(dated, 0)

    def test_a_genuinely_unknown_model_is_still_zero(self):
        """The protection against a guessed price has to survive the change."""
        self.assertEqual(
            llm.estimate_cost("gpt-nonexistent", input_tokens=1_000_000, output_tokens=0),
            0,
        )

    def test_an_unrelated_model_is_not_captured_by_a_shorter_key(self):
        self.assertEqual(
            llm.estimate_cost("claude-haiku", input_tokens=1_000_000, output_tokens=0),
            0,
        )
