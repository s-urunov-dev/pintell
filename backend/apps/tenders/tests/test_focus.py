"""The stage-1 aggregation: open opportunities, focus region, two notice types."""

from __future__ import annotations

from datetime import date, timedelta

from django.core.cache import cache
from django.test import TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.tenders.models import TenderNotice
from apps.tenders.regions import (
    CIS_PLUS_COUNTRIES,
    canonical_country,
    group_countries,
    is_in_group,
)


class CountryGroupTests(TestCase):
    def test_group_contains_cis_states_and_afghanistan(self):
        names = set(group_countries("cis_plus"))
        for expected in ("Uzbekistan", "Kazakhstan", "Armenia", "Afghanistan"):
            self.assertIn(expected, names)
        self.assertEqual(len(names), len(CIS_PLUS_COUNTRIES))

    def test_unknown_group_is_empty_not_everything(self):
        self.assertEqual(group_countries("atlantis"), [])

    def test_upstream_spellings_are_resolved(self):
        # Upstream writes "Kyrgyz Republic" and "Russian Federation".
        self.assertEqual(canonical_country("Kyrgyzstan"), "Kyrgyz Republic")
        self.assertEqual(canonical_country("Russia"), "Russian Federation")
        self.assertTrue(is_in_group("Kyrgyzstan"))
        self.assertFalse(is_in_group("Kenya"))


class FocusQuerySetTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        # Matches every focus rule.
        cls.wanted = TenderNotice.objects.create(
            notice_id="OP1",
            notice_type="Request for Expression of Interest",
            country="Uzbekistan",
            deadline_date=now + timedelta(days=10),
            notice_date=date(2026, 7, 1),
        )
        TenderNotice.objects.create(
            notice_id="OP2",  # right type + region, deadline already passed
            notice_type="Invitation for Bids",
            country="Kazakhstan",
            deadline_date=now - timedelta(days=1),
        )
        TenderNotice.objects.create(
            notice_id="OP3",  # right region + open, but an award notice
            notice_type="Contract Award",
            country="Uzbekistan",
            deadline_date=now + timedelta(days=10),
        )
        TenderNotice.objects.create(
            notice_id="OP4",  # right type + open, outside the region
            notice_type="Invitation for Bids",
            country="Kenya",
            deadline_date=now + timedelta(days=10),
        )
        TenderNotice.objects.create(
            notice_id="OP5",  # right type + region, but no deadline at all
            notice_type="Invitation for Bids",
            country="Armenia",
        )

    def test_focus_keeps_only_actionable_regional_opportunities(self):
        self.assertEqual(
            list(TenderNotice.objects.focus().values_list("notice_id", flat=True)),
            ["OP1"],
        )

    def test_actionable_ignores_region(self):
        ids = set(TenderNotice.objects.actionable().values_list("notice_id", flat=True))
        self.assertEqual(ids, {"OP1", "OP4"})

    def test_country_group_filter_covers_the_whole_region(self):
        ids = set(
            TenderNotice.objects.in_country_group().values_list("notice_id", flat=True)
        )
        self.assertEqual(ids, {"OP1", "OP2", "OP3", "OP5"})


class FocusApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        TenderNotice.objects.create(
            notice_id="OP1",
            notice_type="Request for Expression of Interest",
            country="Uzbekistan",
            deadline_date=now + timedelta(days=5),
            notice_date=date(2026, 7, 1),
            category="consulting",
        )
        TenderNotice.objects.create(
            notice_id="OP2",
            notice_type="Invitation for Bids",
            country="Kenya",
            deadline_date=now + timedelta(days=5),
            notice_date=date(2026, 7, 2),
            category="construction",
        )
        TenderNotice.objects.create(
            notice_id="OP3",
            notice_type="Contract Award",
            country="Uzbekistan",
            notice_date=date(2026, 6, 1),
        )

    def setUp(self):
        # /facets, /stats and the paginated counts are all cached; keep runs
        # independent of each other.
        cache.clear()

    def test_focus_query_param(self):
        response = self.client.get(reverse("tenders:tender-list"), {"focus": "true"})
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual([row["id"] for row in response.data["results"]], ["OP1"])

    def test_focus_false_returns_the_complement(self):
        response = self.client.get(reverse("tenders:tender-list"), {"focus": "false"})
        ids = {row["id"] for row in response.data["results"]}
        self.assertEqual(ids, {"OP2", "OP3"})

    def test_country_group_filter(self):
        response = self.client.get(
            reverse("tenders:tender-list"), {"country_group": "cis_plus"}
        )
        ids = {row["id"] for row in response.data["results"]}
        self.assertEqual(ids, {"OP1", "OP3"})

    def test_unknown_country_group_returns_nothing(self):
        response = self.client.get(
            reverse("tenders:tender-list"), {"country_group": "atlantis"}
        )
        self.assertEqual(response.data["count"], 0)

    def test_category_filter(self):
        response = self.client.get(
            reverse("tenders:tender-list"), {"category": "consulting"}
        )
        self.assertEqual([row["id"] for row in response.data["results"]], ["OP1"])

    def test_list_exposes_category_and_source(self):
        response = self.client.get(reverse("tenders:tender-list"), {"focus": "true"})
        row = response.data["results"][0]
        self.assertEqual(row["category"], "consulting")
        self.assertEqual(row["source"], "worldbank")

    def test_stats_reports_the_focus_feed(self):
        response = self.client.get(reverse("tenders:tender-stats"))
        focus = response.data["focus"]
        self.assertEqual(focus["total"], 1)
        self.assertEqual(focus["country_group"], "cis_plus")
        self.assertIn("Invitation for Bids", focus["notice_types"])

    def test_facets_expose_categories_and_groups(self):
        response = self.client.get(reverse("tenders:tender-facets"))
        self.assertIn("categories", response.data)
        groups = {row["value"] for row in response.data["country_groups"]}
        self.assertIn("cis_plus", groups)
