"""Console read models and operations."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.tenders.models import BackfillPartition, SyncRun, TenderNotice

User = get_user_model()

NO_THROTTLE = {
    "DEFAULT_THROTTLE_CLASSES": [],
    "DEFAULT_THROTTLE_RATES": {},
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "DEFAULT_PAGINATION_CLASS": "apps.core.pagination.StandardResultsSetPagination",
    "PAGE_SIZE": 20,
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
}


@override_settings(REST_FRAMEWORK=NO_THROTTLE)
class ConsoleDataTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            username="operator", password="operator-pass-123", is_staff=True
        )
        now = timezone.now()

        TenderNotice.objects.create(
            notice_id="OP00000001",
            notice_type="Request for Bids",
            country="Uzbekistan",
            notice_date=date(2026, 7, 1),
            deadline_date=now + timedelta(days=3),
            procurement_method_code="RFB",
            bid_description="Road works",
            notice_text_raw="<p>ok</p><script>alert(1)</script>",
            notice_text_sanitized="<p>ok</p><script>alert(1)</script>",  # stale on purpose
        )
        TenderNotice.objects.create(
            notice_id="OP00000002",
            notice_type="Contract Award",
            country="Kenya",
            notice_date=date(2015, 3, 5),
            procurement_method_code="RFQ",
            bid_description="Pipeline",
        )
        TenderNotice.objects.create(
            notice_id="OP00000003",
            notice_type="Contract Award",
            country="Kenya",
            notice_date=None,  # historical record with no upstream date
        )

        SyncRun.objects.create(
            status=SyncRun.Status.SUCCESS, trigger="celery-beat",
            finished_at=now, created_count=10,
        )
        SyncRun.objects.create(status=SyncRun.Status.FAILED, trigger="startup")

        BackfillPartition.objects.create(
            key="recent", kind=BackfillPartition.Kind.RECENT,
            upstream_total=1000, next_offset=1000,
            status=BackfillPartition.Status.COMPLETED,
        )
        cls.partition = BackfillPartition.objects.create(
            key="country:Kenya", kind=BackfillPartition.Kind.COUNTRY,
            label="Kenya", filters={"project_ctry_name": "Kenya"},
            upstream_total=500, next_offset=200, pages_done=2,
            status=BackfillPartition.Status.RUNNING,
        )

    def setUp(self):
        # Paginated totals are cached per query; keep runs independent.
        cache.clear()
        self.client.force_login(self.staff)

    # -- dashboard ---------------------------------------------------------
    def test_overview_shape(self):
        response = self.client.get(reverse("adminpanel:admin-overview"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        data = response.data
        self.assertEqual(data["notices"]["total"], 3)
        self.assertEqual(data["notices"]["open"], 1)
        self.assertEqual(data["notices"]["without_notice_date"], 1)
        self.assertEqual(data["notices"]["countries"], 2)
        self.assertIn("archive", data)
        self.assertIn("sync_health", data)

    def test_overview_year_histogram_excludes_undated_rows(self):
        response = self.client.get(reverse("adminpanel:admin-overview"))
        years = {row["year"]: row["count"] for row in response.data["notices_per_year"]}
        self.assertEqual(years, {2015: 1, 2026: 1})

    def test_overview_top_countries_are_ranked(self):
        response = self.client.get(reverse("adminpanel:admin-overview"))
        top = response.data["top_countries"][0]
        self.assertEqual(top["value"], "Kenya")
        self.assertEqual(top["count"], 2)

    def test_sync_health_counts_failures(self):
        response = self.client.get(reverse("adminpanel:admin-overview"))
        health = response.data["sync_health"]
        self.assertEqual(health["runs_in_window"], 2)
        self.assertEqual(health["failures_in_window"], 1)

    def test_system_status_reports_dependencies(self):
        response = self.client.get(reverse("adminpanel:admin-system"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["database"]["ok"])
        self.assertIn("celery", response.data)
        self.assertIn("configuration", response.data)
        # Secrets must never appear in the configuration summary.
        self.assertNotIn("secret", str(response.data["configuration"]).lower())

    # -- sync runs ---------------------------------------------------------
    def test_sync_runs_list_and_filter(self):
        url = reverse("adminpanel:admin-sync-run-list")
        self.assertEqual(self.client.get(url).data["count"], 2)
        filtered = self.client.get(url, {"status": "failed"})
        self.assertEqual(filtered.data["count"], 1)

    # -- partitions --------------------------------------------------------
    def test_partitions_list_includes_progress(self):
        response = self.client.get(reverse("adminpanel:admin-partition-list"))
        self.assertEqual(response.data["count"], 2)
        row = next(r for r in response.data["results"] if r["key"] == "country:Kenya")
        self.assertEqual(row["progress_percent"], 40.0)

    def test_partition_reset_rewinds_the_checkpoint(self):
        url = reverse("adminpanel:admin-partition-reset", kwargs={"pk": self.partition.pk})
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)

        self.partition.refresh_from_db()
        self.assertEqual(self.partition.next_offset, 0)
        self.assertEqual(self.partition.status, BackfillPartition.Status.PENDING)

    def test_partition_rescan_registers_new_countries(self):
        response = self.client.post(reverse("adminpanel:admin-partition-rescan"))
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        # Uzbekistan exists in the notices but had no partition yet.
        self.assertTrue(
            BackfillPartition.objects.filter(key="country:Uzbekistan").exists()
        )

    # -- notices -----------------------------------------------------------
    def test_notice_list_reports_body_sizes_without_shipping_bodies(self):
        response = self.client.get(reverse("adminpanel:admin-notice-list"))
        row = next(r for r in response.data["results"] if r["id"] == "OP00000001")
        self.assertGreater(row["raw_chars"], 0)
        self.assertNotIn("notice_text_raw", row)

    def test_notice_detail_exposes_both_bodies_for_auditing(self):
        url = reverse(
            "adminpanel:admin-notice-detail", kwargs={"notice_id": "OP00000001"}
        )
        response = self.client.get(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertIn("notice_text_raw", response.data)
        self.assertIn("notice_text_sanitized", response.data)

    def test_resanitize_repairs_a_stale_body(self):
        url = reverse(
            "adminpanel:admin-notice-resanitize", kwargs={"notice_id": "OP00000001"}
        )
        response = self.client.post(url)
        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertTrue(response.data["changed"])

        notice = TenderNotice.objects.get(pk="OP00000001")
        self.assertNotIn("script", notice.notice_text_sanitized.lower())
        # The raw copy is preserved for auditing.
        self.assertIn("script", notice.notice_text_raw.lower())

    def test_resanitize_is_idempotent(self):
        url = reverse(
            "adminpanel:admin-notice-resanitize", kwargs={"notice_id": "OP00000001"}
        )
        self.client.post(url)
        self.assertFalse(self.client.post(url).data["changed"])


@override_settings(REST_FRAMEWORK=NO_THROTTLE)
class ConsoleActionTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = User.objects.create_user(
            username="operator", password="operator-pass-123", is_staff=True
        )
        cls.partition = BackfillPartition.objects.create(
            key="country:Kenya", kind=BackfillPartition.Kind.COUNTRY, label="Kenya"
        )

    def setUp(self):
        self.client.force_login(self.staff)

    @patch("apps.adminpanel.views.sync_procurement_notices")
    def test_trigger_sync_queues_the_task_with_filters(self, task):
        task.apply_async.return_value.id = "task-123"

        response = self.client.post(
            reverse("adminpanel:admin-trigger-sync"),
            {"pages": 3, "country": "Uzbekistan"},
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["task_id"], "task-123")
        kwargs = task.apply_async.call_args.kwargs["kwargs"]
        self.assertEqual(kwargs["max_pages"], 3)
        self.assertEqual(kwargs["filters"], {"project_ctry_name": "Uzbekistan"})
        self.assertEqual(kwargs["trigger"], "console")

    @patch("apps.adminpanel.views.sync_procurement_notices")
    def test_trigger_sync_reports_a_dead_broker_as_503(self, task):
        task.apply_async.side_effect = OSError("connection refused")

        response = self.client.post(reverse("adminpanel:admin-trigger-sync"), {})
        self.assertEqual(response.status_code, status.HTTP_503_SERVICE_UNAVAILABLE)

    @patch("apps.adminpanel.views.backfill_tender_archive")
    def test_trigger_backfill_for_a_named_partition(self, task):
        task.apply_async.return_value.id = "task-456"

        response = self.client.post(
            reverse("adminpanel:admin-trigger-backfill"),
            {"partition_key": "country:Kenya", "pages": 5},
        )

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        kwargs = task.apply_async.call_args.kwargs["kwargs"]
        self.assertEqual(kwargs["partition_key"], "country:Kenya")

    def test_trigger_backfill_rejects_unknown_partition(self):
        response = self.client.post(
            reverse("adminpanel:admin-trigger-backfill"),
            {"partition_key": "country:Atlantis"},
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    @patch("apps.adminpanel.views.backfill_tender_archive")
    def test_partition_run_action_queues_that_partition(self, task):
        task.apply_async.return_value.id = "task-789"

        url = reverse("adminpanel:admin-partition-run", kwargs={"pk": self.partition.pk})
        response = self.client.post(url, {"pages": 2})

        self.assertEqual(response.status_code, status.HTTP_202_ACCEPTED)
        self.assertEqual(response.data["partition"], "country:Kenya")

    def test_sync_trigger_validates_page_bounds(self):
        response = self.client.post(
            reverse("adminpanel:admin-trigger-sync"), {"pages": 9999}
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
