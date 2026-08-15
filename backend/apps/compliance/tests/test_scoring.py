"""How ready a bid is, weighted by what each criterion decides.

A vendor answering criteria wants one number for how far they have got, and the
obvious number — the fraction of rows they have satisfied — is the one that
misleads at exactly the wrong moment: nine formalities met and the turnover gate
failed is not "90% ready". These cases cover the arithmetic that replaces it,
the two things it refuses to conflate (what is still open against what is
already lost), and the rule that the percentage never speaks over the verdict.

The rest cover the field the weighting reads — ``importance``, extracted from
the document's own language — and the labels a vendor reads it under.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from rest_framework.test import APITestCase

from apps.compliance import scoring
from apps.compliance.models import (
    ComplianceScore,
    ExtractionRun,
    TenderRequirement,
    VendorProfile,
)
from apps.tenders.models import TenderNotice

User = get_user_model()


class WeightTests(SimpleTestCase):
    """What one criterion contributes to the bar."""

    def test_a_gate_outweighs_a_preference(self):
        self.assertGreater(scoring.weight_of("high"), scoring.weight_of("low"))

    def test_an_unjudged_criterion_weighs_as_medium(self):
        """L1 reads none of the language that would say whether a criterion
        gates the bid, so it leaves the field blank. Blank must not mean
        weightless — the criterion is still one the tender states."""
        self.assertEqual(scoring.weight_of(""), scoring.weight_of("medium"))
        self.assertEqual(scoring.weight_of(None), scoring.weight_of("medium"))

    def test_a_level_nobody_defined_lands_on_the_default_not_on_zero(self):
        """Zero would drop the criterion out of both halves of the fraction —
        a requirement vanishing from a percentage shown to a vendor."""
        self.assertEqual(scoring.weight_of("critical"), scoring.weight_of("medium"))

    def test_one_missed_gate_is_not_offset_by_a_couple_of_preferences(self):
        """The property the ratios were chosen for. A bar that filled to most
        of the way on formalities while a gate went unmet would be telling a
        bidder the opposite of what they need to hear."""
        gate_only = scoring.score_rows(
            [("high", "unknown", True), ("low", "satisfied", False),
             ("low", "satisfied", False)]
        )

        self.assertLess(gate_only.score, 0.5)


class ScoreTests(SimpleTestCase):
    def test_the_fraction_is_over_weight_not_over_rows(self):
        """Half the rows answered is not half the tender met."""
        rows = [("high", "satisfied", True), ("low", "unknown", False)]

        score = scoring.score_rows(rows)

        # 5 of 6, not 1 of 2.
        self.assertEqual((score.earned, score.total), (5, 6))

    def test_only_a_satisfied_criterion_fills_the_bar(self):
        """Not "answered" — established. An unknown is a question still open
        and must not read as progress."""
        score = scoring.score_rows([("medium", "unknown", True)])

        self.assertEqual(score.score, 0.0)
        self.assertEqual(score.open, 3)

    def test_what_is_still_open_is_reported_apart_from_what_is_lost(self):
        """The gap to `ceiling` is the work left; the gap from `ceiling` to 1
        is what has already gone. One number cannot say both."""
        score = scoring.score_rows(
            [("high", "satisfied", True), ("high", "unknown", True),
             ("high", "failed", True)]
        )

        self.assertAlmostEqual(score.score, 5 / 15)
        self.assertAlmostEqual(score.ceiling, 10 / 15)

    def test_a_failed_criterion_can_never_be_recovered_by_answering_more(self):
        score = scoring.score_rows([("high", "failed", True), ("low", "unknown", False)])

        self.assertLess(score.ceiling, 1.0)

    def test_a_mandatory_failure_blocks_however_high_the_percentage(self):
        """A bid can be most of the way established and impossible. The
        interface has to be able to say both, so both are in the payload."""
        rows = [("low", "failed", True)] + [("high", "satisfied", True)] * 4

        score = scoring.score_rows(rows)

        self.assertGreater(score.score, 0.9)
        self.assertTrue(score.blocked)

    def test_a_failed_preference_does_not_block(self):
        """It is a real verdict worth showing and it stops nobody bidding."""
        score = scoring.score_rows([("low", "failed", False)])

        self.assertFalse(score.blocked)

    def test_nothing_extracted_scores_zero_rather_than_a_vacuous_hundred(self):
        """Every criterion of none is satisfied, arithmetically. Printing that
        as "100% ready" on a tender nobody has read is the single most
        misleading thing this page could do."""
        score = scoring.score_rows([])

        self.assertEqual(score.score, 0.0)
        self.assertEqual(score.ceiling, 0.0)
        self.assertEqual(score.total, 0)

    def test_the_weights_behind_the_fraction_are_published(self):
        """The claim is that a verdict can be recomputed by hand. A percentage
        with no numerator is a number that has to be taken on trust."""
        payload = scoring.score_rows([("high", "satisfied", True)]).as_dict()

        self.assertEqual(payload["earned"] + payload["open"] + payload["lost"],
                         payload["total"])

    def test_the_split_per_level_says_which_half_is_missing(self):
        """"You have answered the formalities and none of the gates" is the
        sentence a single bar cannot say."""
        score = scoring.score_rows(
            [("high", "unknown", True), ("low", "satisfied", False)]
        )

        self.assertEqual(score.by_importance["high"]["earned"], 0)
        self.assertEqual(score.by_importance["low"]["earned"], 1)


class ImportanceOrderTests(TestCase):
    """What a vendor is shown first."""

    def setUp(self):
        self.notice = TenderNotice.objects.create(notice_id="OP-ORD", country="Uzbekistan")
        self.run = ExtractionRun.objects.create(
            notice=self.notice, layers="L2", status=ExtractionRun.Status.OK
        )

    def _requirement(self, key: str, importance: str, **extra) -> TenderRequirement:
        return TenderRequirement.objects.create(
            notice=self.notice,
            run=self.run,
            layer=TenderRequirement.Layer.L2,
            key=key,
            importance=importance,
            expression={"kind": "scalar", "key": key, "op": ">=", "value": 1},
            evidence_quote=f"The bidder shall have {key}.",
            grounding=TenderRequirement.Grounding.VERIFIED,
            **extra,
        )

    def test_what_decides_the_bid_comes_first(self):
        from apps.compliance.views import _requirement_rows

        self._requirement("a_preference", "low")
        self._requirement("an_ordinary_one", "medium")
        self._requirement("a_gate", "high")

        rows, _ = _requirement_rows(self.notice)

        self.assertEqual([row.key for row in rows],
                         ["a_gate", "an_ordinary_one", "a_preference"])

    def test_an_unjudged_row_sorts_where_its_weight_puts_it(self):
        """A criterion must not appear above the gates while counting for less
        than them."""
        from apps.compliance.views import _requirement_rows

        self._requirement("a_gate", "high")
        self._requirement("unjudged", "")
        self._requirement("a_preference", "low")

        rows, _ = _requirement_rows(self.notice)

        self.assertEqual([row.key for row in rows],
                         ["a_gate", "unjudged", "a_preference"])


class LabelLanguageTests(TestCase):
    """The criterion in the reader's language; the quote in the document's."""

    def setUp(self):
        notice = TenderNotice.objects.create(notice_id="OP-LANG", country="Uzbekistan")
        run = ExtractionRun.objects.create(
            notice=notice, layers="L2", status=ExtractionRun.Status.OK
        )
        self.requirement = TenderRequirement.objects.create(
            notice=notice,
            run=run,
            layer=TenderRequirement.Layer.L2,
            key="annual_turnover_avg",
            label="Average annual turnover",
            label_uz="O'rtacha yillik aylanma",
            label_ru="Среднегодовой оборот",
            expression={"kind": "scalar", "key": "annual_turnover_avg",
                        "op": ">=", "value": 1000000},
            evidence_quote="Average annual turnover of USD 1,000,000.",
        )

    def test_each_language_gets_its_own_label(self):
        self.assertEqual(self.requirement.label_for("uz"), "O'rtacha yillik aylanma")
        self.assertEqual(self.requirement.label_for("ru"), "Среднегодовой оборот")
        self.assertEqual(self.requirement.label_for("en"), "Average annual turnover")

    def test_an_untranslated_row_falls_back_to_english_rather_than_blank(self):
        """English is what every source document is written in, so the
        fallback shows the criterion as the tender itself words it — worse to
        read, impossible to misunderstand."""
        self.requirement.label_uz = ""

        self.assertEqual(self.requirement.label_for("uz"), "Average annual turnover")

    def test_a_row_with_no_label_at_all_falls_back_to_its_key(self):
        self.requirement.label = ""
        self.requirement.label_uz = ""

        self.assertEqual(self.requirement.label_for("uz"), "annual_turnover_avg")


class ScoreEndpointTests(APITestCase):
    """The number the vendor's page reads, and where it is kept."""

    def setUp(self):
        self.user = User.objects.create_user(
            username="v@example.com", email="v@example.com", password="pass-12345"
        )
        self.profile = VendorProfile.objects.create(user=self.user, name="Acme")
        self.client.force_login(self.user)

        self.notice = TenderNotice.objects.create(notice_id="OP-SC", country="Uzbekistan")
        run = ExtractionRun.objects.create(
            notice=self.notice, layers="L2", status=ExtractionRun.Status.OK
        )
        self.gate = TenderRequirement.objects.create(
            notice=self.notice,
            run=run,
            layer=TenderRequirement.Layer.L2,
            key="registration",
            label="National registration",
            label_uz="Milliy ro'yxatdan o'tish",
            importance=TenderRequirement.Importance.HIGH,
            expression={"kind": "scalar", "key": "registration", "op": "==", "value": True},
            evidence_quote="The bidder shall be registered.",
            grounding=TenderRequirement.Grounding.VERIFIED,
        )
        self.preference = TenderRequirement.objects.create(
            notice=self.notice,
            run=run,
            layer=TenderRequirement.Layer.L2,
            key="iso_14001",
            label="ISO 14001",
            importance=TenderRequirement.Importance.LOW,
            expression={"kind": "scalar", "key": "iso_14001", "op": "==", "value": True},
            evidence_quote="ISO 14001 certification is desirable.",
            grounding=TenderRequirement.Grounding.VERIFIED,
        )
        self.assessment_url = reverse("compliance:notice-assessment", args=["OP-SC"])
        self.declarations_url = reverse("compliance:notice-declarations", args=["OP-SC"])

    def test_the_assessment_carries_the_weighted_figure(self):
        payload = self.client.post(self.assessment_url, format="json").json()

        self.assertEqual(payload["score"]["score"], 0.0)
        self.assertEqual(payload["score"]["total"], 6)

    def test_answering_a_gate_moves_the_bar_further_than_a_preference(self):
        """The whole reason the percentage is weighted."""
        self.client.post(
            self.declarations_url,
            [{"requirement_id": self.preference.pk, "satisfied": True}],
            format="json",
        )
        after_preference = self.client.post(self.assessment_url, format="json").json()

        self.client.post(
            self.declarations_url,
            [{"requirement_id": self.gate.pk, "satisfied": True}],
            format="json",
        )
        after_gate = self.client.post(self.assessment_url, format="json").json()

        self.assertAlmostEqual(after_preference["score"]["score"], 1 / 6, places=4)
        self.assertAlmostEqual(after_gate["score"]["score"], 1.0)

    def test_the_switch_gets_the_new_figure_in_the_same_response(self):
        """The indicator answers the control. A second request to find out what
        the press did would leave the bar lagging behind the switch."""
        response = self.client.post(
            self.declarations_url,
            [{"requirement_id": self.gate.pk, "satisfied": True}],
            format="json",
        )

        self.assertAlmostEqual(response.json()["score"]["score"], 5 / 6, places=4)

    def test_the_figure_is_kept_so_a_list_can_be_ordered_by_it(self):
        self.client.post(self.assessment_url, format="json")

        stored = ComplianceScore.objects.get(profile=self.profile, notice=self.notice)
        self.assertEqual(stored.total, 6)
        self.assertEqual(stored.requirements, 2)

    def test_the_cache_is_rewritten_rather_than_appended_to(self):
        """It is a copy of a derivable number; a history of it would be a
        history of how often somebody opened the page."""
        for _ in range(3):
            self.client.post(self.assessment_url, format="json")

        self.assertEqual(ComplianceScore.objects.count(), 1)

    def test_the_criterion_is_named_in_the_language_the_reader_asked_for(self):
        payload = self.client.post(
            self.assessment_url, format="json", HTTP_ACCEPT_LANGUAGE="uz"
        ).json()

        labels = [row["label"] for row in payload["requirements"]]
        self.assertIn("Milliy ro'yxatdan o'tish", labels)

    def test_the_quote_stays_in_the_document_s_own_language(self):
        """A translated quote is an altered quote: the grounding verifier
        searches for it in the source, and it would not be found."""
        payload = self.client.post(
            self.assessment_url, format="json", HTTP_ACCEPT_LANGUAGE="uz"
        ).json()

        quotes = [row["evidence_quote"] for row in payload["requirements"]]
        self.assertIn("The bidder shall be registered.", quotes)
