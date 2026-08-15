"""The console's requirement list.

The compliance screen reports counts — "2 requirements" against a notice id.
That answers "did the extraction run". It does not answer "what does this tender
demand", which is the question an operator has when checking whether the output
is any good. These cases cover the endpoint that answers the second one, and the
two things it must never do: hide which tender a row belongs to, and let a
malformed row take the page down with it.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import override_settings
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.compliance.models import ExtractionRun, TenderRequirement
from apps.tenders.models import TenderNotice

User = get_user_model()

NO_THROTTLE = {
    "DEFAULT_AUTHENTICATION_CLASSES": [],
    "DEFAULT_PERMISSION_CLASSES": ["rest_framework.permissions.AllowAny"],
    "EXCEPTION_HANDLER": "apps.core.exceptions.api_exception_handler",
}

TURNOVER = {
    "kind": "scalar",
    "key": "annual_turnover_avg",
    "op": ">=",
    "value": 5000000,
    "unit": "USD",
    "label": "Average annual turnover",
}


@override_settings(REST_FRAMEWORK=NO_THROTTLE)
class RequirementListTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user(
            username="operator", password="operator-pass-123", is_staff=True
        )
        self.client.force_login(self.staff)
        self.url = reverse("adminpanel:admin-requirement-list")

        self.notice = TenderNotice.objects.create(
            notice_id="OP-1",
            country="Uzbekistan",
            bid_description="Reconstruction of the M41 highway",
        )
        self.run = ExtractionRun.objects.create(
            notice=self.notice, layers="L1,L2", status=ExtractionRun.Status.OK
        )
        self.requirement = TenderRequirement.objects.create(
            notice=self.notice,
            run=self.run,
            layer=TenderRequirement.Layer.L2,
            key="annual_turnover_avg",
            label="Turnover",
            expression=TURNOVER,
            evidence_quote="The bidder shall have an average annual turnover of USD 5,000,000.",
            grounding=TenderRequirement.Grounding.VERIFIED,
        )

    def test_the_row_names_the_tender_it_belongs_to(self):
        """The operator question is "which tender is this?" — the answer has to
        be in the row, not one request away."""
        row = self.client.get(self.url).json()["results"][0]

        self.assertEqual(row["notice_id"], "OP-1")
        self.assertEqual(row["notice_title"], "Reconstruction of the M41 highway")
        self.assertEqual(row["notice_country"], "Uzbekistan")

    def test_the_expression_is_rendered_as_a_sentence(self):
        row = self.client.get(self.url).json()["results"][0]

        self.assertEqual(row["summary"], "Average annual turnover ≥ 5 000 000 USD")

    def test_the_tree_is_returned_alongside_the_sentence(self):
        """The summary is a convenience; the verdict is computed from the tree,
        so an operator auditing a wrong verdict needs the tree itself."""
        row = self.client.get(self.url).json()["results"][0]

        self.assertEqual(row["expression"], TURNOVER)

    def test_the_quote_is_returned_because_a_row_without_one_is_unusable(self):
        row = self.client.get(self.url).json()["results"][0]

        self.assertIn("USD 5,000,000", row["evidence_quote"])
        self.assertEqual(row["grounding"], TenderRequirement.Grounding.VERIFIED)

    def test_a_malformed_expression_does_not_take_the_page_down(self):
        """This screen is what an operator opens *because* something is wrong."""
        TenderRequirement.objects.create(
            notice=self.notice,
            run=self.run,
            layer=TenderRequirement.Layer.L2,
            key="broken",
            expression={"kind": "teleport"},
            evidence_quote="q",
        )

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        summaries = {row["key"]: row["summary"] for row in response.json()["results"]}
        self.assertEqual(summaries["broken"], "")
        self.assertEqual(summaries["annual_turnover_avg"], "Average annual turnover ≥ 5 000 000 USD")


@override_settings(REST_FRAMEWORK=NO_THROTTLE)
class RequirementFilterTests(APITestCase):
    def setUp(self):
        cache.clear()
        self.staff = User.objects.create_user(
            username="operator", password="operator-pass-123", is_staff=True
        )
        self.client.force_login(self.staff)
        self.url = reverse("adminpanel:admin-requirement-list")

        self.first = TenderNotice.objects.create(notice_id="OP-A", country="Uzbekistan")
        self.second = TenderNotice.objects.create(notice_id="OP-B", country="Armenia")
        for notice, layer, grounding in (
            (self.first, TenderRequirement.Layer.L1, TenderRequirement.Grounding.VERIFIED),
            (self.first, TenderRequirement.Layer.L2, TenderRequirement.Grounding.NOT_FOUND),
            (self.second, TenderRequirement.Layer.L2, TenderRequirement.Grounding.VERIFIED),
        ):
            TenderRequirement.objects.create(
                notice=notice,
                run=ExtractionRun.objects.create(
                    notice=notice, layers=layer, status=ExtractionRun.Status.OK
                ),
                layer=layer,
                key="k",
                expression={"kind": "scalar", "key": "k", "op": ">=", "value": 1},
                grounding=grounding,
                evidence_quote="q",
            )

    def test_filtering_by_tender_narrows_to_that_tender(self):
        body = self.client.get(self.url, {"search": "OP-B"}).json()

        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["notice_id"], "OP-B")

    def test_filtering_by_grounding_isolates_the_hallucination_signal(self):
        """The most common reason to open this screen."""
        body = self.client.get(
            self.url, {"grounding": TenderRequirement.Grounding.NOT_FOUND}
        ).json()

        self.assertEqual(body["count"], 1)
        self.assertEqual(body["results"][0]["layer"], TenderRequirement.Layer.L2)

    def test_filtering_by_layer_separates_the_free_layer_from_the_paid_ones(self):
        body = self.client.get(self.url, {"layer": TenderRequirement.Layer.L2}).json()

        self.assertEqual(body["count"], 2)

    def test_the_tender_dropdown_offers_only_tenders_that_have_requirements(self):
        """A filter that can select an empty result wastes a click."""
        TenderNotice.objects.create(notice_id="OP-EMPTY", country="Belarus")

        rows = self.client.get(reverse("adminpanel:admin-requirement-notices")).json()

        ids = {row["notice_id"] for row in rows}
        self.assertEqual(ids, {"OP-A", "OP-B"})
        self.assertEqual({row["notice_id"]: row["requirements"] for row in rows}["OP-A"], 2)


class RequirementAccessTests(APITestCase):
    """Extracted requirements are operator data, and the table is read-only."""

    def setUp(self):
        cache.clear()
        self.url = reverse("adminpanel:admin-requirement-list")

    def test_an_anonymous_request_is_refused(self):
        response = self.client.get(self.url)

        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )

    def test_a_signed_in_non_staff_user_is_refused(self):
        user = User.objects.create_user(username="vendor", password="vendor-pass-123")
        self.client.force_login(user)

        response = self.client.get(self.url)

        self.assertEqual(response.status_code, status.HTTP_403_FORBIDDEN)

    @override_settings(REST_FRAMEWORK=NO_THROTTLE)
    def test_the_endpoint_refuses_to_create_a_requirement(self):
        """A hand-written row would carry a quote that does not support it —
        a claim whose own citation contradicts it."""
        staff = User.objects.create_user(
            username="operator", password="operator-pass-123", is_staff=True
        )
        self.client.force_login(staff)
        notice = TenderNotice.objects.create(notice_id="OP-C")

        response = self.client.post(
            self.url, {"notice_id": notice.notice_id, "key": "invented"}, format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_405_METHOD_NOT_ALLOWED)
        self.assertEqual(TenderRequirement.objects.count(), 0)
