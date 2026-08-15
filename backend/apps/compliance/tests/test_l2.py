"""L2 asks only for what is missing, and never for more than it was shown."""

from __future__ import annotations

import json
from types import SimpleNamespace

from django.test import SimpleTestCase

from apps.compliance import l2, llm
from apps.compliance.text import canonical, contains_quote
from apps.compliance.tests.test_l1 import REAL_NOTICE

# OP00444306 (Kyrgyz Republic, REOI for the Uzgen New Museum interior and
# exhibition design, ARIS/REDP, May 2026), copied from the notice body. Chosen
# because L1 reads *nothing* from it and is right not to: there is no amount
# anywhere, and "at least 3 years of experience in museum and cultural heritage
# projects" is a requirement no rule can be written for without matching half
# the scope of works as well. This is precisely the gap L2 exists to fill.
IRREGULAR_NOTICE = (
    "<p>The Agency hereby invites eligible consulting firms (&quot;Consultants&quot;) "
    "to express interest in providing these services.</p>"
    "<p><strong>The following minimum requirements shall be met by an interested "
    "Consulting Firm to be shortlisted:</strong></p>"
    "<ol><li>The Consultant (firm or consortium) must have <strong>at least 3 years "
    "of experience</strong> in museum and cultural heritage projects, including "
    "scientific concept development, exhibition design, interior design, and visitor "
    "interpretation.</li>"
    "<li>Experience in projects funded or supervised by international organizations "
    "(e.g., World Bank, UNESCO, EU) is desirable.</li></ol>"
    "<p>Expressions of Interest must be submitted in written form to the address "
    "below no later than 12:00 (local time) on May 28, 2026.</p>"
)

#: The sentence a well-behaved model would quote from it, verbatim.
YEARS_QUOTE = (
    "The Consultant (firm or consortium) must have at least 3 years of experience "
    "in museum and cultural heritage projects, including scientific concept "
    "development, exhibition design, interior design, and visitor interpretation."
)

# The two paragraphs of OP00456288 that L1 reads on its own, with nothing else
# in the body. Used for the case where the earlier layers have already read
# everything the notice states.
FULLY_READ_NOTICE = (
    "<p>Minimum average annual turnover of USD 28,000,000.00 (twenty-eight million "
    "United States Dollars), calculated as total certified payments received for "
    "contracts in progress and/or completed within past three years.</p>"
    "<p>All Bids must be accompanied by a Bid Security of USD 280,000.00 (Two "
    "hundred and eighty thousand United States Dollars).</p>"
)


# ---------------------------------------------------------------------------
# The double
# ---------------------------------------------------------------------------
def response(
    payload: str | None,
    *,
    stop_reason: str = "end_turn",
    model: str = "claude-haiku-4-5",
    input_tokens: int = 1400,
    output_tokens: int = 260,
    cache_read: int = 1100,
) -> SimpleNamespace:
    """One SDK response, shaped the way ``llm`` reads it.

    ``payload`` of ``None`` produces a response with no text block at all — the
    shape a refusal or a truncated stream actually arrives in.
    """
    content = [] if payload is None else [SimpleNamespace(type="text", text=payload)]
    return SimpleNamespace(
        content=content,
        model=model,
        stop_reason=stop_reason,
        usage=SimpleNamespace(
            input_tokens=input_tokens,
            output_tokens=output_tokens,
            cache_read_input_tokens=cache_read,
            cache_creation_input_tokens=0,
        ),
    )


class FakeClient:
    """Scripted stand-in for ``anthropic.Anthropic``; records what it was sent.

    Every assertion about *not* spending a request is an assertion that this
    object was never called, so it deliberately fails loudly rather than
    returning a default when the script runs out.
    """

    def __init__(self, *responses):
        self._responses = list(responses)
        self.calls: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.calls.append(kwargs)
        if not self._responses:
            raise AssertionError("the layer made more requests than the test scripted")
        return self._responses.pop(0)

    @property
    def source_sent(self) -> str:
        """The source text the last request actually showed the model."""
        content = self.calls[-1]["messages"][0]["content"]
        return content.split("<source>\n", 1)[1].rsplit("\n</source>", 1)[0]

    @property
    def instruction_sent(self) -> str:
        return self.calls[-1]["messages"][0]["content"].split("\n\n<source>", 1)[0]


class RaisingClient:
    """A client whose transport is down. Nothing may escape the layer."""

    def __init__(self):
        self.messages = self
        self.calls: list[dict] = []

    def create(self, **kwargs):
        self.calls.append(kwargs)
        raise ConnectionError("connection reset by peer")


def requirement_row(key: str, quote: str, **overrides) -> dict:
    row = {
        "key": key,
        "label": key.replace("_", " ").title(),
        "applies_to": "single",
        "is_mandatory": True,
        "evidence_quote": quote,
        "expression": {"kind": "scalar", "key": key, "op": ">=", "value": 3},
    }
    row.update(overrides)
    return row


def answer(*rows: dict) -> str:
    return json.dumps({"requirements": list(rows)})


# ---------------------------------------------------------------------------
class ReadingTheNoticeTests(SimpleTestCase):
    """What comes back from a well-behaved model."""

    def test_a_criterion_no_rule_could_reach_is_returned_attributed_to_the_body(self):
        client = FakeClient(response(answer(requirement_row("years_experience", YEARS_QUOTE))))
        result = l2.extract(IRREGULAR_NOTICE, reference_year=2026, client=client)

        [requirement] = result.requirements
        self.assertEqual(requirement.key, "years_experience")
        self.assertEqual(requirement.source, "notice_body")
        self.assertTrue(result.ok)

    def test_a_preference_stays_a_preference(self):
        """"is desirable" is not a gate, and the schema carries that distinction."""
        quote = (
            "Experience in projects funded or supervised by international "
            "organizations (e.g., World Bank, UNESCO, EU) is desirable."
        )
        client = FakeClient(
            response(answer(requirement_row("donor_experience", quote, is_mandatory=False)))
        )
        [requirement] = l2.extract(IRREGULAR_NOTICE, client=client).requirements
        self.assertFalse(requirement.is_mandatory)

    def test_the_model_is_shown_text_not_markup(self):
        """A quote copied out of HTML could never be found in the source again."""
        client = FakeClient(response(answer()))
        l2.extract(IRREGULAR_NOTICE, client=client)

        self.assertNotIn("<strong>", client.source_sent)
        self.assertNotIn("&quot;", client.source_sent)
        self.assertIn(YEARS_QUOTE, client.source_sent)

    def test_what_the_call_cost_is_recorded_where_the_ablation_reads_it(self):
        """A cost reconstructed later from logs is not a measurement."""
        client = FakeClient(response(answer(requirement_row("years_experience", YEARS_QUOTE))))
        result = l2.extract(IRREGULAR_NOTICE, client=client)

        self.assertEqual(result.input_tokens, 1400)
        self.assertEqual(result.output_tokens, 260)
        self.assertGreater(result.cost_usd, 0)
        self.assertEqual(result.model, "claude-haiku-4-5")
        self.assertEqual(result.prompt_version, llm.PROMPT_VERSION)
        self.assertEqual(result.notes["cache_read_tokens"], 1100)


class GroundingTests(SimpleTestCase):
    """A quote that is not in the source it claims to come from."""

    def test_an_unfindable_quote_is_kept_so_it_can_be_counted(self):
        """Deleting the row would destroy the measurement it exists to produce.

        A discarded hallucination and a requirement that was never proposed are
        indistinguishable afterwards, which would report a grounding rate of
        100% however the model behaved. The row travels on; the verifier marks
        it NOT_FOUND and ``is_usable`` keeps it out of every verdict.
        """
        invented = "The Consultant must hold a valid ISO 9001 certificate."
        client = FakeClient(response(answer(requirement_row("certification", invented))))
        result = l2.extract(IRREGULAR_NOTICE, client=client)

        self.assertEqual(len(result.requirements), 1)
        self.assertEqual(result.notes["quotes_not_in_source"], 1)
        self.assertEqual(result.notes["quotes_verified"], 0)
        self.assertFalse(contains_quote(IRREGULAR_NOTICE, result.requirements[0].evidence_quote))

    def test_a_quote_copied_out_of_the_source_is_counted_as_verified(self):
        client = FakeClient(response(answer(requirement_row("years_experience", YEARS_QUOTE))))
        result = l2.extract(IRREGULAR_NOTICE, client=client)

        self.assertEqual(result.notes["quotes_verified"], 1)
        self.assertEqual(result.notes["quotes_not_in_source"], 0)

    def test_a_tidied_quote_does_not_pass_as_verbatim(self):
        """Shortening is the failure mode the prompt argues hardest against."""
        shortened = "The Consultant must have 3 years of experience in museum projects."
        client = FakeClient(response(answer(requirement_row("years_experience", shortened))))
        result = l2.extract(IRREGULAR_NOTICE, client=client)

        self.assertEqual(result.notes["quotes_not_in_source"], 1)


class NotAskingTwiceTests(SimpleTestCase):
    """The layer stack's economic argument, enforced in both directions."""

    def test_what_earlier_layers_established_is_named_in_the_request(self):
        client = FakeClient(response(answer()))
        l2.extract(
            REAL_NOTICE,
            reference_year=2026,
            exclude_keys=["annual_turnover_avg", "bid_security"],
            client=client,
        )

        instruction = client.instruction_sent
        self.assertIn("annual_turnover_avg", instruction)
        self.assertIn("bid_security", instruction)

    def test_an_established_key_returned_anyway_is_dropped_and_counted(self):
        """A silent drop would hide a model that ignores half of what it is told."""
        turnover = (
            "Minimum average annual turnover of USD 28,000,000.00 (twenty-eight "
            "million United States Dollars), calculated as total certified payments "
            "received for contracts in progress and/or completed within past three "
            "years (2023-2025), divided by three."
        )
        other = (
            "Participation as a contractor, joint venture member, management "
            "contractor, or subcontractor, in at least two (2) contracts each with a "
            "value of at least USD 22,400,000.00 (Twenty-two million and four hundred "
            "thousand United States Dollars) or one (1) contract with a value of at "
            "least USD 42,000,000.00 within the last ten (10) years (2016-2025), that "
            "have been successfully and substantially completed and that are similar "
            "to the proposed Plant and Installation Services."
        )
        client = FakeClient(
            response(
                answer(
                    requirement_row("annual_turnover_avg", turnover),
                    requirement_row("similar_contracts_count", other),
                )
            )
        )
        result = l2.extract(
            REAL_NOTICE,
            reference_year=2026,
            exclude_keys=["annual_turnover_avg"],
            client=client,
        )

        self.assertEqual([r.key for r in result.requirements], ["similar_contracts_count"])
        self.assertEqual(result.notes["excluded_keys_returned"], 1)

    def test_no_exclusions_means_no_exclusion_block(self):
        client = FakeClient(response(answer()))
        l2.extract(IRREGULAR_NOTICE, client=client)
        self.assertNotIn("ALREADY EXTRACTED", client.instruction_sent)


class SilenceTests(SimpleTestCase):
    """When the request is not worth making, it is not made."""

    def test_an_empty_body_spends_nothing(self):
        client = FakeClient()
        result = l2.extract("", client=client)

        self.assertEqual(client.calls, [])
        self.assertEqual(result.requirements, [])
        self.assertEqual(result.notes["skipped"], "empty notice body")

    def test_markup_with_no_words_in_it_spends_nothing(self):
        client = FakeClient()
        result = l2.extract("<p>&nbsp;</p><p></p>", client=client)
        self.assertEqual(client.calls, [])
        self.assertIn("skipped", result.notes)

    def test_an_announcement_that_states_no_condition_spends_nothing(self):
        """58% of the corpus. A request buys the correct answer at full price."""
        client = FakeClient()
        text = (
            "<p>The Government of the Kyrgyz Republic has received funding from the "
            "World Bank for the Regional Economic Development Project and intends to "
            "use part of the proceeds for consulting services.</p>"
            "<p>Further information can be provided at the address below during "
            "business hours (9:00 am - 6:00 pm) Monday through Friday.</p>"
        )
        result = l2.extract(text, client=client)

        self.assertEqual(client.calls, [])
        self.assertEqual(result.notes["skipped"], "no requirement language in the notice body")

    def test_a_notice_whose_every_criterion_was_already_read_spends_nothing(self):
        client = FakeClient()
        result = l2.extract(
            FULLY_READ_NOTICE,
            reference_year=2026,
            exclude_keys=["annual_turnover_avg", "bid_security"],
            client=client,
        )

        self.assertEqual(client.calls, [])
        self.assertIn("already read", result.notes["skipped"])

    def test_one_unread_criterion_is_enough_to_ask(self):
        """Coverage is per sentence, so a notice that says more still gets asked.

        The turnover paragraph is spoken for; the shortlisting paragraph is not,
        and it is the one no rule can read.
        """
        client = FakeClient(response(answer()))
        l2.extract(
            FULLY_READ_NOTICE + IRREGULAR_NOTICE,
            exclude_keys=["annual_turnover_avg", "bid_security"],
            client=client,
        )
        self.assertEqual(len(client.calls), 1)

    def test_a_skipped_layer_is_not_a_failed_layer(self):
        """Nothing went wrong, so nothing may be recorded as an error."""
        result = l2.extract("", client=FakeClient())
        self.assertTrue(result.ok)
        self.assertEqual(result.error, "")
        self.assertEqual(result.model, "")


class DegradationTests(SimpleTestCase):
    """Every way the call can fail arrives as a value, never as an exception."""

    def test_a_refusal_is_recorded_and_yields_nothing(self):
        client = FakeClient(response(None, stop_reason="refusal"))
        result = l2.extract(IRREGULAR_NOTICE, client=client)

        self.assertFalse(result.ok)
        self.assertIn("declined", result.error)
        self.assertEqual(result.requirements, [])

    def test_an_unparseable_answer_is_an_error_not_a_crash(self):
        client = FakeClient(response("{not json at all"))
        result = l2.extract(IRREGULAR_NOTICE, client=client)

        self.assertFalse(result.ok)
        self.assertEqual(result.requirements, [])

    def test_a_dead_connection_is_an_error_not_a_crash(self):
        result = l2.extract(IRREGULAR_NOTICE, client=RaisingClient())

        self.assertFalse(result.ok)
        self.assertIn("request failed", result.error)

    def test_a_row_with_no_quote_or_no_key_is_dropped(self):
        """A model that declines to claim is not a model that claimed wrongly."""
        client = FakeClient(
            response(
                answer(
                    requirement_row("years_experience", ""),
                    requirement_row("", YEARS_QUOTE),
                    {"key": "no_expression", "evidence_quote": YEARS_QUOTE},
                    requirement_row("years_experience", YEARS_QUOTE),
                )
            )
        )
        result = l2.extract(IRREGULAR_NOTICE, client=client)

        self.assertEqual([r.key for r in result.requirements], ["years_experience"])

    def test_an_answer_with_no_requirements_is_a_normal_answer(self):
        client = FakeClient(response(answer()))
        result = l2.extract(IRREGULAR_NOTICE, client=client)

        self.assertTrue(result.ok)
        self.assertEqual(result.requirements, [])
        self.assertEqual(result.notes["quotes_verified"], 0)


class BoundingTests(SimpleTestCase):
    """A quote can only be checked against text the model was actually shown."""

    LONG = IRREGULAR_NOTICE + "".join(
        f"<p>Lot {n} covers the supply and installation of display cases, lighting "
        f"tracks, and interpretation panels for the corresponding gallery.</p>"
        for n in range(400)
    )

    def test_a_body_within_budget_is_sent_whole(self):
        client = FakeClient(response(answer()))
        result = l2.extract(IRREGULAR_NOTICE, client=client)

        self.assertEqual(client.source_sent, canonical(IRREGULAR_NOTICE))
        self.assertEqual(result.notes["sentences_dropped"], 0)

    def test_a_long_body_is_cut_between_sentences_never_inside_one(self):
        """A half sentence would be copied faithfully and then fail grounding.

        That is the worst available failure: a correct extraction recorded as a
        hallucination, which moves the headline number in the wrong direction.
        """
        client = FakeClient(response(answer()))
        result = l2.extract(self.LONG, client=client)

        sent = client.source_sent
        self.assertLessEqual(len(sent), l2.MAX_SOURCE_CHARS)
        self.assertGreater(result.notes["sentences_dropped"], 0)
        # A prefix of the canonical text, ending where a sentence ended.
        self.assertTrue(canonical(self.LONG).startswith(sent))
        self.assertTrue(sent.endswith("."))

    def test_the_criteria_at_the_top_of_a_long_notice_still_reach_the_model(self):
        client = FakeClient(response(answer()))
        l2.extract(self.LONG, client=client)
        self.assertIn(YEARS_QUOTE, client.source_sent)

    def test_a_quote_from_a_sentence_that_was_never_sent_does_not_verify(self):
        """The guarantee bounding exists for, stated as a test.

        The sentence is in the notice, but past the budget — so the model could
        not have copied it, and a row carrying it is a claim about text nobody
        showed it.
        """
        tail = (
            "Lot 399 covers the supply and installation of display cases, lighting "
            "tracks, and interpretation panels for the corresponding gallery."
        )
        client = FakeClient(response(answer(requirement_row("display_cases", tail))))
        result = l2.extract(self.LONG, client=client)

        self.assertNotIn(tail, client.source_sent)
        self.assertEqual(result.notes["quotes_not_in_source"], 1)


class InstructionTests(SimpleTestCase):
    """What the per-notice half of the prompt commits the model to."""

    def test_the_publication_year_is_given_rather_than_left_to_today(self):
        """A 2019 notice dated from 2026 excludes every contract that qualified."""
        client = FakeClient(response(answer()))
        l2.extract(REAL_NOTICE, reference_year=2019, client=client)

        self.assertIn("published in 2019", client.instruction_sent)

    def test_without_a_publication_year_the_period_is_dropped_not_guessed(self):
        client = FakeClient(response(answer()))
        l2.extract(REAL_NOTICE, client=client)

        instruction = client.instruction_sent
        self.assertIn("omit the year condition", instruction)
        self.assertNotIn("published in", instruction)

    def test_a_printed_year_range_is_told_to_win(self):
        """The borrower already did the arithmetic; L1 obeys the same rule."""
        client = FakeClient(response(answer()))
        l2.extract(REAL_NOTICE, reference_year=2026, client=client)
        self.assertIn("(2016-2025)", client.instruction_sent)

    def test_the_same_inputs_produce_the_same_bytes(self):
        """A prompt version means nothing if the instruction wanders."""
        first = FakeClient(response(answer()))
        second = FakeClient(response(answer()))
        keys = ["bid_security", "annual_turnover_avg"]
        l2.extract(REAL_NOTICE, reference_year=2026, exclude_keys=keys, client=first)
        l2.extract(REAL_NOTICE, reference_year=2026, exclude_keys=reversed(keys), client=second)

        self.assertEqual(first.instruction_sent, second.instruction_sent)

    def test_the_system_prompt_stays_identical_across_notices(self):
        """The cached prefix pays for itself only if no byte of it moves."""
        client = FakeClient(response(answer()), response(answer()))
        l2.extract(IRREGULAR_NOTICE, client=client)
        l2.extract(REAL_NOTICE, reference_year=2026, client=client)

        self.assertEqual(client.calls[0]["system"], client.calls[1]["system"])
        self.assertEqual(
            client.calls[0]["system"][0]["cache_control"], {"type": "ephemeral"}
        )
