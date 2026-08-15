"""A vendor answering criteria about themselves, with a switch.

Most of what a tender demands is neither a number nor a file. "Advanced
university degree in agricultural economics" is something a company has or has
not, and the assessment page offered only two ways to say so: type a value into
a field the criterion does not have, or upload a document. Vendors mostly do
neither, so those requirements stayed "not yet established" — a description of
our ignorance presented as a statement about their eligibility.

These cases cover the switch, what it may and may not touch, and the one
distinction the feature turns on: a vendor's claim about themselves is not the
same kind of thing as a criterion extracted from a document, and a verdict must
never present the first as the second.
"""

from __future__ import annotations

from django.contrib.auth import get_user_model
from django.test import TestCase
from django.urls import reverse
from rest_framework import status
from rest_framework.test import APITestCase

from apps.compliance.expressions import (
    Bid,
    Portfolio,
    Requirement,
    Scalar,
    Verdict,
    assess_with_declarations,
    declared_result,
    parse_requirement,
)
from apps.compliance.models import (
    ExtractionRun,
    RequirementDeclaration,
    TenderRequirement,
    VendorProfile,
)
from apps.tenders.models import TenderNotice

User = get_user_model()

DEGREE = {
    "key": "advanced_degree",
    "label": "Advanced university degree",
    "is_mandatory": True,
    "evidence_quote": "Advanced university degree (Master's or higher).",
    "expression": {"kind": "scalar", "key": "degree", "op": "==", "value": "masters"},
}


class DeclaredResultTests(TestCase):
    """The engine's side: a declaration is its own kind of answer."""

    def test_a_yes_is_satisfied_and_says_who_said_so(self):
        result = declared_result(True, "Advanced degree")

        self.assertIs(result.verdict, Verdict.SATISFIED)
        self.assertIn("declared by the bidder", result.explanation)

    def test_a_no_is_a_failure_the_vendor_stated(self):
        result = declared_result(False)

        self.assertIs(result.verdict, Verdict.FAILED)

    def test_the_trace_names_the_declaration_rather_than_a_computation(self):
        """An explanation printed for an auditor must state its provenance
        without needing the database beside it."""
        result = declared_result(True)

        self.assertEqual(result.trace.node, "declaration")


class AssessWithDeclarationsTests(TestCase):
    def setUp(self):
        self.requirement = Requirement(
            key="advanced_degree",
            label="Advanced degree",
            expression=Scalar(key="degree", op="==", value="masters"),
        )
        self.empty_bid = Bid.single(Portfolio(name="Acme"))

    def test_an_unanswered_requirement_still_goes_to_the_engine(self):
        assessment = assess_with_declarations([self.requirement], self.empty_bid, [None])

        verdict = assessment.results[0][1].verdict
        self.assertIs(verdict, Verdict.UNKNOWN)

    def test_a_declared_yes_settles_what_the_engine_could_not(self):
        """The whole point: the profile has no `degree` field, and without the
        switch this requirement is unanswerable forever."""
        assessment = assess_with_declarations([self.requirement], self.empty_bid, [True])

        self.assertIs(assessment.results[0][1].verdict, Verdict.SATISFIED)
        self.assertEqual(assessment.status, "eligible")

    def test_a_declared_no_blocks_the_bid(self):
        assessment = assess_with_declarations([self.requirement], self.empty_bid, [False])

        self.assertEqual(assessment.status, "blocked")
        self.assertIs(assessment.hard_eligibility_pass, False)

    def test_the_declaration_wins_over_a_profile_value(self):
        """Both are the vendor's word; the more specific one is the answer they
        gave to this exact criterion. A switch that a stale profile field could
        silently overrule would be a control that does not control anything.
        """
        bid = Bid.single(Portfolio(name="Acme", scalars={"degree": "bachelors"}))

        engine_only = assess_with_declarations([self.requirement], bid, [None])
        declared = assess_with_declarations([self.requirement], bid, [True])

        self.assertIs(engine_only.results[0][1].verdict, Verdict.FAILED)
        self.assertIs(declared.results[0][1].verdict, Verdict.SATISFIED)

    def test_none_is_not_false(self):
        """Collapsing the two would turn every question the vendor has not
        reached yet into a failure."""
        unanswered = assess_with_declarations([self.requirement], self.empty_bid, [None])
        refused = assess_with_declarations([self.requirement], self.empty_bid, [False])

        self.assertIs(unanswered.results[0][1].verdict, Verdict.UNKNOWN)
        self.assertIs(refused.results[0][1].verdict, Verdict.FAILED)

    def test_answers_are_positional_not_keyed(self):
        """Two layers can extract the same criterion into two rows. The vendor
        saw two questions and answered them separately."""
        second = parse_requirement({**DEGREE, "key": "advanced_degree"})

        assessment = assess_with_declarations(
            [self.requirement, second], self.empty_bid, [True, False]
        )

        self.assertIs(assessment.results[0][1].verdict, Verdict.SATISFIED)
        self.assertIs(assessment.results[1][1].verdict, Verdict.FAILED)


class DeclarationEndpointTests(APITestCase):
    def setUp(self):
        self.user = User.objects.create_user(
            username="vendor@example.com", email="vendor@example.com", password="pass-12345"
        )
        self.profile = VendorProfile.objects.create(user=self.user, name="Acme")
        self.client.force_login(self.user)

        self.notice = TenderNotice.objects.create(notice_id="OP-1", country="Uzbekistan")
        run = ExtractionRun.objects.create(
            notice=self.notice, layers="L2", status=ExtractionRun.Status.OK
        )
        self.requirement = TenderRequirement.objects.create(
            notice=self.notice,
            run=run,
            layer=TenderRequirement.Layer.L2,
            key="advanced_degree",
            label="Advanced university degree",
            expression=DEGREE["expression"],
            evidence_quote=DEGREE["evidence_quote"],
            grounding=TenderRequirement.Grounding.VERIFIED,
        )
        self.url = reverse("compliance:notice-declarations", args=["OP-1"])

    def test_a_vendor_can_state_that_they_meet_a_criterion(self):
        response = self.client.post(
            self.url,
            [{"requirement_id": self.requirement.pk, "satisfied": True}],
            format="json",
        )

        self.assertEqual(response.status_code, status.HTTP_200_OK)
        declaration = RequirementDeclaration.objects.get(profile=self.profile)
        self.assertTrue(declaration.satisfied)

    def test_answering_again_updates_rather_than_duplicates(self):
        for value in (True, False):
            self.client.post(
                self.url,
                [{"requirement_id": self.requirement.pk, "satisfied": value}],
                format="json",
            )

        self.assertEqual(RequirementDeclaration.objects.count(), 1)
        self.assertFalse(RequirementDeclaration.objects.get().satisfied)

    def test_sending_null_withdraws_an_answer(self):
        """A vendor who ticked a box by mistake must be able to un-answer it,
        not only to correct it to a "no" they never meant."""
        self.client.post(
            self.url,
            [{"requirement_id": self.requirement.pk, "satisfied": True}],
            format="json",
        )

        self.client.post(
            self.url,
            [{"requirement_id": self.requirement.pk, "satisfied": None}],
            format="json",
        )

        self.assertEqual(RequirementDeclaration.objects.count(), 0)

    def test_several_switches_are_saved_in_one_request(self):
        run = ExtractionRun.objects.create(
            notice=self.notice, layers="L2", status=ExtractionRun.Status.OK
        )
        second = TenderRequirement.objects.create(
            notice=self.notice,
            run=run,
            layer=TenderRequirement.Layer.L2,
            key="experience",
            expression={"kind": "scalar", "key": "years", "op": ">=", "value": 5},
            evidence_quote="At least five years.",
        )

        self.client.post(
            self.url,
            [
                {"requirement_id": self.requirement.pk, "satisfied": True},
                {"requirement_id": second.pk, "satisfied": False},
            ],
            format="json",
        )

        self.assertEqual(RequirementDeclaration.objects.count(), 2)

    def test_a_requirement_of_another_tender_is_refused(self):
        """The id comes from the client, so it is checked against this notice."""
        other = TenderNotice.objects.create(notice_id="OP-2")
        run = ExtractionRun.objects.create(
            notice=other, layers="L2", status=ExtractionRun.Status.OK
        )
        foreign = TenderRequirement.objects.create(
            notice=other,
            run=run,
            layer=TenderRequirement.Layer.L2,
            key="x",
            expression={"kind": "scalar", "key": "x", "op": ">=", "value": 1},
            evidence_quote="q",
        )

        response = self.client.post(
            self.url, [{"requirement_id": foreign.pk, "satisfied": True}], format="json"
        )

        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)
        self.assertEqual(RequirementDeclaration.objects.count(), 0)

    def test_an_anonymous_visitor_cannot_declare(self):
        self.client.logout()

        response = self.client.post(
            self.url, [{"requirement_id": self.requirement.pk, "satisfied": True}],
            format="json",
        )

        self.assertIn(
            response.status_code,
            (status.HTTP_401_UNAUTHORIZED, status.HTTP_403_FORBIDDEN),
        )
        self.assertEqual(RequirementDeclaration.objects.count(), 0)

    def test_the_assessment_reflects_the_answer_and_names_its_source(self):
        self.client.post(
            self.url,
            [{"requirement_id": self.requirement.pk, "satisfied": True}],
            format="json",
        )

        body = self.client.post(
            reverse("compliance:notice-assessment", args=["OP-1"])
        ).json()

        row = body["requirements"][0]
        self.assertEqual(row["verdict"], "satisfied")
        self.assertIs(row["declared"], True)
        self.assertEqual(row["decided_by"], "declaration")

    def test_an_unanswered_requirement_is_reported_as_engine_decided(self):
        body = self.client.post(
            reverse("compliance:notice-assessment", args=["OP-1"])
        ).json()

        row = body["requirements"][0]
        self.assertIsNone(row["declared"])
        self.assertEqual(row["decided_by"], "engine")

    def test_one_vendor_cannot_see_another_vendors_answers(self):
        other_user = User.objects.create_user(
            username="rival@example.com", email="rival@example.com", password="pass-12345"
        )
        other_profile = VendorProfile.objects.create(user=other_user, name="Rival")
        RequirementDeclaration.objects.create(
            profile=other_profile, requirement=self.requirement, satisfied=True
        )

        body = self.client.post(
            reverse("compliance:notice-assessment", args=["OP-1"])
        ).json()

        self.assertIsNone(body["requirements"][0]["declared"])
