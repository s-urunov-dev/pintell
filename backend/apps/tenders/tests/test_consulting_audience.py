"""Firms vs individual consultants — the audience axis of a consulting notice."""

from __future__ import annotations

from django.test import SimpleTestCase, TestCase

from apps.tenders.categories import TenderCategory
from apps.tenders.consulting import ConsultingAudience, classify_audience
from apps.tenders.models import TenderNotice
from apps.tenders.services.ai.classification import ClassificationResult, apply_classification
from apps.tenders.categories import CategorySource


class ClassifyAudienceTests(SimpleTestCase):
    def test_the_method_name_is_what_identifies_an_individual_selection(self):
        guess = classify_audience(
            category=TenderCategory.CONSULTING,
            procurement_method_code="INDV",
            procurement_method_name="Individual Consultant Selection",
        )
        self.assertEqual(guess.audience, ConsultingAudience.INDIVIDUAL)

    def test_an_unknown_code_still_reads_as_individual_when_named_so(self):
        """The name carries the meaning, so a new code must not lose it."""
        guess = classify_audience(
            category=TenderCategory.CONSULTING,
            procurement_method_code="XYZ",
            procurement_method_name="Selection of Individual Consultants",
        )
        self.assertEqual(guess.audience, ConsultingAudience.INDIVIDUAL)

    def test_firm_selection_methods_are_labelled_for_firms(self):
        for code in ("CQS", "QCBS", "QBS", "LCS", "FBS"):
            with self.subTest(code=code):
                guess = classify_audience(
                    category=TenderCategory.CONSULTING, procurement_method_code=code
                )
                self.assertEqual(guess.audience, ConsultingAudience.FIRM)

    def test_a_method_open_to_both_audiences_stays_unanswered(self):
        """Direct Selection can engage either, so it must not claim one."""
        guess = classify_audience(
            category=TenderCategory.CONSULTING,
            procurement_method_code="CDS",
            procurement_method_name="Direct Selection",
        )
        self.assertEqual(guess.audience, ConsultingAudience.UNKNOWN)
        self.assertIn("both", guess.rationale)

    def test_non_consulting_directions_carry_no_audience(self):
        for category in (TenderCategory.SUPPLY, TenderCategory.CONSTRUCTION):
            with self.subTest(category=category):
                guess = classify_audience(
                    category=category, procurement_method_code="RFB"
                )
                self.assertEqual(guess.audience, ConsultingAudience.UNKNOWN)


class AudienceIsDerivedWithTheCategoryTests(TestCase):
    """The audience is written by the same call that writes the direction.

    Deriving it anywhere else would let a notice's direction and its audience
    describe different classifications of the same row.
    """

    def test_classifying_a_consulting_notice_also_sets_its_audience(self):
        notice = TenderNotice.objects.create(
            notice_id="OP100",
            notice_type="Request for Expression of Interest",
            bid_description="Recruitment of an individual consultant",
            procurement_method_code="INDV",
            procurement_method_name="Individual Consultant Selection",
        )

        apply_classification(
            notice,
            ClassificationResult(
                category=TenderCategory.CONSULTING,
                confidence=0.9,
                source=CategorySource.RULES,
            ),
        )

        notice.refresh_from_db()
        self.assertEqual(notice.consulting_audience, ConsultingAudience.INDIVIDUAL)

    def test_a_supply_notice_is_left_without_an_audience(self):
        notice = TenderNotice.objects.create(
            notice_id="OP101",
            notice_type="Invitation for Bids",
            bid_description="Supply of laboratory equipment",
            procurement_method_code="RFB",
            procurement_method_name="Request for Bids",
        )

        apply_classification(
            notice,
            ClassificationResult(
                category=TenderCategory.SUPPLY,
                confidence=0.8,
                source=CategorySource.RULES,
            ),
        )

        notice.refresh_from_db()
        self.assertEqual(notice.consulting_audience, ConsultingAudience.UNKNOWN)
