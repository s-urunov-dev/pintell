"""Project → notice → document → requirement, from the top.

The console could search notices and search requirements, and could not answer
"what is this project, what did it publish, and which of those documents did we
actually read" without already knowing an id — which is the state the World
Bank's own search leaves an operator in.

The cases here pin the two things that make the drill-down trustworthy: that a
project is assembled from the notices rather than from the sparse profile
table, and that a document says which notices and projects it belongs to even
though it is stored once per URL rather than once per notice.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.compliance.models import ExtractionRun, TenderRequirement
from apps.tenders.models import HarvestedDocument, TenderNotice

User = get_user_model()

NO_THROTTLE = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
}


def open_notice(notice_id: str, project_id: str, **extra) -> TenderNotice:
    """A notice inside the focus scope: open deadline, opportunity type."""
    defaults = {
        "country": "Uzbekistan",
        "notice_type": "Request for Expression of Interest",
        "notice_status": "Published",
        "deadline_date": timezone.now() + timezone.timedelta(days=30),
        "notice_date": timezone.now().date(),
        "project_name": "Road Modernization",
    }
    defaults.update(extra)
    return TenderNotice.objects.create(
        notice_id=notice_id, project_id=project_id, **defaults
    )


@override_settings(REST_FRAMEWORK=NO_THROTTLE, INGEST_FOCUS_ONLY=True)
class ProjectListTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user(
            username="operator", password="operator-pass-123", is_staff=True
        )
        self.client.force_login(self.staff)
        self.url = reverse("adminpanel:admin-project-list")

        self.first = open_notice("OP-1", "P100")
        self.second = open_notice("OP-2", "P100")
        open_notice("OP-3", "P200", project_name="Water Supply")

    def test_notices_are_grouped_into_their_project(self):
        rows = {row["project_id"]: row for row in self.client.get(self.url).json()}

        self.assertEqual(rows["P100"]["notices"], 2)
        self.assertEqual(rows["P200"]["notices"], 1)

    def test_the_project_carries_its_name_and_country(self):
        """Read off the notices, because there is no populated project table."""
        rows = {row["project_id"]: row for row in self.client.get(self.url).json()}

        self.assertEqual(rows["P100"]["project_name"], "Road Modernization")
        self.assertEqual(rows["P100"]["country"], "Uzbekistan")

    def test_a_project_with_no_profile_row_still_appears(self):
        """The reason the aggregate is built from notices.

        `ProjectProfile` is filled by a separate enrichment pass and covers a
        small fraction of the corpus. Keying the top of the drill-down on it
        would hide most projects, including open ones.
        """
        from apps.tenders.models import ProjectProfile

        self.assertEqual(ProjectProfile.objects.count(), 0)

        ids = {row["project_id"] for row in self.client.get(self.url).json()}

        self.assertEqual(ids, {"P100", "P200"})

    def test_requirement_and_document_counts_reach_the_project_row(self):
        document = HarvestedDocument.objects.create(
            url_hash="h1", url="https://example.org/tor.pdf"
        )
        document.notices.add(self.first)
        run = ExtractionRun.objects.create(
            notice=self.first, layers="L1", status=ExtractionRun.Status.OK
        )
        TenderRequirement.objects.create(
            notice=self.first,
            run=run,
            layer=TenderRequirement.Layer.L1,
            key="k",
            expression={"kind": "scalar", "key": "k", "op": ">=", "value": 1},
            evidence_quote="q",
        )

        rows = {row["project_id"]: row for row in self.client.get(self.url).json()}

        self.assertEqual(rows["P100"]["documents"], 1)
        self.assertEqual(rows["P100"]["requirements"], 1)

    def test_out_of_scope_notices_are_hidden_unless_asked_for(self):
        """The console is about the corpus the product serves — but "why is my
        project missing" is a real question, so `focus=all` answers it."""
        TenderNotice.objects.create(
            notice_id="OP-OLD",
            project_id="P900",
            country="Kenya",
            notice_type="Contract Award",
            deadline_date=timezone.now() - timezone.timedelta(days=5),
        )

        default = {r["project_id"] for r in self.client.get(self.url).json()}
        widened = {
            r["project_id"] for r in self.client.get(self.url, {"focus": "all"}).json()
        }

        self.assertNotIn("P900", default)
        self.assertIn("P900", widened)

    def test_search_matches_the_project_id_and_its_name(self):
        by_id = self.client.get(self.url, {"search": "P200"}).json()
        by_name = self.client.get(self.url, {"search": "Water"}).json()

        self.assertEqual([r["project_id"] for r in by_id], ["P200"])
        self.assertEqual([r["project_id"] for r in by_name], ["P200"])


@override_settings(REST_FRAMEWORK=NO_THROTTLE, INGEST_FOCUS_ONLY=True)
class ProjectDetailTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user(
            username="operator", password="operator-pass-123", is_staff=True
        )
        self.client.force_login(self.staff)
        self.notice = open_notice("OP-1", "P100")

    def test_a_project_lists_every_notice_it_published(self):
        open_notice("OP-2", "P100")

        body = self.client.get(
            reverse("adminpanel:admin-project-detail", args=["P100"])
        ).json()

        self.assertEqual(body["project_name"], "Road Modernization")
        self.assertEqual({n["notice_id"] for n in body["notices"]}, {"OP-1", "OP-2"})

    def test_an_unknown_project_is_a_404_rather_than_an_empty_list(self):
        """An empty list would read as "this project published nothing"."""
        response = self.client.get(
            reverse("adminpanel:admin-project-detail", args=["P-NOPE"])
        )

        self.assertEqual(response.status_code, status.HTTP_404_NOT_FOUND)


@override_settings(REST_FRAMEWORK=NO_THROTTLE)
class DocumentListTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user(
            username="operator", password="operator-pass-123", is_staff=True
        )
        self.client.force_login(self.staff)
        self.url = reverse("adminpanel:admin-document-list")

        self.first = open_notice("OP-1", "P100")
        self.second = open_notice("OP-2", "P100")
        self.document = HarvestedDocument.objects.create(
            url_hash="h1",
            url="https://example.org/tor.pdf",
            kind=HarvestedDocument.Kind.TOR,
            status=HarvestedDocument.Status.FETCHED,
            text_chars=12000,
            has_text_layer=True,
        )
        self.document.notices.add(self.first, self.second)

    def test_a_document_names_every_notice_that_links_it(self):
        """Identity here is the URL: one TOR is routinely linked by several
        notices of the same project, and the harvester stores it once."""
        row = self.client.get(self.url).json()["results"][0]

        self.assertEqual(sorted(row["notice_ids"]), ["OP-1", "OP-2"])

    def test_a_document_names_the_project_reached_through_its_notices(self):
        """The question the console could not answer at all."""
        row = self.client.get(self.url).json()["results"][0]

        self.assertEqual(row["project_ids"], ["P100"])

    def test_filtering_by_notice_narrows_to_that_notice(self):
        other = HarvestedDocument.objects.create(url_hash="h2", url="https://x/y.pdf")
        other.notices.add(self.second)

        body = self.client.get(self.url, {"notice_id": "OP-1"}).json()

        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["id"], "h1")

    def test_a_document_linked_by_two_notices_is_returned_once(self):
        """The project filter joins through a many-to-many; without `distinct`
        this row would appear twice and the count would be wrong."""
        body = self.client.get(self.url, {"project_id": "P100"}).json()

        self.assertEqual(body["count"], 1)

    def test_how_many_requirements_came_out_of_the_document(self):
        """Zero is the meaningful answer: a document fetched and parsed that
        produced nothing is either free of criteria or a failure of L3."""
        run = ExtractionRun.objects.create(
            notice=self.first, layers="L3", status=ExtractionRun.Status.OK
        )
        TenderRequirement.objects.create(
            notice=self.first,
            run=run,
            layer=TenderRequirement.Layer.L3,
            key="k",
            expression={"kind": "scalar", "key": "k", "op": ">=", "value": 1},
            evidence_quote="q",
            source_document=self.document,
        )

        row = self.client.get(self.url).json()["results"][0]

        self.assertEqual(row["requirements"], 1)

    def test_the_document_body_is_not_in_the_list_payload(self):
        """These run to 400 000 characters; fifty of them would be a page."""
        row = self.client.get(self.url).json()["results"][0]

        self.assertNotIn("text", row)
        self.assertEqual(row["text_chars"], 12000)


class DrilldownAccessTests(APITestCase):
    """Every level is staff-only, like the rest of the console."""

    def setUp(self):
        cache.clear()

    def test_projects_refuse_an_anonymous_request(self):
        response = self.client.get(reverse("adminpanel:admin-project-list"))

        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_documents_refuse_a_signed_in_non_staff_user(self):
        user = User.objects.create_user(username="vendor", password="vendor-pass-123")
        self.client.force_login(user)

        response = self.client.get(reverse("adminpanel:admin-document-list"))

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)
