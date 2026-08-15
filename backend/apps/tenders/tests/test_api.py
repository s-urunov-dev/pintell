"""API contract: read-only, filterable, and never leaking raw HTML."""

from __future__ import annotations

from datetime import UTC, date, datetime, timedelta
from unittest import mock

from django.core.cache import cache
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.tenders.models import TenderNotice


class TenderApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.open_notice = TenderNotice.objects.create(
            notice_id="OP00000001",
            notice_type="Request for Bids",
            notice_status="Published",
            notice_date=date(2026, 7, 22),
            deadline_date=now + timedelta(days=10),
            country="Uzbekistan",
            project_id="P100001",
            project_name="Rural Roads",
            bid_description="Supply of road maintenance equipment",
            procurement_method_code="RFB",
            procurement_method_name="Request for Bids",
            notice_text_sanitized="<p>Safe body</p>",
            notice_text_raw="<p>Safe body</p><script>alert(1)</script>",
        )
        cls.closed_notice = TenderNotice.objects.create(
            notice_id="OP00000002",
            notice_type="Contract Award",
            notice_date=date(2026, 6, 1),
            deadline_date=now - timedelta(days=5),
            country="Kenya",
            project_name="Water Supply",
            bid_description="Pipeline construction",
            procurement_method_code="RFQ",
            procurement_method_name="Request for Quotations",
        )

    def setUp(self):
        # /facets is cached; keep runs independent of each other.
        cache.clear()

    def test_list_returns_paginated_envelope(self):
        response = self.client.get(reverse("tenders:tender-list"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        for key in ("count", "total_pages", "page", "page_size", "results"):
            self.assertIn(key, response.data)
        self.assertEqual(response.data["count"], 2)

    def test_list_is_ordered_newest_first_by_notice_date(self):
        response = self.client.get(reverse("tenders:tender-list"))
        ids = [row["id"] for row in response.data["results"]]
        self.assertEqual(ids, ["OP00000001", "OP00000002"])

    def test_undated_historical_notices_sort_last(self):
        # Part of the archive predates upstream's noticedate field. Postgres
        # would otherwise place these NULLs first under DESC.
        TenderNotice.objects.create(
            notice_id="OP00000003",
            notice_type="Contract Award",
            notice_date=None,
            country="Nepal",
            bid_description="Historical award without a publication date",
        )
        response = self.client.get(reverse("tenders:tender-list"))
        ids = [row["id"] for row in response.data["results"]]
        self.assertEqual(ids[-1], "OP00000003")

    def test_explicit_ordering_also_keeps_nulls_last(self):
        TenderNotice.objects.create(notice_id="OP00000004", deadline_date=None)
        response = self.client.get(
            reverse("tenders:tender-list"), {"ordering": "-deadline_date"}
        )
        ids = [row["id"] for row in response.data["results"]]
        self.assertEqual(ids[-1], "OP00000004")

    def test_filter_by_country(self):
        response = self.client.get(reverse("tenders:tender-list"), {"country": "uzbekistan"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["country"], "Uzbekistan")

    def test_filter_by_procurement_method_code_or_name(self):
        by_code = self.client.get(reverse("tenders:tender-list"), {"procurement_method": "RFQ"})
        self.assertEqual(by_code.data["count"], 1)
        by_name = self.client.get(
            reverse("tenders:tender-list"), {"procurement_method": "Request for Quotations"}
        )
        self.assertEqual(by_name.data["count"], 1)

    def test_filter_is_open(self):
        response = self.client.get(reverse("tenders:tender-list"), {"is_open": "true"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], "OP00000001")

    def test_search(self):
        response = self.client.get(reverse("tenders:tender-list"), {"search": "pipeline"})
        self.assertEqual(response.data["count"], 1)
        self.assertEqual(response.data["results"][0]["id"], "OP00000002")

    def test_detail_returns_sanitized_body_only(self):
        url = reverse("tenders:tender-detail", kwargs={"notice_id": "OP00000001"})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["notice_text_sanitized"], "<p>Safe body</p>")
        self.assertNotIn("notice_text_raw", response.data)
        self.assertIn("contact", response.data)

    def test_list_omits_notice_body(self):
        response = self.client.get(reverse("tenders:tender-list"))
        self.assertNotIn("notice_text_sanitized", response.data["results"][0])

    def test_unknown_id_returns_uniform_error_envelope(self):
        url = reverse("tenders:tender-detail", kwargs={"notice_id": "OP99999999"})
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)
        self.assertIn("error", response.data)
        self.assertIn("message", response.data["error"])

    def test_write_methods_are_rejected(self):
        list_url = reverse("tenders:tender-list")
        self.assertEqual(
            self.client.post(list_url, {}).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )
        detail_url = reverse("tenders:tender-detail", kwargs={"notice_id": "OP00000001"})
        self.assertEqual(
            self.client.delete(detail_url).status_code,
            status.HTTP_405_METHOD_NOT_ALLOWED,
        )

    def test_facets_endpoint(self):
        response = self.client.get(reverse("tenders:tender-facets"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        countries = {row["value"] for row in response.data["countries"]}
        self.assertEqual(countries, {"Uzbekistan", "Kenya"})
        methods = {row["value"] for row in response.data["procurement_methods"]}
        self.assertEqual(methods, {"RFB", "RFQ"})

    def test_stats_endpoint(self):
        response = self.client.get(reverse("tenders:tender-stats"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["total_notices"], 2)
        self.assertEqual(response.data["open_notices"], 1)
        self.assertEqual(response.data["data_source"]["license"], "CC BY 4.0")

    @mock.patch("django.utils.timezone.now")
    def test_closing_today_counts_tonight_and_not_the_rest_of_the_week(self, now):
        """The headline tile answers "what runs out today", not "this week".

        The clock is frozen rather than read: the assertion is about a day
        boundary, and a test that only passes for 23 hours and 59 minutes of
        each day is a test nobody can trust the morning it fails.
        """
        now.return_value = datetime(2026, 8, 9, 9, 0, tzinfo=UTC)

        for notice_id, deadline in (
            ("OP00000101", datetime(2026, 8, 9, 12, 0, tzinfo=UTC)),
            ("OP00000102", datetime(2026, 8, 12, 12, 0, tzinfo=UTC)),
        ):
            TenderNotice.objects.create(
                notice_id=notice_id,
                notice_type="Invitation for Bids",
                country="Uzbekistan",
                deadline_date=deadline,
            )

        response = self.client.get(reverse("tenders:tender-stats"))

        self.assertEqual(response.data["focus"]["closing_today"], 1)

    def test_health_endpoint(self):
        response = self.client.get(reverse("health"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["status"], "ok")
