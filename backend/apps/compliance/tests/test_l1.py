"""L1 reads what the notice states, and states nothing the notice does not."""

from django.test import SimpleTestCase

from apps.compliance import l1
from apps.compliance.expressions import AppliesTo, Bid, Portfolio, Verdict
from apps.compliance.text import canonical, contains_quote, sentences

# The qualification paragraph of OP00456288 (Uzbekistan, modernisation of four
# 220 kV substations, deadline September 2026), copied from the notice body.
# Real text rather than a constructed fixture: the phrasing quirks it carries —
# footnote markers, the doubled numerals, the amount written twice — are the
# thing the rules have to survive.
REAL_NOTICE = (
    "<p>The Bidders&rsquo; qualification requirements include: The qualification "
    "requirements for bidders, including General Experience, Specific Experience, "
    "Financial Capabilities, and other requirements set out in Section III of the "
    "RFB, shall apply.</p>"
    "<p>Specific Experience: Participation as a contractor, joint venture "
    "member[1], management contractor, or subcontractor, in at least two (2) "
    "contracts each with a value of at least USD 22,400,000.00 (Twenty-two million "
    "and four hundred thousand United States Dollars) or one (1) contract with a "
    "value of at least USD 42,000,000.00 within the last ten (10) years "
    "(2016-2025), that have been successfully and substantially completed and that "
    "are similar to the proposed Plant and Installation Services.</p>"
    "<p>Minimum average annual turnover of USD 28,000,000.00 (twenty-eight million "
    "United States Dollars), calculated as total certified payments received for "
    "contracts in progress and/or completed within past three years (2023-2025), "
    "divided by three.</p>"
    "<p>Access to, or availability of, financial resources such as liquid assets, "
    "unencumbered real assets, lines of credit, and other financial means to meet: "
    "(i) the following cash-flow requirement: USD 5,600,000.00</p>"
    "<p>All Bids must be accompanied by a Bid Security of USD 280,000.00 (Two "
    "hundred and eighty thousand United States Dollars).</p>"
)


class CanonicalTextTests(SimpleTestCase):
    def test_character_entities_are_resolved(self):
        """A quote carrying &rsquo; can never be found in the source PDF."""
        self.assertEqual(canonical("the Bidders&rsquo; documents"), "the Bidders' documents")

    def test_markup_is_removed(self):
        self.assertEqual(canonical("<p>Bid Security of <strong>USD 1</strong></p>"),
                         "Bid Security of USD 1.")

    def test_paragraph_breaks_end_a_sentence(self):
        """Without this every quote is the whole notice.

        Notice paragraphs routinely carry no closing full stop, so the split has
        to come from the markup or not at all.
        """
        text = canonical("<p>First clause</p><p>Second clause</p>")
        self.assertEqual(len(sentences(text)), 2)

    def test_footnote_markers_do_not_break_a_phrase(self):
        self.assertIn("joint venture member,", canonical("joint venture member[1],"))


class RealNoticeTests(SimpleTestCase):
    """Every figure the notice states, read back exactly."""

    def setUp(self):
        self.requirements = {
            r.key: r for r in l1.extract(REAL_NOTICE, reference_year=2026)
        }

    def test_reads_all_four_stated_criteria(self):
        self.assertEqual(
            set(self.requirements),
            {"similar_contracts_count", "annual_turnover_avg", "liquid_assets", "bid_security"},
        )

    def test_similar_contracts_keeps_count_floor_and_window(self):
        """The criterion that motivated the expression tree, read whole.

        A bare count would drop the value floor and the ten-year window, and
        then pass a vendor whose two contracts are a tenth of the threshold.
        """
        payload = self.requirements["similar_contracts_count"].expression.to_dict()
        self.assertEqual(payload["value"], 2)
        self.assertIn({"field": "value_usd", "op": ">=", "value": 22400000.0}, payload["where"])
        self.assertIn({"field": "completed_year", "op": ">=", "value": 2016}, payload["where"])

    def test_per_contract_floor_is_the_lower_of_the_two_alternatives(self):
        """"two contracts of 22.4M, or one of 42M" — the count read is two."""
        payload = self.requirements["similar_contracts_count"].expression.to_dict()
        floors = [f["value"] for f in payload["where"] if f["field"] == "value_usd"]
        self.assertEqual(floors, [22400000.0])

    def test_turnover_and_liquid_assets_are_read_as_thresholds(self):
        self.assertEqual(self.requirements["annual_turnover_avg"].expression.value, 28000000.0)
        self.assertEqual(self.requirements["liquid_assets"].expression.value, 5600000.0)

    def test_explicit_year_range_wins_over_arithmetic(self):
        """The borrower already did the subtraction; its answer is authoritative.

        The notice says both "last ten (10) years" and "(2016-2025)". Against a
        2026 reference year the arithmetic gives 2017, and the notice gives 2016.
        """
        payload = self.requirements["similar_contracts_count"].expression.to_dict()
        floor = [f["value"] for f in payload["where"] if f["field"] == "completed_year"]
        self.assertEqual(floor, [2016])

    def test_bid_security_is_a_preference_not_a_gate(self):
        """No source says a bid security must be covered by any balance item.

        It is extracted because a vendor needs the number, and left
        non-mandatory because gating on it would be an invented rule.
        """
        self.assertFalse(self.requirements["bid_security"].is_mandatory)

    def test_every_quote_is_findable_in_the_source(self):
        """The grounding contract, checked on the layer that should never fail it.

        L1 copies its quotes out of the same canonical string it matched
        against, so a failure here means the two sides have drifted apart —
        which is precisely the bug that would report correct extractions as
        hallucinations.
        """
        for key, requirement in self.requirements.items():
            with self.subTest(key=key):
                self.assertTrue(contains_quote(REAL_NOTICE, requirement.evidence_quote))

    def test_a_quote_carries_the_condition_not_only_the_number(self):
        quote = self.requirements["annual_turnover_avg"].evidence_quote
        self.assertIn("average annual turnover", quote.lower())
        self.assertIn("28,000,000", quote)


class SilenceTests(SimpleTestCase):
    """What L1 declines to say."""

    def test_a_notice_that_defers_to_section_iii_yields_nothing(self):
        """The requirements exist, but not in this document.

        Roughly three notices in four look like this, and the empty answer is
        the correct one: the thresholds are in the bidding document, and no
        amount of rule-writing over the notice can recover what the notice does
        not say. Reading that document is L3's job — including when the vendor
        has to ask the published contact for it (D17).
        """
        text = (
            "<p>The qualification requirements are set out in Section III of the "
            "bidding document, which may be obtained on written request.</p>"
        )
        self.assertEqual(l1.extract(text), [])

    def test_a_figure_with_no_criterion_attached_is_not_a_requirement(self):
        text = "<p>The estimated contract value is USD 15,000,000.</p>"
        self.assertEqual(l1.extract(text), [])

    def test_time_window_is_dropped_rather_than_computed_against_today(self):
        """A 2019 notice evaluated against 2026 would exclude qualifying work."""
        text = (
            "<p>Participation in at least two (2) contracts each of at least USD "
            "1,000,000 within the last five years.</p>"
        )
        [requirement] = l1.extract(text, reference_year=None)
        fields = [f["field"] for f in requirement.expression.to_dict()["where"]]
        self.assertNotIn("completed_year", fields)

    def test_local_currency_is_read_but_never_converted(self):
        """Which year's rate, from which source, is an open question."""
        text = "<p>Minimum average annual turnover of UZS 5,000,000,000.</p>"
        [requirement] = l1.extract(text)
        self.assertEqual(requirement.expression.unit, "UZS")
        self.assertEqual(requirement.expression.value, 5000000000.0)

    def test_a_criterion_stated_twice_is_returned_once(self):
        text = (
            "<p>Minimum average annual turnover of USD 10,000,000.</p>"
            "<p>Bidders must show an average annual turnover of USD 10,000,000.</p>"
        )
        self.assertEqual(len(l1.extract(text)), 1)


class EndToEndTests(SimpleTestCase):
    """What the extracted requirements do once the engine has them."""

    def test_a_vendor_short_on_turnover_is_told_which_criterion_blocks_them(self):
        requirements = l1.extract(REAL_NOTICE, reference_year=2026)
        bid = Bid([
            Portfolio(
                name="Kichik Qurilish MChJ",
                scalars={"annual_turnover_avg": 4_000_000, "liquid_assets": 6_000_000},
                collections={"contracts": []},
            )
        ])
        results = {r.key: res for r, res in
                   (( q, q.evaluate(bid)) for q in requirements)}

        self.assertIs(results["annual_turnover_avg"].verdict, Verdict.FAILED)
        self.assertIs(results["liquid_assets"].verdict, Verdict.SATISFIED)
        # Declared "no contracts" is a real failure, not an unknown.
        self.assertIs(results["similar_contracts_count"].verdict, Verdict.FAILED)
        # Never asked about, never held against them.
        self.assertIs(results["bid_security"].verdict, Verdict.UNKNOWN)

    def test_an_undeclared_figure_is_unknown_not_a_failure(self):
        requirements = {r.key: r for r in l1.extract(REAL_NOTICE, reference_year=2026)}
        bid = Bid([Portfolio(name="Hech narsa aytmagan", scalars={}, collections={})])
        verdict = requirements["annual_turnover_avg"].evaluate(bid).verdict
        self.assertIs(verdict, Verdict.UNKNOWN)

    def test_requirements_survive_a_round_trip_through_storage(self):
        """What is written to the database must evaluate identically."""
        from apps.compliance.expressions import parse_requirement

        for original in l1.extract(REAL_NOTICE, reference_year=2026):
            with self.subTest(key=original.key):
                restored = parse_requirement(original.to_dict())
                self.assertEqual(restored.to_dict(), original.to_dict())

    def test_applies_to_defaults_to_single_because_the_matrix_is_elsewhere(self):
        """The JV column lives in Section III, which this document does not carry."""
        for requirement in l1.extract(REAL_NOTICE, reference_year=2026):
            self.assertIs(requirement.applies_to, AppliesTo.SINGLE)


class ContinuationTests(SimpleTestCase):
    """A clause whose figure sits in the paragraph below it."""

    CONTINUED = (
        "<p>All Bids must be accompanied by a Bid Security in the amount of:</p>"
        "<p>USD 150,000.00</p>"
    )

    def test_a_colon_carries_the_amount_forward(self):
        """"in the amount of:" with the sum below is a recurring real shape."""
        [requirement] = l1.extract(self.CONTINUED)
        self.assertEqual(requirement.key, "bid_security")
        self.assertEqual(requirement.expression.value, 150000.0)

    def test_the_quote_carries_both_halves(self):
        [requirement] = l1.extract(self.CONTINUED)
        self.assertIn("Bid Security", requirement.evidence_quote)
        self.assertIn("150,000", requirement.evidence_quote)
        self.assertTrue(contains_quote(self.CONTINUED, requirement.evidence_quote))

    def test_a_figure_below_is_not_taken_without_a_colon(self):
        """Only punctuation that states continuation may join two fragments.

        Otherwise the rule reaches forward and pairs a criterion with whatever
        number appears next — here, the contract value.
        """
        text = (
            "<p>A Bid Security is required as specified in the bidding document.</p>"
            "<p>The estimated contract value is USD 9,000,000.</p>"
        )
        self.assertEqual(l1.extract(text), [])

    def test_a_declaration_with_no_amount_yields_nothing(self):
        """Half the misses in the corpus are this, and they are correct misses."""
        text = "<p>All bids must be supported with a Declaration of Bid Security.</p>"
        self.assertEqual(l1.extract(text), [])
