"""The database-level link between a notice and its mirrored project.

A notice carries the raw upstream key (``project_id``) from the moment it is
synced; ``project_ref`` is the join, and it can only exist once the project has
been mirrored from a different API. Since the two arrive independently, the
tests below are mostly about the two orders they can arrive in.
"""

from __future__ import annotations

from datetime import timedelta
from decimal import Decimal

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.tenders.models import ProjectProfile, TenderNotice
from apps.tenders.services.projects import link_notices, sync_project
from apps.tenders.services.sync import sync_notices
from apps.tenders.services.worldbank import NoticePage

from .test_projects import FakeProjectClient


def notice_payload(notice_id: str, project_id: str) -> dict:
    return {
        "id": notice_id,
        "notice_type": "Request for Expression of Interest",
        "project_id": project_id,
        "project_name": "Rural Roads",
        "country_name": "Uzbekistan",
        "bid_description": "Consulting services",
    }


class FakeClient:
    def __init__(self, payloads):
        self._payloads = payloads

    def iter_pages(self, **kwargs):
        yield NoticePage(notices=list(self._payloads), offset=0, rows=100, total=len(self._payloads))


# The linking fixtures are out-of-scope countries; see test_sync for why the
# ingest gate is switched off rather than the payloads rewritten.
@override_settings(INGEST_FOCUS_ONLY=False)
class LinkingTests(TestCase):
    def test_notice_synced_before_its_project_has_no_link_yet(self):
        sync_notices(max_pages=1, client=FakeClient([notice_payload("OP1", "P167598")]))

        notice = TenderNotice.objects.get(pk="OP1")
        self.assertEqual(notice.project_id, "P167598")
        self.assertIsNone(notice.project_ref_id)

    def test_mirroring_the_project_links_the_notices_that_waited(self):
        sync_notices(max_pages=1, client=FakeClient([notice_payload("OP1", "P167598")]))

        sync_project("P167598", client=FakeProjectClient())

        self.assertEqual(TenderNotice.objects.get(pk="OP1").project_ref_id, "P167598")

    def test_notice_synced_after_its_project_is_linked_on_arrival(self):
        sync_project("P167598", client=FakeProjectClient())

        sync_notices(max_pages=1, client=FakeClient([notice_payload("OP1", "P167598")]))

        self.assertEqual(TenderNotice.objects.get(pk="OP1").project_ref_id, "P167598")

    def test_resyncing_a_changed_notice_keeps_its_link(self):
        sync_project("P167598", client=FakeProjectClient())
        sync_notices(max_pages=1, client=FakeClient([notice_payload("OP1", "P167598")]))

        changed = notice_payload("OP1", "P167598") | {"bid_description": "Something else"}
        stats = sync_notices(max_pages=1, client=FakeClient([changed]))

        self.assertEqual(stats.updated, 1)
        self.assertEqual(TenderNotice.objects.get(pk="OP1").project_ref_id, "P167598")

    def test_an_unmirrored_project_never_produces_a_dangling_link(self):
        # A dangling key would raise DoesNotExist on attribute access, because
        # the column carries no database constraint to catch it.
        sync_notices(max_pages=1, client=FakeClient([notice_payload("OP1", "P999999")]))

        notice = TenderNotice.objects.get(pk="OP1")
        self.assertIsNone(notice.project_ref)

    def test_link_notices_is_idempotent(self):
        ProjectProfile.objects.create(project_id="P167598")
        TenderNotice.objects.create(notice_id="OP1", project_id="P167598")

        self.assertEqual(link_notices("P167598"), 1)
        self.assertEqual(link_notices("P167598"), 0)

    def test_link_notices_ignores_a_blank_key(self):
        TenderNotice.objects.create(notice_id="OP1", project_id="")

        self.assertEqual(link_notices(""), 0)


class ProjectFilterTests(APITestCase):
    """Filtering and ordering notices by their project's own columns."""

    @classmethod
    def setUpTestData(cls):
        now = timezone.now()
        cls.big = ProjectProfile.objects.create(
            project_id="P100001",
            name="Rural Roads",
            status="Active",
            team_lead="Aigerim Sadykova, John Smith",
            implementing_agency="Ministry of Transport",
            total_amount_usd=Decimal("50000000"),
        )
        cls.small = ProjectProfile.objects.create(
            project_id="P100002",
            name="Water Supply",
            status="Closed",
            team_lead="John Smith",
            implementing_agency="Ministry of Water",
            total_amount_usd=Decimal("1000000"),
        )
        for notice_id, project in (("OP1", cls.big), ("OP2", cls.small)):
            TenderNotice.objects.create(
                notice_id=notice_id,
                notice_type="Request for Expression of Interest",
                deadline_date=now + timedelta(days=10),
                country="Uzbekistan",
                project_id=project.project_id,
                project_ref=project,
            )
        # Same shape, but its project has never been mirrored.
        cls.orphan = TenderNotice.objects.create(
            notice_id="OP3",
            notice_type="Request for Expression of Interest",
            deadline_date=now + timedelta(days=10),
            country="Uzbekistan",
            project_id="P999999",
        )
        cls.url = reverse("tenders:tender-list")

    def ids(self, response) -> list[str]:
        return [row["id"] for row in response.data["results"]]

    def test_filter_by_team_lead_matches_one_name_in_the_list(self):
        response = self.client.get(self.url, {"project_team_lead": "aigerim"})

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        self.assertEqual(self.ids(response), ["OP1"])

    def test_filter_by_team_lead_shared_between_projects(self):
        response = self.client.get(self.url, {"project_team_lead": "John Smith"})

        self.assertEqual(sorted(self.ids(response)), ["OP1", "OP2"])

    def test_filter_by_project_status(self):
        response = self.client.get(self.url, {"project_status": "closed"})

        self.assertEqual(self.ids(response), ["OP2"])

    def test_filter_by_implementing_agency(self):
        response = self.client.get(self.url, {"project_agency": "transport"})

        self.assertEqual(self.ids(response), ["OP1"])

    def test_filter_by_financing_range(self):
        response = self.client.get(self.url, {"project_amount_min": "10000000"})

        self.assertEqual(self.ids(response), ["OP1"])

        response = self.client.get(self.url, {"project_amount_max": "10000000"})

        self.assertEqual(self.ids(response), ["OP2"])

    def test_has_project_separates_mirrored_from_unmirrored(self):
        response = self.client.get(self.url, {"has_project": "false"})

        self.assertEqual(self.ids(response), ["OP3"])

        response = self.client.get(self.url, {"has_project": "true"})

        self.assertEqual(sorted(self.ids(response)), ["OP1", "OP2"])

    def test_ordering_by_project_financing(self):
        # Asserted as relative position, not absolute index: where a notice
        # with no mirrored project lands is up to the database (PostgreSQL and
        # SQLite disagree about NULLs), but the two known amounts must not be.
        descending = self.ids(self.client.get(self.url, {"ordering": "-project_amount"}))
        self.assertLess(descending.index("OP1"), descending.index("OP2"))

        ascending = self.ids(self.client.get(self.url, {"ordering": "project_amount"}))
        self.assertLess(ascending.index("OP2"), ascending.index("OP1"))

    def test_ordering_by_project_financing_keeps_unjoined_notices(self):
        """Sorting by the project must not silently drop notices without one."""
        response = self.client.get(self.url, {"ordering": "-project_amount"})

        self.assertEqual(sorted(self.ids(response)), ["OP1", "OP2", "OP3"])

    def test_detail_still_embeds_the_project_through_the_join(self):
        url = reverse("tenders:tender-detail", kwargs={"notice_id": "OP1"})

        with self.assertNumQueries(2):
            response = self.client.get(url)

        self.assertEqual(response.data["project"]["project_id"], "P100001")

    def test_detail_finds_a_project_linked_after_the_notice_was_written(self):
        # The window between `sync_project` creating the profile and
        # `link_notices` connecting it: the reader should still see it.
        ProjectProfile.objects.create(project_id="P999999", name="Late Arrival")
        url = reverse("tenders:tender-detail", kwargs={"notice_id": "OP3"})

        response = self.client.get(url)

        self.assertEqual(response.data["project"]["name"], "Late Arrival")
