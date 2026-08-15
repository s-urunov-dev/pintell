"""Tests for the compliance API.

The engine's own semantics are covered by ``test_expressions.py``. What is
tested here is everything the HTTP layer adds on top of it, and the tests are
weighted towards the promises that are easy to break by accident: that an
unknown never leaves as a failure, that the third state of
``hard_eligibility_pass`` survives JSON, that an ungrounded requirement never
reaches a bidder, and that a broken or empty corpus degrades instead of raising.
"""

from __future__ import annotations

from django.test import TestCase
from django.urls import reverse

from django.contrib.auth import get_user_model

from apps.compliance.models import ExtractionRun, TenderRequirement, VendorProfile
from apps.tenders.models import TenderNotice

User = get_user_model()

#: The criterion from Section III used throughout `test_expressions.py`, in its
#: stored form: at least two completed contracts worth US$5m each.
SIMILAR_CONTRACTS = {
    "kind": "count",
    "entity": "contracts",
    "op": ">=",
    "value": 2,
    "where": [
        {"field": "value_usd", "op": ">=", "value": 5_000_000},
        {"field": "successfully_completed", "op": "==", "value": True},
    ],
}

TURNOVER = {"kind": "scalar", "key": "annual_turnover_avg", "op": ">=", "value": 10_000_000}


class ComplianceApiTestCase(TestCase):
    """Fixtures shared by every test below."""

    def setUp(self) -> None:
        self.notice = TenderNotice.objects.create(
            notice_id="OP00456288",
            bid_description="Rehabilitation of 220 kV substations",
            country="Uzbekistan",
            notice_type="Invitation for Bids",
        )
        self.run = ExtractionRun.objects.create(
            notice=self.notice, layers="L1,L2", model="claude-haiku"
        )

    def add_requirement(self, **overrides) -> TenderRequirement:
        fields = {
            "notice": self.notice,
            "run": self.run,
            "layer": TenderRequirement.Layer.L2,
            "key": "annual_turnover_avg",
            "label": "Average annual turnover",
            "expression": TURNOVER,
            "evidence_quote": "average annual turnover of US$28 million",
            "source": "notice_body",
            "grounding": TenderRequirement.Grounding.VERIFIED,
        }
        fields.update(overrides)
        return TenderRequirement.objects.create(**fields)

    def sign_in(self, **profile_fields) -> VendorProfile:
        """Create a vendor account with a profile, and sign it in.

        Every assessment test needs one, and none of them care about the
        credentials — what they care about is that the request carries a
        session, because that is now the only way a profile reaches the API.
        """
        # Counted rather than fixed, so a test that signs in twice — switching
        # vendors mid-test — does not collide on the username.
        self._accounts = getattr(self, "_accounts", 0) + 1
        user = User.objects.create_user(
            username=profile_fields.pop("email", f"vendor{self._accounts}@example.uz"),
            password="vendor-pass-123",
        )
        profile = VendorProfile.objects.create(user=user, **profile_fields)
        self.client.force_login(user)
        return profile

    def assess(self):
        """The whole request: a tender in the URL, a vendor in the session."""
        return self.client.post(
            reverse("compliance:notice-assessment", args=[self.notice.pk])
        )


class VendorProfileTests(ComplianceApiTestCase):
    """The profile a vendor owns, reached without naming it."""

    def test_a_signed_in_vendor_reads_and_amends_their_own_profile(self):
        self.sign_in(name="Alpha Qurilish", country="Uzbekistan")
        url = reverse("compliance:vendor-profile")

        self.assertEqual(self.client.get(url).json()["name"], "Alpha Qurilish")

        patched = self.client.patch(
            url,
            data={"scalars": {"annual_turnover_avg": 30_000_000}},
            content_type="application/json",
        )
        self.assertEqual(patched.status_code, 200)
        self.assertEqual(patched.json()["scalars"]["annual_turnover_avg"], 30_000_000)

    def test_a_visitor_who_is_not_signed_in_reaches_no_profile_at_all(self):
        """The fix for Q8: there is no id to guess, and no route without a session."""
        VendorProfile.objects.create(name="Somebody else")

        response = self.client.get(reverse("compliance:vendor-profile"))

        self.assertEqual(response.status_code, 403)

    def test_a_blank_field_is_stored_as_undeclared_rather_than_as_an_answer(self):
        """An empty form field must reach the engine as silence, not as "".

        `""` is a declared value that compares as unknown against every
        threshold forever, so storing it would look answered in the UI while
        being permanently unreadable.
        """
        self.sign_in(name="Beta")

        response = self.client.patch(
            reverse("compliance:vendor-profile"),
            data={"scalars": {"annual_turnover_avg": "  ", "liquid_assets": 0}},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        scalars = response.json()["scalars"]
        self.assertNotIn("annual_turnover_avg", scalars)
        # Zero is a real declaration and must survive.
        self.assertEqual(scalars["liquid_assets"], 0)

    def test_an_empty_contract_list_is_kept_because_it_means_something(self):
        """"I have no contracts" is a failure; saying nothing is unknown."""
        self.sign_in(name="Gamma")

        response = self.client.patch(
            reverse("compliance:vendor-profile"),
            data={"collections": {"contracts": []}},
            content_type="application/json",
        )

        self.assertEqual(response.json()["collections"], {"contracts": []})

    def test_a_value_the_engine_cannot_compare_is_rejected(self):
        self.sign_in(name="Delta")

        response = self.client.patch(
            reverse("compliance:vendor-profile"),
            data={"scalars": {"turnover": {"amount": 5}}},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_records_must_be_a_list_of_flat_objects(self):
        self.sign_in(name="Epsilon")

        response = self.client.patch(
            reverse("compliance:vendor-profile"),
            data={"collections": {"contracts": "three"}},
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 400)

    def test_a_profile_cannot_be_deleted_through_the_api(self):
        """The retention policy is an open legal question — see the model."""
        self.sign_in(name="Zeta")

        response = self.client.delete(reverse("compliance:vendor-profile"))

        self.assertEqual(response.status_code, 405)


class AssessmentVerdictTests(ComplianceApiTestCase):
    def test_a_qualifying_vendor_is_eligible_and_says_why(self):
        self.add_requirement()
        profile = self.sign_in(
            name="Alpha", scalars={"annual_turnover_avg": 28_000_000}
        )

        body = self.assess().json()

        self.assertEqual(body["status"], "eligible")
        self.assertIs(body["hard_eligibility_pass"], True)
        self.assertEqual(body["requirements"][0]["verdict"], "satisfied")

    def test_an_undeclared_value_is_unknown_and_never_a_failure(self):
        """The single most important behaviour in the feature."""
        self.add_requirement()
        profile = self.sign_in(name="Alpha")

        body = self.assess().json()

        self.assertEqual(body["requirements"][0]["verdict"], "unknown")
        self.assertEqual(body["status"], "incomplete")
        self.assertEqual(body["counts"]["failed"], 0)
        self.assertEqual(body["counts"]["blockers"], 0)

    def test_a_pending_verdict_survives_json_as_null_not_as_false(self):
        """`None` is a state. Coercing it would reject a vendor nobody judged."""
        self.add_requirement()
        profile = self.sign_in(name="Alpha")

        body = self.assess().json()

        self.assertIn("hard_eligibility_pass", body)
        self.assertIsNone(body["hard_eligibility_pass"])

    def test_an_unknown_requirement_names_the_value_that_would_settle_it(self):
        self.add_requirement()
        profile = self.sign_in(name="Alpha")

        missing = self.assess().json()["requirements"][0]["missing"]

        self.assertEqual(missing, [{
            "kind": "scalar", "key": "annual_turnover_avg", "label": "", "unit": "",
        }])

    def test_a_settled_requirement_is_not_given_a_shopping_list(self):
        """A failure must not read as "supply these and you will pass"."""
        self.add_requirement()
        profile = self.sign_in(
            name="Alpha", scalars={"annual_turnover_avg": 1_000}
        )

        requirement = self.assess().json()["requirements"][0]

        self.assertEqual(requirement["verdict"], "failed")
        self.assertEqual(requirement["missing"], [])

    def test_a_missing_record_type_is_asked_for_as_a_whole(self):
        self.add_requirement(
            key="similar_contracts_count",
            label="Similar contracts",
            expression=SIMILAR_CONTRACTS,
        )
        profile = self.sign_in(name="Alpha")

        missing = self.assess().json()["requirements"][0]["missing"]

        self.assertEqual([entry["kind"] for entry in missing], ["collection"])
        self.assertEqual(missing[0]["entity"], "contracts")

    def test_records_missing_a_filtered_field_are_named_field_by_field(self):
        """This is why a shortfall came back unknown rather than refused."""
        self.add_requirement(
            key="similar_contracts_count", expression=SIMILAR_CONTRACTS
        )
        profile = self.sign_in(
            name="Alpha",
            collections={"contracts": [{"value_usd": 6_000_000}]},
        )

        requirement = self.assess().json()["requirements"][0]

        self.assertEqual(requirement["verdict"], "unknown")
        self.assertEqual(
            [(entry["kind"], entry["field"]) for entry in requirement["missing"]],
            [("record_field", "successfully_completed")],
        )

    def test_a_mandatory_failure_blocks_while_a_preference_does_not(self):
        self.add_requirement(key="turnover_pref", is_mandatory=False)
        profile = self.sign_in(
            name="Alpha", scalars={"annual_turnover_avg": 1_000}
        )

        body = self.assess().json()

        self.assertEqual(body["requirements"][0]["verdict"], "failed")
        self.assertEqual(body["counts"]["blockers"], 0)
        self.assertIs(body["hard_eligibility_pass"], True)


class AssessmentExplanationTests(ComplianceApiTestCase):
    def test_every_requirement_carries_the_quote_it_came_from(self):
        self.add_requirement()
        profile = self.sign_in(name="Alpha")

        requirement = self.assess().json()["requirements"][0]

        self.assertEqual(
            requirement["evidence_quote"], "average annual turnover of US$28 million"
        )
        self.assertEqual(requirement["source"], "notice_body")

    def test_the_verdict_arrives_with_enough_working_to_recompute_it(self):
        """The product's claim: a verdict a reader can check by hand."""
        self.add_requirement()
        profile = self.sign_in(
            name="Alpha", scalars={"annual_turnover_avg": 28_000_000}
        )

        requirement = self.assess().json()["requirements"][0]

        # What was asked, what happened, and the numbers on both sides of it.
        self.assertEqual(requirement["expression"]["value"], 10_000_000)
        self.assertEqual(requirement["trace"]["verdict"], "satisfied")
        self.assertIn("28000000", requirement["explanation"].replace(",", ""))

    def test_the_extraction_layer_and_grounding_state_travel_with_the_row(self):
        self.add_requirement(layer=TenderRequirement.Layer.L3)
        profile = self.sign_in(name="Alpha")

        requirement = self.assess().json()["requirements"][0]

        self.assertEqual(requirement["layer"], "L3")
        self.assertEqual(requirement["grounding"], "verified")
        self.assertEqual(requirement["run"]["layers"], "L1,L2")


class RequirementSelectionTests(ComplianceApiTestCase):
    def test_an_ungrounded_requirement_never_reaches_a_bidder(self):
        """Those rows measure hallucination; they are not statements of fact."""
        self.add_requirement(
            key="invented_requirement",
            grounding=TenderRequirement.Grounding.NOT_FOUND,
        )
        profile = self.sign_in(name="Alpha")

        body = self.assess().json()

        self.assertEqual(body["requirements"], [])
        self.assertEqual(body["excluded"]["not_found"], 1)

    def test_the_requirements_endpoint_withholds_them_too(self):
        self.add_requirement(grounding=TenderRequirement.Grounding.NOT_FOUND)

        body = self.client.get(
            reverse("compliance:notice-requirements", args=[self.notice.pk])
        ).json()

        self.assertEqual(body["requirements"], [])
        self.assertEqual(body["excluded"]["not_found"], 1)

    def test_a_re_extracted_criterion_is_shown_once_from_the_newest_run(self):
        self.add_requirement(evidence_quote="the older wording")
        newer_run = ExtractionRun.objects.create(
            notice=self.notice, layers="L1,L2,L3", model="claude-sonnet"
        )
        self.add_requirement(
            run=newer_run,
            layer=TenderRequirement.Layer.L3,
            evidence_quote="the newer wording",
        )
        profile = self.sign_in(name="Alpha")

        body = self.assess().json()

        self.assertEqual(len(body["requirements"]), 1)
        self.assertEqual(body["requirements"][0]["evidence_quote"], "the newer wording")
        self.assertEqual(body["excluded"]["superseded"], 1)


class DegradationTests(ComplianceApiTestCase):
    def test_a_notice_with_nothing_extracted_is_unrated_not_an_error(self):
        profile = self.sign_in(name="Alpha")

        response = self.assess()

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["status"], "unrated")
        self.assertEqual(body["requirements"], [])
        self.assertEqual(body["counts"]["total"], 0)

    def test_a_requirement_that_will_not_parse_costs_only_itself(self):
        """One bad extraction must not take down the page around it."""
        self.add_requirement()
        self.add_requirement(
            key="broken", expression={"kind": "wormhole", "value": 3}
        )
        profile = self.sign_in(
            name="Alpha", scalars={"annual_turnover_avg": 28_000_000}
        )

        body = self.assess().json()

        self.assertEqual(len(body["requirements"]), 1)
        self.assertEqual(body["excluded"]["unparsable"], 1)
        self.assertEqual(body["requirements"][0]["verdict"], "satisfied")

    def test_an_unknown_notice_is_a_404(self):
        self.sign_in(name="Alpha")

        response = self.client.post(
            reverse("compliance:notice-assessment", args=["OP00000000"])
        )

        self.assertEqual(response.status_code, 404)


class SignedOutTests(ComplianceApiTestCase):
    """What a visitor can reach without an account, and what they cannot."""

    def test_an_assessment_requires_a_session(self):
        self.add_requirement()

        response = self.client.post(
            reverse("compliance:notice-assessment", args=[self.notice.pk])
        )

        self.assertEqual(response.status_code, 403)

    def test_the_tender_criteria_stay_public(self):
        """A vendor must be able to read what is asked before signing up."""
        self.add_requirement()

        response = self.client.get(
            reverse("compliance:notice-requirements", args=[self.notice.pk])
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["requirements"]), 1)
