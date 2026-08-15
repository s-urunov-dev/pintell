"""The second answer the extraction call returns, and where it may not reach.

D20 asks one call two questions about the same sentences. These tests hold the
three properties that makes it safe to do so:

* the expert half never becomes a requirement, so no verdict moves;
* a position obeys the same evidence rule as a requirement;
* the role that comes back is a key in the directory, because the schema said
  so — never a string somebody has to match afterwards.
"""

from __future__ import annotations

import json

from django.test import SimpleTestCase, TestCase

from apps.compliance import l2, llm, pipeline
from apps.compliance.models import TenderExpertPosition, TenderRequirement
from apps.compliance.tests.test_l2 import (
    FakeClient,
    IRREGULAR_NOTICE,
    YEARS_QUOTE,
    requirement_row,
    response,
)
from apps.experts.models import Expert, ExpertType
from apps.experts.vocabulary import UNMAPPED, role_slugs
from apps.tenders.models import TenderNotice

# A consulting REOI's team paragraph, and the sentence a well-behaved model
# quotes out of it. Written to be the ordinary case rather than a hard one:
# what is under test is the plumbing, not the reading.
TEAM_NOTICE = (
    "<p>The Consultant shall field a team including a Team Leader with at least "
    "15 years of experience and two Resettlement Specialists.</p>"
)
TEAM_QUOTE = (
    "The Consultant shall field a team including a Team Leader with at least 15 "
    "years of experience and two Resettlement Specialists."
)


def expert_row(title: str, role: str, quote: str, **overrides) -> dict:
    row = {
        "title": title,
        "role": role,
        "count": 1,
        "is_mandatory": True,
        "evidence_quote": quote,
    }
    row.update(overrides)
    return row


def answer(*, requirements=(), experts=()) -> str:
    return json.dumps(
        {"requirements": list(requirements), "expert_positions": list(experts)}
    )


class SchemaTests(SimpleTestCase):
    """The vocabulary reaches the model through the schema, not the prompt."""

    def test_the_roles_are_an_enum_the_model_must_answer_inside(self):
        schema = llm.build_schema(["team-leader", "gender-specialist", UNMAPPED])
        role = schema["properties"]["expert_positions"]["items"]["properties"]["role"]

        self.assertEqual(
            role["enum"], ["team-leader", "gender-specialist", UNMAPPED]
        )

    def test_without_a_vocabulary_the_older_question_is_asked_unchanged(self):
        """A deployment with no taxonomy loaded still extracts requirements."""
        self.assertEqual(llm.build_schema([]), llm.REQUIREMENT_SCHEMA)
        self.assertNotIn("expert_positions", llm.build_schema(None)["properties"])

    def test_the_frozen_system_prompt_carries_no_role_names(self):
        """The vocabulary varies with the database; the cached prefix must not.

        An interpolated role list would invalidate the prompt cache on every
        call, which is the cost the schema placement exists to avoid.
        """
        for slug in ("team-leader", "gender-specialist", "auditor"):
            self.assertNotIn(slug, llm.SYSTEM_PROMPT)

    def test_asking_for_experts_leaves_the_requirement_half_untouched(self):
        with_experts = llm.build_schema(["team-leader", UNMAPPED])

        self.assertEqual(
            with_experts["properties"]["requirements"],
            llm.REQUIREMENT_SCHEMA["properties"]["requirements"],
        )

    def test_the_schema_uses_no_keyword_output_config_rejects(self):
        """`minimum` on the expert `count` failed every L2 and L3 call.

        The API answers 400 "For 'integer' type, property 'minimum' is not
        supported" and refuses the whole request, so a single validation word
        the code did not need took the paid layers down completely — silently,
        because a failed run looks like a tender with nothing to extract. The
        assertion is over the whole schema rather than that one field: the next
        person adding a bound will reach for `maximum` or `maxLength`.
        """
        unsupported = {
            "minimum",
            "maximum",
            "exclusiveMinimum",
            "exclusiveMaximum",
            "minLength",
            "maxLength",
            "minItems",
            "maxItems",
            "multipleOf",
            "uniqueItems",
            "pattern",
        }

        def walk(node, path="schema"):
            if isinstance(node, dict):
                for key, value in node.items():
                    self.assertNotIn(
                        key, unsupported, f"{path}.{key} is rejected by the API"
                    )
                    walk(value, f"{path}.{key}")
            elif isinstance(node, list):
                for i, value in enumerate(node):
                    walk(value, f"{path}[{i}]")

        walk(llm.build_schema(["team-leader", "gender-specialist", UNMAPPED]))
        walk(llm.REQUIREMENT_SCHEMA)


class LayerTests(SimpleTestCase):
    """What L2 does with the second array."""

    def test_positions_come_back_beside_the_requirements_not_among_them(self):
        client = FakeClient(
            response(
                answer(
                    requirements=[requirement_row("years_experience", YEARS_QUOTE)],
                    experts=[expert_row("Team Leader", "team-leader", YEARS_QUOTE)],
                )
            )
        )

        result = l2.extract(IRREGULAR_NOTICE, client=client, role_slugs=["team-leader"])

        self.assertEqual([r.key for r in result.requirements], ["years_experience"])
        self.assertEqual([e.title for e in result.experts], ["Team Leader"])

    def test_the_count_a_document_states_is_carried(self):
        client = FakeClient(
            response(
                answer(
                    experts=[
                        expert_row(
                            "Resettlement Specialist",
                            "resettlement-specialist",
                            YEARS_QUOTE,
                            count=2,
                        )
                    ]
                )
            )
        )

        result = l2.extract(IRREGULAR_NOTICE, client=client, role_slugs=["x"])

        self.assertEqual(result.experts[0].count, 2)

    def test_a_position_with_no_quote_is_never_proposed(self):
        """Same rule as a requirement: silence rather than an unbacked claim."""
        client = FakeClient(
            response(answer(experts=[expert_row("Team Leader", "team-leader", "  ")]))
        )

        result = l2.extract(IRREGULAR_NOTICE, client=client, role_slugs=["team-leader"])

        self.assertEqual(result.experts, [])

    def test_a_quote_absent_from_what_was_sent_is_kept_and_counted(self):
        """The hallucination signal, held apart from the requirement one."""
        client = FakeClient(
            response(
                answer(
                    experts=[
                        expert_row(
                            "Team Leader",
                            "team-leader",
                            "A sentence this notice does not contain.",
                        )
                    ]
                )
            )
        )

        result = l2.extract(IRREGULAR_NOTICE, client=client, role_slugs=["team-leader"])

        self.assertEqual(len(result.experts), 1)
        self.assertEqual(result.notes["expert_quotes_not_in_source"], 1)
        self.assertEqual(result.notes["quotes_not_in_source"], 0)

    def test_an_absurd_count_is_clamped_rather_than_shown_to_a_vendor(self):
        client = FakeClient(
            response(
                answer(
                    experts=[
                        expert_row("Team Leader", "team-leader", YEARS_QUOTE, count=5000)
                    ]
                )
            )
        )

        result = l2.extract(IRREGULAR_NOTICE, client=client, role_slugs=["team-leader"])

        self.assertEqual(result.experts[0].count, 99)

    def test_no_vocabulary_means_no_expert_array_in_the_request(self):
        client = FakeClient(
            response(answer(requirements=[requirement_row("years_experience", YEARS_QUOTE)]))
        )

        l2.extract(IRREGULAR_NOTICE, client=client)

        schema = client.calls[-1]["output_config"]["format"]["schema"]
        self.assertNotIn("expert_positions", schema["properties"])


class VocabularyTests(TestCase):
    """The bridge from a tender's words to a directory row."""

    fixtures = ["expert_types"]

    def test_the_vocabulary_is_every_role_plus_an_escape_hatch(self):
        slugs = role_slugs()

        self.assertEqual(len(slugs), 37)
        self.assertEqual(slugs[-1], UNMAPPED)
        self.assertIn("gender-specialist", slugs)

    def test_families_are_never_offered_as_an_answer(self):
        """A person is a Gender Specialist, never an 'Environmental and social'."""
        slugs = set(role_slugs())

        for family in ExpertType.objects.families():
            self.assertNotIn(family.slug, slugs)

    def test_the_order_is_stable_so_two_runs_build_the_same_schema(self):
        self.assertEqual(role_slugs(), role_slugs())


class PersistenceTests(TestCase):
    """What reaches the database, and what it is forbidden from reaching."""

    fixtures = ["expert_types"]

    def setUp(self):
        self.notice = TenderNotice.objects.create(
            notice_id="OP-EXPERTS-1",
            bid_description="Consulting services",
            notice_text_sanitized=TEAM_NOTICE,
        )

    def _run(self, *experts, requirements=()):
        client = FakeClient(
            response(answer(requirements=requirements, experts=list(experts)))
        )
        from apps.compliance.tests.test_pipeline import _layer
        from types import ModuleType

        stub = ModuleType("apps.compliance.l2")

        def extract(text, **kwargs):
            return l2.extract(text, client=client, **kwargs)

        stub.extract = extract
        with _layer("l2", stub):
            return pipeline.extract_for_notice(self.notice, layers=("L2",))

    def test_a_position_is_stored_against_the_notice_and_its_run(self):
        run = self._run(expert_row("Team Leader", "team-leader", TEAM_QUOTE))

        position = TenderExpertPosition.objects.get(notice=self.notice)
        self.assertEqual(position.title, "Team Leader")
        self.assertEqual(position.role.slug, "team-leader")
        self.assertEqual(position.run, run)
        self.assertEqual(position.layer, "L2")

    def test_a_position_never_becomes_a_requirement(self):
        """The invariant the separate table exists to hold."""
        self._run(expert_row("Team Leader", "team-leader", TEAM_QUOTE))

        self.assertEqual(TenderRequirement.objects.count(), 0)
        self.assertEqual(TenderExpertPosition.objects.count(), 1)

    def test_a_role_the_taxonomy_has_no_row_for_is_stored_unmapped(self):
        """A gap in the CEO's list, kept as a measurement rather than forced."""
        self._run(expert_row("Underwater Welding Inspector", UNMAPPED, TEAM_QUOTE))

        position = TenderExpertPosition.objects.get(notice=self.notice)
        self.assertIsNone(position.role)
        self.assertEqual(position.title, "Underwater Welding Inspector")

    def test_a_position_whose_quote_is_not_in_the_notice_is_stored_and_withheld(self):
        self._run(
            expert_row("Team Leader", "team-leader", "Not a sentence in this notice.")
        )

        position = TenderExpertPosition.objects.get(notice=self.notice)
        self.assertEqual(position.grounding, TenderRequirement.Grounding.NOT_FOUND)
        self.assertFalse(position.is_usable)

    def test_a_grounded_position_is_usable(self):
        self._run(expert_row("Team Leader", "team-leader", TEAM_QUOTE))

        position = TenderExpertPosition.objects.get(notice=self.notice)
        self.assertEqual(position.grounding, TenderRequirement.Grounding.VERIFIED)
        self.assertTrue(position.is_usable)

    def test_finding_only_positions_is_not_a_failed_run(self):
        """A consulting REOI naming a team and no threshold is an ordinary shape."""
        run = self._run(expert_row("Team Leader", "team-leader", TEAM_QUOTE))

        self.assertEqual(run.status, run.Status.OK)

    def test_retiring_a_role_keeps_the_evidence_it_was_asked_for(self):
        self._run(expert_row("Team Leader", "team-leader", TEAM_QUOTE))

        ExpertType.objects.filter(slug="team-leader").delete()

        position = TenderExpertPosition.objects.get(notice=self.notice)
        self.assertIsNone(position.role)
        self.assertEqual(position.title, "Team Leader")

    def test_deleting_a_role_does_not_delete_the_people_or_the_family(self):
        """PROTECT covers the family; the experts simply lose one tag."""
        expert = Expert.objects.create(full_name="Jane Doe")
        expert.types.set(ExpertType.objects.filter(slug="team-leader"))

        ExpertType.objects.filter(slug="team-leader").delete()

        expert.refresh_from_db()
        self.assertEqual(expert.types.count(), 0)
        self.assertTrue(ExpertType.objects.filter(slug="project-management").exists())


class NoticeExpertsEndpointTests(TestCase):
    """What a vendor reads: what the tender asks, and who we hold."""

    fixtures = ["expert_types"]

    def setUp(self):
        self.notice = TenderNotice.objects.create(
            notice_id="OP-EXPERTS-API",
            bid_description="Consulting services",
            notice_text_sanitized=TEAM_NOTICE,
        )
        self.run = self.notice.extraction_runs.create(layers="L2", model="test")

        self.leader = Expert.objects.create(full_name="Aziza Karimova")
        self.leader.types.set(ExpertType.objects.filter(slug="team-leader"))

    def _position(self, title, role_slug, **overrides):
        fields = {
            "notice": self.notice,
            "run": self.run,
            "layer": "L2",
            "title": title,
            "role": ExpertType.objects.filter(slug=role_slug).first(),
            "evidence_quote": TEAM_QUOTE,
            "grounding": TenderRequirement.Grounding.VERIFIED,
        }
        fields.update(overrides)
        return TenderExpertPosition.objects.create(**fields)

    def _get(self):
        return self.client.get(f"/api/compliance/notices/{self.notice.pk}/experts/")

    def test_the_endpoint_is_public(self):
        """Reading what a tender demands never requires an account."""
        self.assertEqual(self._get().status_code, 200)

    def test_positions_and_candidates_arrive_as_different_fields(self):
        """The separation is the design: a quote and a suggestion never merge."""
        self._position("Team Leader", "team-leader")

        body = self._get().json()

        self.assertEqual(body["positions"][0]["title"], "Team Leader")
        self.assertEqual(body["positions"][0]["evidence_quote"], TEAM_QUOTE)
        self.assertEqual(
            [row["full_name"] for row in body["candidates"]["team-leader"]],
            ["Aziza Karimova"],
        )

    def test_a_position_the_taxonomy_cannot_file_still_reaches_the_vendor(self):
        self._position("Underwater Welding Inspector", "", role=None)

        body = self._get().json()

        self.assertEqual(body["positions"][0]["title"], "Underwater Welding Inspector")
        self.assertIsNone(body["positions"][0]["role"])
        self.assertEqual(body["candidates"], {})

    def test_an_ungrounded_position_is_counted_but_never_shown(self):
        self._position(
            "Ghost Specialist",
            "gender-specialist",
            grounding=TenderRequirement.Grounding.NOT_FOUND,
        )

        body = self._get().json()

        self.assertEqual(body["positions"], [])
        self.assertEqual(body["excluded"]["not_found"], 1)
        self.assertNotIn("Ghost", str(body))

    def test_the_same_position_found_by_two_runs_is_shown_once(self):
        later = self.notice.extraction_runs.create(layers="L3", model="test")
        self._position("Team Leader", "team-leader")
        self._position("Team Leader", "team-leader", run=later, layer="L3")

        body = self._get().json()

        self.assertEqual(len(body["positions"]), 1)
        self.assertEqual(body["positions"][0]["layer"], "L3")
        self.assertEqual(body["excluded"]["superseded"], 1)

    def test_two_different_titles_are_two_seats_even_under_one_role(self):
        """Collapsing on role would quietly halve a team."""
        self._position("Environmental Specialist", "es-safeguards-specialist")
        self._position("Social Specialist", "es-safeguards-specialist")

        body = self._get().json()

        self.assertEqual(len(body["positions"]), 2)

    def test_a_notice_nobody_extracted_answers_empty_rather_than_404(self):
        body = self._get().json()

        self.assertEqual(body["positions"], [])
        self.assertEqual(body["candidates"], {})

    def test_no_verdict_field_appears_anywhere_in_the_payload(self):
        """Positions sit beside the verdict, never inside it."""
        self._position("Team Leader", "team-leader")

        body = str(self._get().json())

        for word in ("satisfied", "hard_eligibility_pass", "eligible"):
            self.assertNotIn(word, body)
