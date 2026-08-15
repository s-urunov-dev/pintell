"""Direction classification: rules first, Claude only where rules are unsure."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

from django.test import SimpleTestCase, TestCase, override_settings

from apps.tenders.categories import (
    CategorySource,
    TenderCategory,
    classify_by_rules,
)
from apps.tenders.models import TenderNotice
from apps.tenders.services.ai import classification


class RuleClassifierTests(SimpleTestCase):
    def test_construction_from_description(self):
        guess = classify_by_rules(
            description="Construction of new WWTP, sewerage networks and pumping stations"
        )
        self.assertEqual(guess.category, TenderCategory.CONSTRUCTION)
        self.assertEqual(guess.source, CategorySource.RULES)

    def test_supply_from_description(self):
        guess = classify_by_rules(
            description="Procurement of office and laboratory furniture for the Institute"
        )
        self.assertEqual(guess.category, TenderCategory.SUPPLY)

    def test_consulting_from_individual_consultant_method(self):
        guess = classify_by_rules(
            description="Senior Consultant - Waste Management Policy",
            procurement_method_code="INDV",
        )
        self.assertEqual(guess.category, TenderCategory.CONSULTING)

    def test_it_beats_supply_when_technology_dominates(self):
        guess = classify_by_rules(
            description="Supply and Installation of Enterprise Backup & Recovery Software Solution"
        )
        self.assertEqual(guess.category, TenderCategory.IT)

    def test_procurement_group_is_a_signal(self):
        guess = classify_by_rules(description="Assignment", procurement_group="CS")
        self.assertEqual(guess.category, TenderCategory.CONSULTING)

    def test_unclassifiable_text_stays_unknown(self):
        guess = classify_by_rules(description="Lot 3")
        self.assertEqual(guess.category, TenderCategory.UNKNOWN)
        self.assertEqual(guess.confidence, 0.0)

    def test_confidence_is_bounded(self):
        guess = classify_by_rules(
            description="Construction of a bridge; civil works; rehabilitation of road works"
        )
        self.assertGreater(guess.confidence, 0)
        self.assertLessEqual(guess.confidence, 1.0)


class GroupOverrulesTheKeywordsTests(SimpleTestCase):
    """Where the source's own code and our guess disagree, the code wins."""

    def test_a_works_contract_is_not_consulting_because_it_mentions_an_audit(self):
        """`OP00460089`, which is why this rule exists.

        Pump supply and installation, tendered as civil works, classified
        `consulting` on the phrase *energy audit* — and then shown at the top
        of a real audit tender's competitor panel.
        """
        guess = classify_by_rules(
            description=(
                "The detailed energy audit, supply, and installation of "
                "distribution pumps and boosters"
            ),
            procurement_group="CW",
        )
        self.assertEqual(guess.category, TenderCategory.CONSTRUCTION)
        self.assertIn("overruled", guess.rationale)

    def test_a_consultancy_to_supervise_construction_stays_consulting(self):
        """The same rule in the other direction, and the commoner case."""
        guess = classify_by_rules(
            description="Consulting Services for Construction Supervision of road works",
            procurement_group="CS",
        )
        self.assertEqual(guess.category, TenderCategory.CONSULTING)

    def test_it_survives_a_goods_code_rather_than_collapsing_into_supply(self):
        """Computer equipment really is procured as goods.

        `it` is a direction vendors subscribe to, and 223 mirrored notices
        reach it from a `GO` group. The code speaks about three directions and
        this is not one of them.
        """
        guess = classify_by_rules(
            description="Supply and Installation of Enterprise Backup & Recovery Software Solution",
            procurement_group="GO",
        )
        self.assertEqual(guess.category, TenderCategory.IT)

    def test_the_non_consulting_code_still_only_hints(self):
        """`NC` agrees with the keywords on 49% of the mirror — see Q19.

        Until that question is answered it may not overrule anything, so a
        notice the keywords read as construction stays construction.
        """
        guess = classify_by_rules(
            description="Construction of a bridge; civil works; earthworks",
            procurement_group="NC",
        )
        self.assertEqual(guess.category, TenderCategory.CONSTRUCTION)

    def test_an_overruled_guess_reports_the_confidence_of_what_was_chosen(self):
        """Not of the category that was rejected — that number described
        evidence for an answer nobody is giving."""
        guess = classify_by_rules(
            description="The detailed energy audit of the pumping station",
            procurement_group="CW",
        )
        self.assertEqual(guess.category, TenderCategory.CONSTRUCTION)
        self.assertLess(guess.confidence, 0.5)


class ClassifyNoticeTests(TestCase):
    def setUp(self):
        self.notice = TenderNotice.objects.create(
            notice_id="OP00000001",
            notice_type="Invitation for Bids",
            bid_description="Construction of rural roads in Samarkand region",
            country="Uzbekistan",
        )

    def test_rules_only_when_ai_is_disabled(self):
        result = classification.classify_notice(self.notice, use_ai=False)
        self.assertEqual(result.category, TenderCategory.CONSTRUCTION)
        self.assertEqual(result.source, CategorySource.RULES)

    def test_apply_writes_the_result_onto_the_notice(self):
        result = classification.classify_notice(self.notice, use_ai=False)
        classification.apply_classification(self.notice, result)

        self.notice.refresh_from_db()
        self.assertEqual(self.notice.category, TenderCategory.CONSTRUCTION)
        self.assertEqual(self.notice.category_source, CategorySource.RULES)
        self.assertIsNotNone(self.notice.category_updated_at)

    @patch("apps.tenders.services.ai.classification.get_client")
    @patch("apps.tenders.services.ai.classification.classification_enabled", return_value=True)
    def test_ai_is_skipped_when_rules_are_confident(self, _enabled, get_client):
        classification.classify_notice(self.notice)
        get_client.assert_not_called()

    @patch("apps.tenders.services.ai.classification.get_client")
    @patch("apps.tenders.services.ai.classification.classification_enabled", return_value=True)
    def test_ai_is_used_for_ambiguous_notices(self, _enabled, get_client):
        vague = TenderNotice.objects.create(
            notice_id="OP00000002", notice_type="Invitation for Bids",
            bid_description="Lot 2", country="Armenia",
        )
        get_client.return_value = _fake_client(
            '{"category": "supply", "confidence": 0.82, "rationale": "Goods delivery."}'
        )

        result = classification.classify_notice(vague)

        self.assertEqual(result.category, TenderCategory.SUPPLY)
        self.assertEqual(result.source, CategorySource.AI)
        self.assertAlmostEqual(result.confidence, 0.82)

    @patch("apps.tenders.services.ai.classification.get_client")
    @patch("apps.tenders.services.ai.classification.classification_enabled", return_value=True)
    def test_model_failure_falls_back_to_rules(self, _enabled, get_client):
        vague = TenderNotice.objects.create(
            notice_id="OP00000003", notice_type="Invitation for Bids",
            bid_description="Procurement of desks", country="Armenia",
        )
        get_client.side_effect = RuntimeError("rate limited")

        result = classification.classify_notice(vague, force=True)

        self.assertEqual(result.source, CategorySource.RULES)
        self.assertEqual(result.category, TenderCategory.SUPPLY)

    @patch("apps.tenders.services.ai.classification.get_client")
    @patch("apps.tenders.services.ai.classification.classification_enabled", return_value=True)
    def test_refusal_falls_back_to_rules(self, _enabled, get_client):
        vague = TenderNotice.objects.create(
            notice_id="OP00000004", notice_type="Invitation for Bids",
            bid_description="Supply of vehicles", country="Armenia",
        )
        client = _fake_client("{}")
        client.messages.create.return_value.stop_reason = "refusal"
        get_client.return_value = client

        result = classification.classify_notice(vague, force=True)
        self.assertEqual(result.source, CategorySource.RULES)

    @patch("apps.tenders.services.ai.classification.get_client")
    @patch("apps.tenders.services.ai.classification.classification_enabled", return_value=True)
    def test_unknown_category_from_model_falls_back(self, _enabled, get_client):
        vague = TenderNotice.objects.create(
            notice_id="OP00000005", notice_type="Invitation for Bids",
            bid_description="Supply of vehicles", country="Armenia",
        )
        get_client.return_value = _fake_client(
            '{"category": "spaceflight", "confidence": 1, "rationale": "n/a"}'
        )

        result = classification.classify_notice(vague, force=True)
        self.assertEqual(result.source, CategorySource.RULES)

    def test_classify_pending_only_touches_the_focus_feed(self):
        from datetime import timedelta

        from django.utils import timezone

        TenderNotice.objects.create(
            notice_id="OP00000010",
            notice_type="Invitation for Bids",
            country="Uzbekistan",
            deadline_date=timezone.now() + timedelta(days=5),
            bid_description="Construction of a school building",
        )
        TenderNotice.objects.create(
            notice_id="OP00000011",  # outside the region — must stay unknown
            notice_type="Invitation for Bids",
            country="Kenya",
            deadline_date=timezone.now() + timedelta(days=5),
            bid_description="Construction of a clinic",
        )

        counters = classification.classify_pending(use_ai=False)

        self.assertEqual(counters["by_rules"], 1)
        self.assertEqual(
            TenderNotice.objects.get(pk="OP00000011").category, TenderCategory.UNKNOWN
        )


@override_settings(ANTHROPIC={**{}, "ENABLED": True, "API_KEY": ""})
class ReclassifyTests(TestCase):
    """A rule change is worth nothing until the rows it fixes are re-read."""

    def _notice(self, notice_id: str, **overrides) -> TenderNotice:
        fields = {
            "notice_type": "Contract Award",
            "bid_description": "The detailed energy audit, supply and installation of pumps",
            "procurement_group": "CW",
            "country": "Uzbekistan",
            "category": TenderCategory.CONSULTING,
            "category_source": CategorySource.RULES,
        }
        return TenderNotice.objects.create(notice_id=notice_id, **{**fields, **overrides})

    def test_a_notice_the_old_rules_got_wrong_is_corrected(self):
        self._notice("OP1")

        result = classification.reclassify_by_rules(limit=10)

        self.assertEqual(result["changed"], 1)
        self.assertEqual(
            TenderNotice.objects.get(notice_id="OP1").category,
            TenderCategory.CONSTRUCTION,
        )

    def test_a_manual_override_is_left_alone(self):
        """Someone decided that by hand; a keyword table moving is not a
        reason to undo it."""
        self._notice("OP1", category_source=CategorySource.MANUAL)

        classification.reclassify_by_rules(limit=10)

        self.assertEqual(
            TenderNotice.objects.get(notice_id="OP1").category,
            TenderCategory.CONSULTING,
        )

    def test_an_ai_classification_is_left_alone_too(self):
        """It was paid for, and it read the body rather than a keyword list."""
        self._notice("OP1", category_source=CategorySource.AI)

        classification.reclassify_by_rules(limit=10)

        self.assertEqual(
            TenderNotice.objects.get(notice_id="OP1").category,
            TenderCategory.CONSULTING,
        )

    def test_losing_the_signal_does_not_throw_a_direction_away(self):
        """`unknown` is not an improvement on an answer already recorded."""
        self._notice("OP1", bid_description="Lot 3", procurement_group="")

        classification.reclassify_by_rules(limit=10)

        self.assertEqual(
            TenderNotice.objects.get(notice_id="OP1").category,
            TenderCategory.CONSULTING,
        )


class AiAvailabilityTests(SimpleTestCase):
    def test_missing_key_disables_ai(self):
        from apps.tenders.services.ai.client import ai_enabled

        self.assertFalse(ai_enabled())

    @override_settings(
        ANTHROPIC={"ENABLED": True, "API_KEY": "sk-test", "CLASSIFY_ENABLED": False}
    )
    def test_a_key_alone_does_not_switch_the_classifier_on(self):
        """The key is bought for compliance; the classifier has its own schedule."""
        from apps.tenders.services.ai.client import ai_enabled, classification_enabled

        self.assertTrue(ai_enabled())
        self.assertFalse(classification_enabled())

    @override_settings(
        ANTHROPIC={"ENABLED": True, "API_KEY": "", "CLASSIFY_ENABLED": True}
    )
    def test_asking_for_the_classifier_without_a_key_still_runs_on_rules(self):
        from apps.tenders.services.ai.client import classification_enabled

        self.assertFalse(classification_enabled())


def _fake_client(json_text: str) -> MagicMock:
    """Minimal stand-in for the Anthropic client used by the classifier."""
    block = MagicMock()
    block.type = "text"
    block.text = json_text

    response = MagicMock()
    response.stop_reason = "end_turn"
    response.content = [block]

    client = MagicMock()
    client.messages.create.return_value = response
    return client
