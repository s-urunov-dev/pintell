"""A quote is trusted only when it is found, and a miss is kept as a number."""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.compliance import grounding
from apps.compliance.models import TenderRequirement

#: One paragraph of a real notice body, in the form the column actually holds it:
#: HTML with character entities. Grounding has to survive both.
SOURCE = (
    "<p>All Bids must be accompanied by a Bid Security of USD 280,000.00.</p>"
    "<p>The Bidders&rsquo; qualification requirements include a minimum average "
    "annual turnover of USD 28,000,000.00 over the past three years.</p>"
)


class VerifyTests(SimpleTestCase):
    def test_a_quote_copied_from_the_source_verifies(self):
        state = grounding.verify(
            "a Bid Security of USD 280,000.00", SOURCE, layer="L1"
        )
        self.assertEqual(state, grounding.VERIFIED)

    def test_markup_and_entities_do_not_break_a_real_quote(self):
        """The quote and the source are canonicalised by the same function.

        Without that, every quote containing an apostrophe would be reported as
        a hallucination — the failure ``text.canonical`` exists to prevent.
        """
        state = grounding.verify(
            "The Bidders' qualification requirements include", SOURCE, layer="L2"
        )
        self.assertEqual(state, grounding.VERIFIED)

    def test_a_paraphrase_is_not_grounded(self):
        state = grounding.verify(
            "bidders must post a bid security of about 280 thousand dollars",
            SOURCE,
            layer="L2",
        )
        self.assertEqual(state, grounding.NOT_FOUND)

    def test_a_number_the_source_does_not_contain_is_not_grounded(self):
        """The case the measurement exists for: a plausible, invented figure."""
        state = grounding.verify(
            "a Bid Security of USD 380,000.00", SOURCE, layer="L2"
        )
        self.assertEqual(state, grounding.NOT_FOUND)

    def test_a_missing_quote_fails_rather_than_defers(self):
        """No quote is a contract violation, not an unfinished check."""
        self.assertEqual(grounding.verify("", SOURCE, layer="L1"), grounding.NOT_FOUND)

    def test_a_missing_source_fails_rather_than_defers(self):
        """Unverifiable must not read as usable.

        ``unchecked`` would be the accurate word and the dangerous one: those
        rows count as usable and would reach a bidder's verdict unchecked.
        """
        self.assertEqual(
            grounding.verify("a Bid Security", "", layer="L2"), grounding.NOT_FOUND
        )

    def test_no_layer_may_opt_itself_out_of_being_checked(self):
        """Dropping L0 (D17) left nothing whose evidence we do not hold."""
        self.assertEqual(grounding.EXEMPT_LAYERS, frozenset())

    def test_the_states_are_the_ones_the_column_stores(self):
        """The module keeps plain strings; they may never drift from the enum."""
        self.assertEqual(grounding.VERIFIED, TenderRequirement.Grounding.VERIFIED.value)
        self.assertEqual(grounding.NOT_FOUND, TenderRequirement.Grounding.NOT_FOUND.value)
        self.assertEqual(grounding.UNCHECKED, TenderRequirement.Grounding.UNCHECKED.value)


class GroundingRateTests(SimpleTestCase):
    def test_the_rate_is_verified_over_what_was_actually_checked(self):
        rate = grounding.rate_over(
            [grounding.VERIFIED] * 9 + [grounding.NOT_FOUND]
        )
        self.assertEqual(rate.checked, 10)
        self.assertEqual(rate.rate, 0.9)

    def test_unanswered_rows_do_not_inflate_the_rate(self):
        """A row nobody checked must not count as a pass."""
        rate = grounding.rate_over(
            [grounding.VERIFIED, grounding.NOT_FOUND] + [grounding.UNCHECKED] * 50
        )
        self.assertEqual(rate.checked, 2)
        self.assertEqual(rate.rate, 0.5)
        self.assertEqual(rate.total, 52)
        self.assertEqual(rate.total, 52)

    def test_nothing_checked_has_no_rate_rather_than_a_zero(self):
        rate = grounding.rate_over([grounding.UNCHECKED, grounding.UNCHECKED])
        self.assertIsNone(rate.rate)
        self.assertIsNone(rate.as_dict()["rate"])

    def test_the_counts_travel_with_the_ratio(self):
        """94% over eleven rows and over eleven hundred are different claims."""
        payload = grounding.rate_over([grounding.VERIFIED, grounding.NOT_FOUND]).as_dict()
        self.assertEqual(payload["verified"], 1)
        self.assertEqual(payload["not_found"], 1)
        self.assertEqual(payload["checked"], 2)
        self.assertEqual(payload["rate"], 0.5)
