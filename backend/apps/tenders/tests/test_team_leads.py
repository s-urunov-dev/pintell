"""Enriching the World Bank team leads behind the third contact tier.

The tier starts as a bare name, so everything here is about how far it is
legitimate to go from that name — and where the line is. Two rules are load
bearing and each has a test that would fail if someone relaxed them:

* a pattern-derived address is never labelled as confirmed, and
* nothing off the staff domain, and no data-broker page, is ever stored for a
  named individual.

The provider is stubbed rather than called: these assert the contract this
module imposes on the answer, not any model's behaviour. Which model produced
it is deliberately invisible here — the answer format is plain labelled lines,
so Claude and Gemini are interchangeable behind :mod:`.providers`.
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase, TestCase

from apps.tenders.models import TeamLeadProfile
from apps.tenders.services.ai import people


class EmailPatternTests(SimpleTestCase):
    def test_first_initial_and_surname_at_the_staff_domain(self):
        self.assertEqual(people.derive_work_email("Mohini Kak"), "mkak@worldbank.org")
        self.assertEqual(people.derive_work_email("Koji Nishida"), "knishida@worldbank.org")

    def test_middle_names_do_not_enter_the_address(self):
        self.assertEqual(
            people.derive_work_email("Solene Marie Paule Rougeaux"),
            "srougeaux@worldbank.org",
        )

    def test_particles_are_not_treated_as_the_surname(self):
        self.assertEqual(people.derive_work_email("Jan van Dijk"), "jdijk@worldbank.org")

    def test_a_single_name_yields_nothing_rather_than_a_guess(self):
        self.assertEqual(people.derive_work_email("Madonna"), "")
        self.assertEqual(people.derive_work_email(""), "")


class AnswerParsingTests(SimpleTestCase):
    def test_a_published_staff_address_is_taken_as_confirmed(self):
        result = people._parse_answer(
            "TITLE: Senior Energy Specialist\n"
            "UNIT: Energy & Extractives\n"
            "EMAIL: knishida@worldbank.org\n"
        )
        self.assertEqual(result.title, "Senior Energy Specialist")
        self.assertEqual(result.email, "knishida@worldbank.org")
        self.assertEqual(result.email_source, TeamLeadProfile.EmailSource.VERIFIED)

    def test_an_address_off_the_staff_domain_is_discarded(self):
        # A personal mailbox for a named person is exactly what this module
        # must not store, however the model came by it.
        result = people._parse_answer("EMAIL: koji.nishida@gmail.com")
        self.assertEqual(result.email, "")
        self.assertEqual(result.email_source, "")

    def test_data_broker_pages_are_not_stored_as_a_profile(self):
        result = people._parse_answer(
            "LINK: https://rocketreach.co/koji-nishida\n"
            "LINK: https://www.zoominfo.com/p/koji-nishida\n"
        )
        self.assertEqual(result.links, [])

    def test_a_bank_page_is_kept_and_labelled(self):
        result = people._parse_answer("LINK: https://www.worldbank.org/en/about/people/k/koji")
        self.assertEqual(result.links, [
            {"url": "https://www.worldbank.org/en/about/people/k/koji", "kind": "worldbank"},
        ])

    def test_a_paper_in_the_bank_repository_is_not_a_staff_page(self):
        # openknowledge.worldbank.org is a worldbank.org host, so the broader
        # institutional test would claim it and the UI would offer "Profile".
        result = people._parse_answer(
            "LINK: https://openknowledge.worldbank.org/entities/publication/abc"
        )
        self.assertEqual(result.links[0]["kind"], "publication")

    def test_a_model_saying_it_does_not_know_is_not_a_value(self):
        # Observed live: the prompt asks for the line to be omitted, and Gemini
        # answers the question literally instead. Stored as-is, that sentence
        # would be shown to a user as this person's duty station.
        result = people._parse_answer(
            "TITLE: Senior Health Specialist\n"
            "LOCATION: Not specified in the provided search results.\n"
            "UNIT: N/A\n"
        )
        self.assertEqual(result.title, "Senior Health Specialist")
        self.assertEqual(result.location, "")
        self.assertEqual(result.unit, "")

    def test_a_hedged_value_is_not_a_published_fact(self):
        # Observed live: Gemini reasoned inside the field rather than leaving
        # it out. Shown as-is this reads as the person's unit.
        result = people._parse_answer(
            'UNIT: Energy (implied by "Senior Energy Specialist" and project '
            "focus, though a specific Global Practice name isn't explicitly "
            "stated on a single page for him.)\n"
            "LOCATION: (Not explicitly stated on published World Bank pages)\n"
        )
        self.assertEqual(result.unit, "")
        self.assertEqual(result.location, "")

    def test_a_sentence_is_never_a_job_title(self):
        long_title = "He is currently working as " + "a specialist " * 12
        self.assertEqual(people._parse_answer(f"TITLE: {long_title}").title, "")

    def test_a_trailing_bracket_that_belongs_to_the_text_survives(self):
        # Unwrapping a parenthetical must not eat an acronym's own bracket.
        result = people._parse_answer(
            "UNIT: Finance, Competitiveness & Innovation Global Practice (FCI)"
        )
        self.assertEqual(
            result.unit, "Finance, Competitiveness & Innovation Global Practice (FCI)"
        )

    def test_a_summary_may_be_prose(self):
        # The one field where a sentence is the point.
        result = people._parse_answer("SUMMARY: Works on social protection in Central Asia.")
        self.assertEqual(result.summary, "Works on social protection in Central Asia.")

    def test_an_unidentified_person_yields_nothing(self):
        self.assertFalse(people._parse_answer("NONE").found)

    def test_an_unrelated_page_is_not_a_profile(self):
        # A news article that merely mentions someone places them nowhere.
        self.assertEqual(people._parse_answer("LINK: https://news.example.com/x").links, [])


class LookupTests(SimpleTestCase):
    def test_without_a_provider_the_derived_address_still_comes_back(self):
        with patch.object(people, "search_enabled", return_value=False):
            result = people.look_up_team_lead("Mohini Kak")
        self.assertEqual(result.email, "mkak@worldbank.org")
        self.assertEqual(result.email_source, TeamLeadProfile.EmailSource.PATTERN)
        self.assertLess(result.email_confidence, 0.8)
        # Never ran, as opposed to ran and found nothing.
        self.assertFalse(result.checked)

    def test_a_published_address_supersedes_the_derived_one(self):
        with patch.object(people, "search_enabled", return_value=True), \
             patch.object(
                 people, "search_answer",
                 return_value="TITLE: Senior Energy Specialist\n"
                              "EMAIL: knishida2@worldbank.org",
             ):
            result = people.look_up_team_lead("Koji Nishida")
        self.assertEqual(result.email, "knishida2@worldbank.org")
        self.assertEqual(result.email_source, TeamLeadProfile.EmailSource.VERIFIED)

    def test_the_derived_address_fills_in_when_nothing_was_published(self):
        with patch.object(people, "search_enabled", return_value=True), \
             patch.object(
                 people, "search_answer", return_value="TITLE: Senior Energy Specialist"
             ):
            result = people.look_up_team_lead("Koji Nishida")
        self.assertEqual(result.email, "knishida@worldbank.org")
        self.assertEqual(result.email_source, TeamLeadProfile.EmailSource.PATTERN)

    def test_a_failing_lookup_degrades_instead_of_raising(self):
        with patch.object(people, "search_enabled", return_value=True), \
             patch.object(people, "search_answer", side_effect=RuntimeError("boom")):
            result = people.look_up_team_lead("Koji Nishida")
        self.assertEqual(result.email, "knishida@worldbank.org")
        self.assertFalse(result.checked)


class StorageTests(TestCase):
    def test_a_lookup_is_stored_once_per_person(self):
        with patch.object(people, "search_enabled", return_value=True), \
             patch.object(
                 people, "search_answer",
                 return_value="TITLE: Senior Health Specialist\n"
                              "UNIT: Health, Nutrition & Population",
             ) as search:
            people.enrich_team_lead("Mohini Kak")
            # A second call for the same person must not spend a second
            # search — the name is the key, not the notice it appeared on.
            people.enrich_team_lead("Mohini Kak")
            self.assertEqual(search.call_count, 1)

        profile = TeamLeadProfile.objects.get()
        self.assertEqual(profile.title, "Senior Health Specialist")
        self.assertIsNotNone(profile.checked_at)

    def test_spelling_drift_between_projects_collapses_to_one_row(self):
        with patch.object(people, "search_enabled", return_value=False):
            people.enrich_team_lead("Mirodil Khusanov")
            people.enrich_team_lead("Mirodil Xusanov")
        self.assertEqual(TeamLeadProfile.objects.count(), 1)

    def test_profiles_for_returns_nothing_for_an_unknown_name(self):
        self.assertEqual(people.profiles_for(["Nobody At All"]), {})

    def test_profiles_for_is_keyed_by_the_published_name(self):
        with patch.object(people, "search_enabled", return_value=False):
            people.enrich_team_lead("Mohini Kak")
        found = people.profiles_for(["Mohini Kak"])
        self.assertIn("Mohini Kak", found)
        self.assertFalse(found["Mohini Kak"]["email_confirmed"])
