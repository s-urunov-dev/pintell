"""Project documents and the ESRS summary, keyed by Project ID."""

from __future__ import annotations

from datetime import date, timedelta
from unittest.mock import patch

from django.conf import settings
from django.core.cache import cache
from django.test import SimpleTestCase, TestCase
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.tenders.models import ProjectDocument, ProjectProfile, TenderNotice
from apps.tenders.services.projects import (
    ProjectAPIError,
    ProjectSyncStats,
    _retry_delay,
    map_document,
    map_project,
    pick_esrs,
    select_pending_projects,
    sync_lock_key,
    sync_project,
)
from apps.tenders.tasks import sync_project_profile

PROJECT_PAYLOAD = {
    "id": "P167598",
    "project_name": "Primary Health Care Quality Improvement Program",
    "countryshortname": "Kyrgyz Republic",
    "status": "Active",
    "totalamt": "20,000,000",
    "totalcommamt": "37,000,000",
    "lendinginstr": "Program-for-Results Financing",
    "impagency": "Ministry of Health",
    "sector": [{"Name": "Public Administration - Health"}, {"Name": "ICT Services"}],
    "boardapprovaldate": "2019-06-07T00:00:00Z",
    "closingdate": "2026-06-30T00:00:00Z",
    "url": "https://projects.worldbank.org/en/projects-operations/project-detail/P167598",
}

DOCUMENT_PAYLOADS = [
    {
        "guid": "099070826093552381",
        "display_title": "Kyrgyz Republic - Procurement Plan",
        "docty": "Procurement Plan",
        "docdt": "2026-07-08T04:00:00Z",
        "pdfurl": "https://documents.worldbank.org/curated/en/099070826093552381/pdf/P167.pdf",
        "txturl": "https://documents.worldbank.org/curated/en/099070826093552381/text/P167.txt",
        "url": "http://documents.worldbank.org/curated/en/099070826093552381",
        "lang": "English",
    },
    {
        "guid": "099052926052513696",
        "display_title": "Appraisal Environmental and Social Review Summary (ESRS) - ESRSA02670",
        "docty": "Environmental and Social Review Summary",
        "docdt": "2023-05-16T04:00:00Z",
        "pdfurl": "https://documents.worldbank.org/curated/en/099052926052513696/pdf/esrs.pdf",
        "url": "http://documents.worldbank.org/curated/en/099052926052513696",
        "lang": "English",
    },
]


class FakeProjectClient:
    """Replays scripted upstream payloads; can be told to fail.

    Either endpoint can be handed an exception instead of a payload, which is
    how the "metadata arrived, documents did not" case is reproduced.
    """

    def __init__(self, project=PROJECT_PAYLOAD, documents=None, fail=False):
        self._project = project
        self._documents = DOCUMENT_PAYLOADS if documents is None else documents
        self._fail = fail

    def fetch_project(self, project_id):
        if self._fail:
            raise ProjectAPIError("upstream 503")
        if isinstance(self._project, Exception):
            raise self._project
        return self._project

    def fetch_documents(self, project_id, rows=100):
        if self._fail:
            raise ProjectAPIError("upstream 503")
        if isinstance(self._documents, Exception):
            raise self._documents
        return self._documents


class MappingTests(SimpleTestCase):
    def test_project_money_and_dates_are_parsed(self):
        values = map_project(PROJECT_PAYLOAD)
        self.assertEqual(values["name"], PROJECT_PAYLOAD["project_name"])
        self.assertEqual(str(values["total_amount_usd"]), "20000000")
        self.assertEqual(str(values["commitment_amount_usd"]), "37000000")
        self.assertEqual(values["board_approval_date"], date(2019, 6, 7))
        self.assertEqual(values["sectors"], ["Public Administration - Health", "ICT Services"])

    def test_document_mapping_keeps_title_and_pdf(self):
        values = map_document(DOCUMENT_PAYLOADS[0], "P167598")
        self.assertEqual(values["guid"], "099070826093552381")
        self.assertTrue(values["title"])
        self.assertTrue(values["pdf_url"].endswith(".pdf"))
        self.assertEqual(values["doc_date"], date(2026, 7, 8))

    def test_document_without_guid_is_dropped(self):
        self.assertIsNone(map_document({"display_title": "x"}, "P1"))

    def test_esrs_is_picked_by_type(self):
        mapped = [map_document(d, "P167598") for d in DOCUMENT_PAYLOADS]
        esrs = pick_esrs(mapped)
        self.assertIsNotNone(esrs)
        self.assertIn("ESRS", esrs["title"])

    def test_no_esrs_returns_none(self):
        mapped = [map_document(DOCUMENT_PAYLOADS[0], "P167598")]
        self.assertIsNone(pick_esrs(mapped))


class SyncProjectTests(TestCase):
    def test_mirrors_profile_documents_and_esrs(self):
        stats = ProjectSyncStats()
        profile = sync_project("P167598", client=FakeProjectClient(), stats=stats)

        self.assertEqual(profile.name, PROJECT_PAYLOAD["project_name"])
        self.assertEqual(profile.documents_count, 2)
        self.assertEqual(ProjectDocument.objects.count(), 2)
        self.assertTrue(profile.has_esrs)
        self.assertEqual(profile.esrs_report_no, "ESRSA02670")
        self.assertEqual(stats.esrs_found, 1)

    def test_second_sync_updates_rather_than_duplicating(self):
        sync_project("P167598", client=FakeProjectClient())
        sync_project("P167598", client=FakeProjectClient())

        self.assertEqual(ProjectProfile.objects.count(), 1)
        self.assertEqual(ProjectDocument.objects.count(), 2)

    def test_upstream_failure_is_recorded_not_raised(self):
        stats = ProjectSyncStats()
        profile = sync_project("P167598", client=FakeProjectClient(fail=True), stats=stats)

        self.assertEqual(stats.failed, 1)
        self.assertIn("503", profile.last_error)
        self.assertEqual(ProjectDocument.objects.count(), 0)

    def test_failure_arms_a_backoff_and_success_clears_it(self):
        failed = sync_project("P167598", client=FakeProjectClient(fail=True))

        self.assertEqual(failed.error_count, 1)
        self.assertIsNotNone(failed.next_retry_at)
        self.assertIsNotNone(failed.last_attempt_at)
        self.assertIsNone(failed.fetched_at)

        healed = sync_project("P167598", client=FakeProjectClient())

        self.assertEqual(healed.error_count, 0)
        self.assertIsNone(healed.next_retry_at)
        self.assertEqual(healed.last_error, "")
        self.assertIsNotNone(healed.fetched_at)

    def test_backoff_widens_with_each_consecutive_failure(self):
        client = FakeProjectClient(fail=True)
        first = sync_project("P167598", client=client)
        first_wait = first.next_retry_at - first.last_attempt_at

        second = sync_project("P167598", client=client)
        second_wait = second.next_retry_at - second.last_attempt_at

        self.assertEqual(second.error_count, 2)
        self.assertGreater(second_wait, first_wait)

    def test_backoff_is_capped(self):
        # A project upstream has never heard of must not schedule its retry
        # past what a DateTimeField can hold.
        self.assertEqual(
            _retry_delay(500), timedelta(days=settings.PROJECTS["RETRY_MAX_DAYS"])
        )

    def test_project_missing_upstream_counts_as_a_failure(self):
        profile = sync_project("P000000", client=FakeProjectClient(project=None))

        self.assertEqual(profile.error_count, 1)
        self.assertIn("no project", profile.last_error)

    def test_documents_failure_alone_still_arms_the_backoff(self):
        profile = sync_project(
            "P167598", client=FakeProjectClient(documents=ProjectAPIError("upstream 500"))
        )

        self.assertEqual(profile.name, PROJECT_PAYLOAD["project_name"])
        self.assertEqual(profile.error_count, 1)
        self.assertIn("documents", profile.last_error)


class PendingSelectionTests(TestCase):
    """Which projects the next batch picks up, and in what order."""

    def test_never_mirrored_projects_come_first(self):
        ProjectProfile.objects.create(project_id="P000001", fetched_at=timezone.now())

        pending = select_pending_projects(["P000001", "P000002"], limit=5)

        self.assertEqual(pending.new, ["P000002"])
        self.assertEqual(pending.ids[0], "P000002")

    def test_fresh_profiles_are_left_alone(self):
        ProjectProfile.objects.create(project_id="P000001", fetched_at=timezone.now())

        self.assertEqual(select_pending_projects(["P000001"], limit=5).ids, [])

    def test_stale_profiles_are_refetched_oldest_first(self):
        now = timezone.now()
        days = settings.PROJECTS["REFRESH_DAYS"]
        ProjectProfile.objects.create(
            project_id="P000001", fetched_at=now - timedelta(days=days + 1)
        )
        ProjectProfile.objects.create(
            project_id="P000002", fetched_at=now - timedelta(days=days + 30)
        )
        ProjectProfile.objects.create(project_id="P000003", fetched_at=now)

        pending = select_pending_projects([], limit=5)

        self.assertEqual(pending.stale, ["P000002", "P000001"])

    def test_failed_profile_is_retried_only_after_its_backoff(self):
        now = timezone.now()
        ProjectProfile.objects.create(
            project_id="P000001",
            error_count=2,
            next_retry_at=now + timedelta(hours=1),
            last_error="upstream 503",
        )

        self.assertEqual(select_pending_projects([], limit=5).retry, [])

        ProjectProfile.objects.filter(pk="P000001").update(
            next_retry_at=now - timedelta(minutes=1)
        )

        self.assertEqual(select_pending_projects([], limit=5).retry, ["P000001"])

    def test_new_projects_outrank_retries_and_staleness_within_the_limit(self):
        now = timezone.now()
        ProjectProfile.objects.create(
            project_id="P000001", error_count=1, next_retry_at=now - timedelta(hours=1)
        )
        ProjectProfile.objects.create(
            project_id="P000002",
            fetched_at=now - timedelta(days=settings.PROJECTS["REFRESH_DAYS"] + 1),
        )

        pending = select_pending_projects(["P000009"], limit=1)

        self.assertEqual(pending.ids, ["P000009"])

    def test_retries_outrank_staleness(self):
        now = timezone.now()
        ProjectProfile.objects.create(
            project_id="P000001", error_count=1, next_retry_at=now - timedelta(hours=1)
        )
        ProjectProfile.objects.create(
            project_id="P000002",
            fetched_at=now - timedelta(days=settings.PROJECTS["REFRESH_DAYS"] + 1),
        )

        self.assertEqual(select_pending_projects([], limit=1).ids, ["P000001"])

    def test_blank_and_duplicate_candidates_are_ignored(self):
        pending = select_pending_projects(["", "P000001", "P000001", None], limit=5)

        self.assertEqual(pending.new, ["P000001"])


class ProjectDocumentsApiTests(APITestCase):
    @classmethod
    def setUpTestData(cls):
        cls.notice = TenderNotice.objects.create(
            notice_id="OP00458651",
            notice_type="Request for Expression of Interest",
            country="Kyrgyz Republic",
            project_id="P167598",
            bid_description="Consultant for health quality improvement",
        )

    def test_documents_endpoint_lists_titles_and_pdfs(self):
        sync_project("P167598", client=FakeProjectClient())

        url = reverse("tenders:tender-documents", kwargs={"notice_id": "OP00458651"})
        response = self.client.get(url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(response.data["project_id"], "P167598")
        self.assertEqual(len(response.data["documents"]), 2)
        first = response.data["documents"][0]
        self.assertIn("title", first)
        self.assertIn("pdf_url", first)

    def test_documents_endpoint_reports_esrs_block(self):
        sync_project("P167598", client=FakeProjectClient())

        url = reverse("tenders:tender-documents", kwargs={"notice_id": "OP00458651"})
        esrs = self.client.get(url).data["esrs"]

        self.assertIsNotNone(esrs)
        self.assertEqual(esrs["report_no"], "ESRSA02670")
        self.assertTrue(esrs["pdf_url"])

    def test_unmirrored_project_says_pending(self):
        url = reverse("tenders:tender-documents", kwargs={"notice_id": "OP00458651"})
        with patch("apps.tenders.tasks.sync_project_profile.delay"):
            response = self.client.get(url)

        self.assertTrue(response.data["pending"])
        self.assertEqual(response.data["documents"], [])


class OnDemandSyncTests(APITestCase):
    """A notice the periodic cycle never reaches still gets its project."""

    @classmethod
    def setUpTestData(cls):
        cls.notice = TenderNotice.objects.create(
            notice_id="OP00458651",
            notice_type="Request for Expression of Interest",
            country="Kyrgyz Republic",
            project_id="P167598",
        )
        cls.url = reverse("tenders:tender-documents", kwargs={"notice_id": "OP00458651"})

    def setUp(self):
        cache.clear()

    def test_pending_documents_request_queues_the_mirror(self):
        with patch("apps.tenders.tasks.sync_project_profile.delay") as delay:
            response = self.client.get(self.url)

        delay.assert_called_once_with("P167598")
        self.assertTrue(response.data["queued"])

    def test_second_reader_does_not_queue_the_same_project_again(self):
        with patch("apps.tenders.tasks.sync_project_profile.delay") as delay:
            first = self.client.get(self.url)
            second = self.client.get(self.url)

        self.assertEqual(delay.call_count, 1)
        self.assertTrue(first.data["queued"])
        self.assertFalse(second.data["queued"])

    def test_a_dead_broker_is_not_an_error_for_the_reader(self):
        with patch(
            "apps.tenders.tasks.sync_project_profile.delay",
            side_effect=OSError("broker unreachable"),
        ):
            response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertFalse(response.data["queued"])
        # The lock was released, so the next reader retries rather than
        # waiting out the whole window on a broker that may already be back.
        self.assertIsNone(cache.get(sync_lock_key("P167598")))

    def test_a_mirrored_project_queues_nothing(self):
        sync_project("P167598", client=FakeProjectClient())

        with patch("apps.tenders.tasks.sync_project_profile.delay") as delay:
            response = self.client.get(self.url)

        delay.assert_not_called()
        self.assertEqual(len(response.data["documents"]), 2)

    def test_notice_without_a_project_queues_nothing(self):
        TenderNotice.objects.create(notice_id="OP00000001", project_id="")
        url = reverse("tenders:tender-documents", kwargs={"notice_id": "OP00000001"})

        with patch("apps.tenders.tasks.sync_project_profile.delay") as delay:
            response = self.client.get(url)

        delay.assert_not_called()
        self.assertIsNone(response.data["project"])

    def test_the_task_mirrors_the_project(self):
        with patch(
            "apps.tenders.tasks.sync_project",
            side_effect=lambda pid, **kw: sync_project(
                pid, client=FakeProjectClient(), **kw
            ),
        ):
            result = sync_project_profile("P167598")

        self.assertEqual(result["project_id"], "P167598")
        self.assertEqual(ProjectProfile.objects.count(), 1)

    def test_the_task_swallows_unexpected_failures(self):
        with patch(
            "apps.tenders.tasks.sync_project", side_effect=RuntimeError("boom")
        ):
            result = sync_project_profile("P167598")

        self.assertIn("boom", result["error"])

    def test_notice_detail_embeds_the_project_dashboard(self):
        sync_project("P167598", client=FakeProjectClient())

        url = reverse("tenders:tender-detail", kwargs={"notice_id": "OP00458651"})
        project = self.client.get(url).data["project"]

        self.assertEqual(project["project_id"], "P167598")
        self.assertEqual(project["status"], "Active")
        self.assertTrue(project["has_esrs"])
