"""What the scheduler reads, and — mostly — what it refuses to read.

The extraction itself is covered by `test_pipeline` and the layer tests. What
matters here is the selection: an archive of 25,000 notices sits behind the
same pipeline, and the difference between reading thirty of them and reading
all of them is the difference between a demo and a bill.

No model and no network: with no API key the stack runs L1 only, which is a
regular expression over the notice body, so these tests exercise the real task
rather than a stand-in for it.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.compliance import tasks
from apps.compliance.models import ExtractionRun
from apps.tenders.models import TenderNotice

#: Carries a turnover figure L1's rules recognise, so a notice built from it
#: produces a requirement rather than an empty run.
BODY = (
    "The Consultant shall demonstrate an average annual turnover of "
    "US$ 4,000,000 over the last three years."
)

AUTO_ON = {"AUTO_EXTRACT": True, "AUTO_BATCH_SIZE": 25}


def make_notice(notice_id: str, **overrides) -> TenderNotice:
    """Build a notice the scheduler would consider.

    ``deadline_at`` is passed for readability and is then **discarded**:
    ``TenderNotice.save`` re-derives it from ``deadline_date`` + ``deadline_time``
    + ``country`` on every write, always. Setting it here does not move the
    closing instant, and a test that believes it does is a test about the wrong
    thing — which is exactly how the case below came to depend on the hour of
    the day. To place the deadline, set ``deadline_date`` (and freeze the clock).
    """
    now = timezone.now()
    fields = {
        "notice_id": notice_id,
        "notice_type": "Request for Expression of Interest",
        "country": "Uzbekistan",
        "notice_date": now.date() - timedelta(days=3),
        "deadline_date": now + timedelta(days=10),
        "deadline_at": now + timedelta(days=10),
        "notice_text_sanitized": BODY,
    }
    fields.update(overrides)
    return TenderNotice.objects.create(**fields)


@override_settings(COMPLIANCE=AUTO_ON)
class WhatTheScheduleReads(TestCase):
    def test_an_open_tender_is_read(self):
        make_notice("OP-OPEN-1")

        result = tasks.extract_active_requirements()

        self.assertEqual(result["active_notices"], 1)
        self.assertEqual(result["runs"], 1)
        self.assertEqual(ExtractionRun.objects.count(), 1)

    def test_a_closed_tender_is_never_read(self):
        """Its criteria are history — nobody can bid on it, at any price."""
        now = timezone.now()
        make_notice(
            "OP-CLOSED-1",
            deadline_date=now - timedelta(days=1),
            deadline_at=now - timedelta(days=1),
        )

        result = tasks.extract_active_requirements()

        self.assertEqual(result["active_notices"], 0)
        self.assertFalse(ExtractionRun.objects.exists())

    @mock.patch("django.utils.timezone.now")
    def test_a_tender_closing_later_today_is_still_read(self, now):
        """The case `deadline_date` alone got wrong: bidding closes this evening.

        The clock is frozen, for the same reason it is frozen in
        `tenders.test_api.test_closing_today_counts_tonight_and_not_the_rest_of_the_week`:
        the assertion is about the window between midnight and the real closing
        instant, so a real clock decides whether it passes.

        It decided badly. `deadline_at` cannot be set from a fixture —
        `TenderNotice.save` re-derives it — so the notice below closes at the
        end of its day in Tashkent, which is 18:59 UTC. Read at 09:00 the
        tender is open; read at 20:00 it is closed and the assertion failed,
        every evening, on a suite that had nothing to do with it.
        """
        now.return_value = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)

        make_notice(
            "OP-TODAY-1",
            # Midnight of the closing day — already in the past — while the
            # deadline the notice is actually judged on is ten hours away.
            deadline_date=datetime(2026, 8, 9, 0, 0, tzinfo=UTC),
        )

        result = tasks.extract_active_requirements()

        self.assertEqual(result["active_notices"], 1)

    def test_a_notice_dated_in_the_future_waits(self):
        """Reading a tender the borrower has not announced yet is not our call."""
        make_notice("OP-FUTURE-1", notice_date=timezone.now().date() + timedelta(days=2))

        self.assertEqual(tasks.extract_active_requirements()["active_notices"], 0)

    def test_an_award_notice_is_not_an_opportunity(self):
        make_notice("OP-AWARD-1", notice_type="Contract Award")

        self.assertEqual(tasks.extract_active_requirements()["active_notices"], 0)

    def test_a_tender_outside_the_focus_group_is_not_read(self):
        make_notice("OP-OUT-1", country="India")

        self.assertEqual(tasks.extract_active_requirements()["active_notices"], 0)

    def test_a_second_cycle_does_not_pay_for_the_same_notice_again(self):
        """The beat entry runs every half hour; it must be a no-op by default."""
        make_notice("OP-OPEN-2")

        first = tasks.extract_active_requirements()
        second = tasks.extract_active_requirements()

        self.assertEqual(first["runs"], 1)
        self.assertEqual(second["runs"], 0)
        self.assertEqual(ExtractionRun.objects.count(), 1)

    def test_the_batch_size_bounds_one_cycle(self):
        for index in range(4):
            make_notice(f"OP-BATCH-{index}")

        with override_settings(COMPLIANCE={"AUTO_EXTRACT": True, "AUTO_BATCH_SIZE": 2}):
            result = tasks.extract_active_requirements()

        self.assertEqual(result["active_notices"], 4)
        self.assertEqual(result["runs"], 2)


class WhenItIsSwitchedOff(TestCase):
    @override_settings(COMPLIANCE={"AUTO_EXTRACT": False, "AUTO_BATCH_SIZE": 25})
    def test_nothing_runs_and_the_result_says_so(self):
        make_notice("OP-OFF-1")

        result = tasks.extract_active_requirements()

        self.assertEqual(result, {"enabled": False})
        self.assertFalse(ExtractionRun.objects.exists())


@override_settings(COMPLIANCE=AUTO_ON)
class WhichLayersItAsksFor(TestCase):
    @override_settings(ANTHROPIC={"ENABLED": True, "API_KEY": "", "MODEL": "claude-haiku-4-5"})
    def test_without_a_key_it_asks_only_for_the_free_layer(self):
        make_notice("OP-LAYERS-1")

        result = tasks.extract_active_requirements()

        self.assertEqual(result["layers"], "L1")

    @override_settings(
        ANTHROPIC={"ENABLED": True, "API_KEY": "sk-test", "MODEL": "claude-haiku-4-5"}
    )
    def test_with_a_key_it_asks_for_the_whole_stack_without_being_told(self):
        """Configuring a key is the instruction; no second switch to remember."""
        make_notice("OP-LAYERS-2")

        result = tasks.extract_active_requirements()

        self.assertEqual(result["layers"], "L1,L2,L3")
