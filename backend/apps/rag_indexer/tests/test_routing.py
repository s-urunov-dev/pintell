"""Which model a question goes to, and what the model is shown.

Two properties are worth pinning above all the individual rules. The first is
the **direction of the default**: anything the router does not recognise goes
to the capable tier, because the cost of the other mistake is an answer that
reads worse in a way no schema catches. The second is that **compression never
touches a figure** — the prompt tells the model to quote amounts exactly as the
source writes them, and a compressor that normalised them would make that
instruction unfollowable.
"""

from __future__ import annotations

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from apps.rag_indexer.services import routing

#: A deployment that has actually opted into a cheap tier. Without this the
#: router reports `deep` for everything, which is the shipped default and is
#: tested separately below.
TIERED = {
    **settings.ANTHROPIC,
    "MODEL": "claude-opus-5",
    "CHAT_MODEL": "",
    "CHAT_MODEL_FAST": "claude-sonnet-5",
    "CHAT_MODEL_DEEP": "",
}


@override_settings(ANTHROPIC=TIERED)
class Routing(SimpleTestCase):
    """The rules, in the order they are allowed to fire."""

    def test_a_scoped_lookup_takes_the_cheap_tier(self):
        chosen = routing.route("Aloqa uchun pochta qanday?", notice_id="OP-1")

        self.assertEqual(chosen.tier, "fast")
        self.assertEqual(chosen.model, "claude-sonnet-5")

    def test_an_analysis_word_beats_a_lookup_word(self):
        """One question can ask for both. The harder half decides."""
        chosen = routing.route(
            "What is the deadline and how does it compare with the others?",
            notice_id="OP-1",
        )

        self.assertEqual(chosen.tier, "deep")
        self.assertEqual(chosen.reason, "analysis:word")

    def test_an_unscoped_lookup_is_not_a_field_lookup(self):
        """"What are the deadlines" across the archive is a list, not a field."""
        chosen = routing.route("What is the deadline?")

        self.assertEqual(chosen.tier, "deep")
        self.assertEqual(chosen.reason, "analysis:unscoped")

    def test_a_long_question_is_complex_whatever_it_says(self):
        question = " ".join(["muddat"] * 30)

        self.assertEqual(routing.route(question, notice_id="OP-1").reason, "analysis:length")

    def test_an_unrecognised_question_goes_to_the_capable_tier(self):
        chosen = routing.route("Bu tender bo'yicha nimalarni bilishim kerak?")

        self.assertEqual(chosen.tier, "deep")
        self.assertEqual(chosen.reason, "analysis:default")

    def test_a_keyword_matches_as_a_word_and_not_as_a_substring(self):
        """D39's lesson, carried: `risk` inside `brisk` is not a risk question."""
        chosen = routing.route("Is the brisk delivery schedule confirmed here?", notice_id="OP-1")

        self.assertNotEqual(chosen.reason, "analysis:word")

    def test_russian_and_uzbek_are_read_too(self):
        self.assertEqual(
            routing.route("Сравните требования по обороту").reason, "analysis:word"
        )
        self.assertEqual(
            routing.route("Ushbu tenderlarni taqqoslang").reason, "analysis:word"
        )


class RoutingWithoutAFastTier(SimpleTestCase):
    """The shipped default: one tier, and the router says so honestly."""

    def test_a_lookup_still_goes_deep_when_no_fast_model_is_configured(self):
        with override_settings(ANTHROPIC={**TIERED, "CHAT_MODEL_FAST": ""}):
            chosen = routing.route("Contact email?", notice_id="OP-1")

        self.assertEqual(chosen.tier, "deep")
        self.assertEqual(chosen.model, "claude-opus-5")
        # The rule that fired is still reported, so "nothing was routed" and
        # "everything was classified as complex" stay distinguishable.
        self.assertEqual(chosen.reason, "lookup:word:no_fast_model")


class Compression(SimpleTestCase):
    """What comes out of a passage before the model reads it."""

    def test_figures_and_currency_survive_exactly(self):
        text = "<p>average annual turnover of   USD 22.4 million</p>"

        compressed = routing.compress(text, max_chars=200)

        self.assertIn("USD 22.4 million", compressed)
        self.assertNotIn("<p>", compressed)

    def test_runs_of_whitespace_collapse_but_paragraphs_do_not(self):
        text = "First line.\n\nSecond    line."

        compressed = routing.compress(text, max_chars=200)

        self.assertEqual(compressed, "First line.\n\nSecond line.")

    def test_a_passage_that_opens_with_its_own_title_prints_it_once(self):
        text = "Road rehabilitation works. The bidder shall demonstrate turnover."

        compressed = routing.compress(
            text, max_chars=200, title="Road rehabilitation works"
        )

        self.assertEqual(compressed, "The bidder shall demonstrate turnover.")

    def test_an_over_budget_passage_is_cut_on_a_word_and_says_so(self):
        text = "word " * 200

        compressed = routing.compress(text, max_chars=50)

        self.assertLessEqual(len(compressed), 52)
        self.assertTrue(compressed.endswith("…"))
        self.assertFalse(compressed.endswith("wor …"))

    def test_zero_width_and_control_characters_go(self):
        text = "USD​ 22.4\x0c million"

        self.assertEqual(routing.compress(text, max_chars=100), "USD 22.4 million")
