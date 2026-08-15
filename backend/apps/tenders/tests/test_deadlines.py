"""A wrong deadline is the most damaging bug this site can ship.

If the countdown says time remains after submission has closed, someone
prepares a bid they cannot file. These tests pin the conversion so a future
change to the timezone table cannot silently shift it.
"""

from __future__ import annotations

from datetime import datetime, time, timezone as dt_timezone

from django.test import TestCase
from django.utils import timezone

from apps.tenders.deadlines import (
    parse_local_time,
    resolve_deadline,
    timezone_for_country,
)
from apps.tenders.models import TenderNotice
from apps.tenders.serializers import TenderNoticeListSerializer


def _utc_midnight(year: int, month: int, day: int) -> datetime:
    """How `deadline_date` is stored: a date, pinned at midnight UTC."""
    return datetime(year, month, day, tzinfo=dt_timezone.utc)


class ParseLocalTimeTests(TestCase):
    def test_reads_the_shapes_upstream_actually_publishes(self):
        cases = {
            "17:00": time(17, 0),
            "09:30": time(9, 30),
            "23:45": time(23, 45),
            "17.00": time(17, 0),
            "1700": time(17, 0),
            "5:00 PM": time(17, 0),
            "5:00 pm": time(17, 0),
            "9:30 AM": time(9, 30),
        }
        for raw, expected in cases.items():
            with self.subTest(raw=raw):
                self.assertEqual(parse_local_time(raw), expected)

    def test_midnight_and_noon_meridiem_edges(self):
        # 12 AM is 00:00 and 12 PM is 12:00 — the one pair a naive
        # "add 12 for PM" rule gets wrong in both directions.
        self.assertEqual(parse_local_time("12:00 AM"), time(0, 0))
        self.assertEqual(parse_local_time("12:00 PM"), time(12, 0))

    def test_returns_none_rather_than_guessing(self):
        for raw in ["", "   ", "noon", "end of day", "TBA", None]:
            with self.subTest(raw=raw):
                self.assertIsNone(parse_local_time(raw))

    def test_rejects_impossible_clock_values(self):
        self.assertIsNone(parse_local_time("99:99"))


class TimezoneLookupTests(TestCase):
    def test_focus_region_is_exact(self):
        expected = {
            "Uzbekistan": "Asia/Tashkent",
            "Tajikistan": "Asia/Dushanbe",
            "Kyrgyz Republic": "Asia/Bishkek",
            "Azerbaijan": "Asia/Baku",
            "Armenia": "Asia/Yerevan",
            "Belarus": "Europe/Minsk",
            "Moldova": "Europe/Chisinau",
            "Afghanistan": "Asia/Kabul",
        }
        for country, zone in expected.items():
            with self.subTest(country=country):
                resolved = timezone_for_country(country)
                self.assertIsNotNone(resolved)
                self.assertEqual(resolved[0], zone)
                self.assertFalse(resolved[1], "focus region must not be approximate")

    def test_multi_zone_countries_are_flagged_approximate(self):
        for country in ["Russian Federation", "Kazakhstan"]:
            with self.subTest(country=country):
                resolved = timezone_for_country(country)
                self.assertIsNotNone(resolved)
                self.assertTrue(
                    resolved[1],
                    "a country spanning several zones must not claim precision",
                )

    def test_matching_ignores_case_and_padding(self):
        self.assertEqual(timezone_for_country("  uZbEkIsTaN "), ("Asia/Tashkent", False))

    def test_unknown_country_returns_none(self):
        self.assertIsNone(timezone_for_country("Atlantis"))
        self.assertIsNone(timezone_for_country(""))


class ResolveDeadlineTests(TestCase):
    def test_local_time_is_converted_to_the_right_instant(self):
        # 17:00 in Tashkent (UTC+5) is 12:00 UTC — not 17:00 UTC.
        resolved = resolve_deadline(_utc_midnight(2026, 8, 11), "17:00", "Uzbekistan")
        self.assertIsNotNone(resolved)
        self.assertEqual(
            resolved.at, datetime(2026, 8, 11, 12, 0, tzinfo=dt_timezone.utc)
        )
        self.assertEqual(resolved.local_time, "17:00")
        self.assertFalse(resolved.approximate)

    def test_daylight_saving_is_applied_not_a_fixed_offset(self):
        """Chisinau is UTC+3 in August and UTC+2 in January."""
        summer = resolve_deadline(_utc_midnight(2026, 8, 11), "17:00", "Moldova")
        winter = resolve_deadline(_utc_midnight(2026, 1, 14), "17:00", "Moldova")
        self.assertEqual(summer.at.hour, 14)
        self.assertEqual(winter.at.hour, 15)

    def test_missing_time_falls_to_end_of_day_not_midnight(self):
        """An unpublished time means the day is still open, not already over."""
        resolved = resolve_deadline(_utc_midnight(2026, 8, 11), "", "Uzbekistan")
        self.assertIsNotNone(resolved)
        # 23:59 Tashkent = 18:59 UTC on the same date.
        self.assertEqual(
            resolved.at, datetime(2026, 8, 11, 18, 59, tzinfo=dt_timezone.utc)
        )
        self.assertEqual(resolved.local_time, "", "no clock was published to show")

    def test_unknown_country_yields_no_instant(self):
        """Better to show whole days than a countdown we cannot justify."""
        self.assertIsNone(
            resolve_deadline(_utc_midnight(2026, 8, 11), "17:00", "Atlantis")
        )

    def test_missing_date_yields_no_instant(self):
        self.assertIsNone(resolve_deadline(None, "17:00", "Uzbekistan"))

    def test_multi_zone_country_resolves_but_admits_it(self):
        resolved = resolve_deadline(
            _utc_midnight(2026, 8, 11), "17:00", "Russian Federation"
        )
        self.assertIsNotNone(resolved)
        self.assertTrue(resolved.approximate)
        self.assertEqual(resolved.timezone, "Europe/Moscow")


class OneInstantTests(TestCase):
    """The API counts down to the instant the rest of the system filters on.

    Two derivations of one value is the shape every "the card and the panel
    disagree" bug takes here. ``deadline_at`` is written at the save choke
    point from the fields as they were handed in; the serializer used to
    re-derive it from the same fields read back out of Postgres, which is not
    the same input — a date written as midnight in a +05:00 zone comes back as
    19:00 the day before, and the two answers then name different days.
    """

    def _notice(self, **kwargs) -> TenderNotice:
        return TenderNotice.objects.create(
            notice_id="OP00456288",
            notice_type="Request for Expression of Interest",
            **{"country": "Uzbekistan", **kwargs},
        )

    def test_the_serialized_instant_is_the_stored_one(self):
        notice = self._notice(
            deadline_date=_utc_midnight(2026, 8, 28), deadline_time="23:45"
        )

        payload = TenderNoticeListSerializer(notice).data

        self.assertEqual(payload["deadline"]["at"], notice.deadline_at)
        self.assertEqual(payload["deadline"]["timezone"], "Asia/Tashkent")
        self.assertEqual(payload["deadline"]["local_time"], "23:45")

    def test_a_date_stored_off_midnight_cannot_split_the_two_answers(self):
        """The reproduction: the card said today, the countdown said tomorrow."""
        notice = self._notice(
            # Midnight on the 15th in Tashkent, which Postgres returns as
            # 19:00 on the 14th. Upstream writes midnight UTC, so this is a
            # shape a future writer could produce rather than one in the
            # mirror — and one derivation is what keeps it harmless.
            deadline_date=datetime(2026, 8, 14, 19, 0, tzinfo=dt_timezone.utc),
            deadline_time="16:59",
        )
        notice.refresh_from_db()

        payload = TenderNoticeListSerializer(notice).data

        self.assertEqual(payload["deadline"]["at"], notice.deadline_at)
        self.assertEqual(
            (payload["deadline"]["at"] - timezone.now()).days,
            notice.days_until_deadline,
            "the countdown and the card's day count must start from one instant",
        )

    def test_a_country_off_the_map_still_shows_no_clock(self):
        """A stored fallback instant is not a zone anyone can be told."""
        notice = self._notice(
            country="Narnia", deadline_date=_utc_midnight(2026, 8, 28)
        )

        self.assertIsNotNone(notice.deadline_at)
        self.assertIsNone(TenderNoticeListSerializer(notice).data["deadline"])
