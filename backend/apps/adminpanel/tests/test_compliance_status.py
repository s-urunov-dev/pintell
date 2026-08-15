"""The console's view of the automatic extraction.

The numbers here are what an operator acts on: whether the schedule is on,
which layers it can afford, how much of the live set has been read, and what
the last few runs produced. Each of those has a way of being subtly wrong —
counting a closed tender, counting a run twice, showing a model name when no
key is configured — so each is asserted rather than the shape as a whole.
"""

from __future__ import annotations

from datetime import timedelta

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.compliance.models import (
    ExtractionRun,
    TenderExpertPosition,
    TenderRequirement,
)
from apps.experts.models import ExpertType
from apps.tenders.models import TenderNotice

User = get_user_model()

NO_THROTTLE = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
}

AUTO_ON = {"AUTO_EXTRACT": True, "AUTO_BATCH_SIZE": 25}
NO_KEY = {"ENABLED": True, "API_KEY": "", "MODEL": "claude-opus-5"}


@override_settings(REST_FRAMEWORK=NO_THROTTLE, COMPLIANCE=AUTO_ON, ANTHROPIC=NO_KEY)
class ComplianceStatusTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user(
            username="operator", password="operator-pass-123", is_staff=True
        )
        self.client.force_login(self.staff)
        self.url = reverse("adminpanel:admin-compliance")

        now = timezone.now()
        self.open_notice = TenderNotice.objects.create(
            notice_id="OP-LIVE-1",
            notice_type="Request for Expression of Interest",
            country="Uzbekistan",
            notice_date=now.date() - timedelta(days=1),
            deadline_date=now + timedelta(days=5),
            notice_text_sanitized="Average annual turnover of US$ 1,000,000.",
        )
        # Closed: same shape, deadline behind us.
        TenderNotice.objects.create(
            notice_id="OP-DEAD-1",
            notice_type="Request for Expression of Interest",
            country="Uzbekistan",
            notice_date=now.date() - timedelta(days=90),
            deadline_date=now - timedelta(days=2),
            notice_text_sanitized="Average annual turnover of US$ 1,000,000.",
        )

    def test_the_denominator_is_the_live_set_not_the_mirror(self):
        """A closed tender in the count would make the queue look permanent."""
        body = self.client.get(self.url).json()

        self.assertEqual(body["active_notices"], 1)
        self.assertEqual(body["active_pending"], 1)
        self.assertEqual(body["active_read"], 0)

    def test_a_read_tender_moves_from_pending_to_read(self):
        ExtractionRun.objects.create(
            notice=self.open_notice, layers="L1", status=ExtractionRun.Status.OK
        )

        body = self.client.get(self.url).json()

        self.assertEqual(body["active_read"], 1)
        self.assertEqual(body["active_pending"], 0)

    def test_a_failed_run_does_not_count_as_read(self):
        """Otherwise a broken deployment reports full coverage."""
        ExtractionRun.objects.create(
            notice=self.open_notice,
            layers="L1",
            status=ExtractionRun.Status.FAILED,
            error="boom",
        )

        body = self.client.get(self.url).json()

        self.assertEqual(body["active_read"], 0)
        self.assertEqual(body["runs_failed"], 1)

    def test_an_ungrounded_requirement_is_not_counted_as_a_result(self):
        """`active_with_requirements` must mean usable ones, not stored ones."""
        run = ExtractionRun.objects.create(
            notice=self.open_notice, layers="L1", status=ExtractionRun.Status.OK
        )
        TenderRequirement.objects.create(
            notice=self.open_notice,
            run=run,
            layer=TenderRequirement.Layer.L1,
            key="annual_turnover_avg",
            label="Turnover",
            expression={"kind": "scalar", "key": "annual_turnover_avg", "op": ">=", "value": 1},
            evidence_quote="not in the source",
            grounding=TenderRequirement.Grounding.NOT_FOUND,
        )

        body = self.client.get(self.url).json()

        self.assertEqual(body["active_with_requirements"], 0)

    def test_without_a_key_it_reports_the_free_layer_and_names_no_model(self):
        body = self.client.get(self.url).json()

        self.assertEqual(body["layers"], "L1")
        self.assertFalse(body["model_available"])
        self.assertEqual(body["model"], "")

    @override_settings(
        ANTHROPIC={"ENABLED": True, "API_KEY": "sk-test", "MODEL": "claude-haiku-4-5"}
    )
    def test_with_a_key_it_reports_the_whole_stack_and_the_model(self):
        body = self.client.get(self.url).json()

        self.assertEqual(body["layers"], "L1,L2,L3")
        self.assertTrue(body["model_available"])
        self.assertEqual(body["model"], "claude-haiku-4-5")

    def test_recent_runs_show_what_each_produced(self):
        run = ExtractionRun.objects.create(
            notice=self.open_notice, layers="L1", status=ExtractionRun.Status.OK
        )
        TenderRequirement.objects.create(
            notice=self.open_notice,
            run=run,
            layer=TenderRequirement.Layer.L1,
            key="annual_turnover_avg",
            label="Turnover",
            expression={"kind": "scalar", "key": "annual_turnover_avg", "op": ">=", "value": 1},
            evidence_quote="Average annual turnover of US$ 1,000,000.",
            grounding=TenderRequirement.Grounding.VERIFIED,
        )

        body = self.client.get(self.url).json()

        self.assertEqual(len(body["recent_runs"]), 1)
        self.assertEqual(body["recent_runs"][0]["requirements"], 1)
        self.assertEqual(body["recent_runs"][0]["notice_id"], "OP-LIVE-1")

    def test_the_console_is_staff_only(self):
        self.client.logout()
        self.assertEqual(
            self.client.get(self.url).status_code, status.HTTP_403_FORBIDDEN
        )


WITH_KEY = {"ENABLED": True, "API_KEY": "sk-test", "MODEL": "claude-haiku-4-5"}


@override_settings(REST_FRAMEWORK=NO_THROTTLE, COMPLIANCE=AUTO_ON, ANTHROPIC=WITH_KEY)
class QueueDepthTests(APITestCase):
    """The queue is measured at the depth the button reads, not at any depth.

    These are the numbers an operator presses a button on, so each one is
    asserted against the state that used to make it lie: a corpus read at L1 in
    a deployment that now runs the whole stack reported 24 of 24 read and a
    button that would have read 24.
    """

    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user(
            username="operator", password="operator-pass-123", is_staff=True
        )
        self.client.force_login(self.staff)
        self.url = reverse("adminpanel:admin-compliance")

        now = timezone.now()
        self.notice = TenderNotice.objects.create(
            notice_id="OP-LIVE-2",
            notice_type="Request for Expression of Interest",
            country="Uzbekistan",
            notice_date=now.date() - timedelta(days=1),
            deadline_date=now + timedelta(days=5),
            notice_text_sanitized="Average annual turnover of US$ 1,000,000.",
        )

    def _run(self, layers, status_value, *, times=1):
        for _ in range(times):
            ExtractionRun.objects.create(
                notice=self.notice, layers=layers, status=status_value
            )

    def test_a_tender_read_only_at_l1_is_pending_once_the_stack_runs_deeper(self):
        self._run("L1", ExtractionRun.Status.OK)

        body = self.client.get(self.url).json()

        self.assertEqual(body["layers"], "L1,L2,L3")
        self.assertEqual(body["active_read"], 0)
        self.assertEqual(body["active_pending"], 1)
        self.assertEqual(body["active_stalled"], 0)

    def test_a_tender_read_at_this_depth_is_read_and_the_queue_is_empty(self):
        self._run("L1,L2,L3", ExtractionRun.Status.OK)

        body = self.client.get(self.url).json()

        self.assertEqual(body["active_read"], 1)
        self.assertEqual(body["active_pending"], 0)
        self.assertEqual(body["active_stalled"], 0)

    def test_a_tender_past_the_retry_cap_is_stalled_rather_than_pending(self):
        """A queue that has stopped draining must say why on the screen."""
        self._run("L1,L2,L3", ExtractionRun.Status.FAILED, times=3)

        body = self.client.get(self.url).json()

        self.assertEqual(body["active_read"], 0)
        self.assertEqual(body["active_pending"], 0)
        self.assertEqual(body["active_stalled"], 1)

    def test_a_tender_that_failed_twice_is_still_offered(self):
        self._run("L1,L2,L3", ExtractionRun.Status.FAILED, times=2)

        body = self.client.get(self.url).json()

        self.assertEqual(body["active_pending"], 1)
        self.assertEqual(body["active_stalled"], 0)


@override_settings(REST_FRAMEWORK=NO_THROTTLE, COMPLIANCE=AUTO_ON, ANTHROPIC=NO_KEY)
class ExpertPositionReportingTests(APITestCase):
    """A run that found a team and no threshold is a result, not a blank."""

    fixtures = ["expert_types"]

    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user(
            username="operator", password="operator-pass-123", is_staff=True
        )
        self.client.force_login(self.staff)
        self.url = reverse("adminpanel:admin-compliance")

        now = timezone.now()
        self.notice = TenderNotice.objects.create(
            notice_id="OP-TEAM-1",
            notice_type="Request for Expression of Interest",
            country="Uzbekistan",
            notice_date=now.date() - timedelta(days=1),
            deadline_date=now + timedelta(days=5),
            notice_text_sanitized="The team shall include a Team Leader.",
        )
        self.run = ExtractionRun.objects.create(
            notice=self.notice, layers="L2", status=ExtractionRun.Status.OK
        )

    def _position(self, title="Team Leader", **overrides):
        fields = {
            "notice": self.notice,
            "run": self.run,
            "layer": "L2",
            "title": title,
            "role": ExpertType.objects.filter(slug="team-leader").first(),
            "evidence_quote": "The team shall include a Team Leader.",
            "grounding": TenderRequirement.Grounding.VERIFIED,
        }
        fields.update(overrides)
        return TenderExpertPosition.objects.create(**fields)

    def _requirement(self, key):
        return TenderRequirement.objects.create(
            notice=self.notice,
            run=self.run,
            layer="L2",
            key=key,
            expression={"kind": "scalar", "key": key, "op": ">=", "value": 1},
            evidence_quote="The team shall include a Team Leader.",
            grounding=TenderRequirement.Grounding.VERIFIED,
        )

    def test_a_run_reports_its_positions_beside_its_requirements(self):
        self._position()

        body = self.client.get(self.url).json()

        self.assertEqual(body["recent_runs"][0]["expert_positions"], 1)
        self.assertEqual(body["recent_runs"][0]["requirements"], 0)

    def test_two_counts_over_two_relations_do_not_multiply_each_other(self):
        """Without ``distinct`` the join reports 6 of each instead of 3 and 2."""
        for key in ("a", "b", "c"):
            self._requirement(key)
        self._position("Team Leader")
        self._position("M&E Specialist")

        run = self.client.get(self.url).json()["recent_runs"][0]

        self.assertEqual(run["requirements"], 3)
        self.assertEqual(run["expert_positions"], 2)

    def test_the_live_set_counts_tenders_naming_a_team(self):
        self._position()

        body = self.client.get(self.url).json()

        self.assertEqual(body["active_with_experts"], 1)
        self.assertEqual(body["active_with_requirements"], 0)

    def test_an_ungrounded_position_is_not_counted_as_a_result(self):
        self._position(grounding=TenderRequirement.Grounding.NOT_FOUND)

        body = self.client.get(self.url).json()

        self.assertEqual(body["active_with_experts"], 0)
