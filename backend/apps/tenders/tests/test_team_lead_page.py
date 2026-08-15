"""The team lead profile page.

Two things this endpoint must get right. First, the half that carries the page:
which projects a person leads, and every tender those projects issued. That is
matched on a folded name, so it has to survive both spelling drift between
projects and the near-miss surname that must *not* match.

Second, what the page refuses to become. The stored shape has no field for a
personal social account, messaging handle or photograph, and the payload is
written out explicitly rather than dumped from the model — a test pins that,
because the cheapest way for this page to turn into a dossier is for someone to
add a field and have it serialised by accident.
"""

from __future__ import annotations

from django.test import TestCase
from django.utils import timezone

from apps.tenders.models import ProjectProfile, TeamLeadProfile, TenderNotice
from apps.tenders.team_leads import profile_payload, roster, slug_from_url


class SlugTests(TestCase):
    def test_a_url_id_round_trips_to_the_stored_key(self):
        self.assertEqual(slug_from_url("mohini-kak"), "mohini kak")

    def test_the_id_is_case_insensitive(self):
        self.assertEqual(slug_from_url("Mohini-Kak"), "mohini kak")


class ProfilePayloadTests(TestCase):
    def setUp(self):
        self.lead = TeamLeadProfile.objects.create(
            slug="mohini kak", name="Mohini Kak",
            title="Senior Health Specialist",
            unit="Health, Nutrition and Population",
            work_email="mkak@worldbank.org",
            email_source=TeamLeadProfile.EmailSource.PATTERN,
            checked_at=timezone.now(),
        )
        self.project = ProjectProfile.objects.create(
            project_id="P167598", name="Health System Improvement",
            country="Uzbekistan", team_lead="Mohini Kak,Sven Neelsen",
        )
        # A different person whose surname shares no token.
        ProjectProfile.objects.create(
            project_id="P999999", name="Other project",
            country="Kazakhstan", team_lead="Anna Sukhova",
        )
        TenderNotice.objects.create(
            notice_id="OP1", project_id="P167598", country="Uzbekistan",
            bid_description="Health equipment supply",
            notice_type="Invitation for Bids",
            deadline_date=timezone.now() + timezone.timedelta(days=10),
        )
        TenderNotice.objects.create(
            notice_id="OP2", project_id="P999999", country="Kazakhstan",
            bid_description="Unrelated", notice_type="Invitation for Bids",
        )

    def test_only_projects_naming_this_person_are_listed(self):
        payload = profile_payload(self.lead)
        self.assertEqual([p["project_id"] for p in payload["projects"]], ["P167598"])

    def test_tenders_come_from_those_projects_only(self):
        payload = profile_payload(self.lead)
        self.assertEqual([n["id"] for n in payload["notices"]], ["OP1"])
        self.assertEqual(payload["stats"], {"projects": 1, "notices": 1, "open_notices": 1})

    def test_a_near_miss_surname_is_not_the_same_person(self):
        # "Sukhova" vs "Sukhovaya" share a prefix; the icontains pre-filter
        # would let it through, so the fold has to settle it.
        ProjectProfile.objects.create(
            project_id="P888888", name="Near miss",
            country="Georgia", team_lead="Anna Sukhovaya",
        )
        sukhova = TeamLeadProfile.objects.create(slug="anna suxova", name="Anna Sukhova")
        payload = profile_payload(sukhova)
        self.assertEqual([p["project_id"] for p in payload["projects"]], ["P999999"])

    def test_spelling_drift_between_projects_still_matches(self):
        ProjectProfile.objects.create(
            project_id="P777777", name="Drift",
            country="Uzbekistan", team_lead="Mohini KAK",
        )
        payload = profile_payload(self.lead)
        self.assertIn("P777777", [p["project_id"] for p in payload["projects"]])

    def test_a_derived_address_is_not_reported_as_confirmed(self):
        self.assertFalse(profile_payload(self.lead)["email_confirmed"])

    def test_the_payload_carries_no_personal_presence_fields(self):
        # The page is employer-published professional information plus this
        # database's own facts. A field named for a personal account or a
        # private contact detail appearing here would mean the scope moved
        # without anyone deciding to move it.
        #
        # `photo_url` is deliberately absent from this list: it holds the
        # portrait the Bank publishes on its own author page to identify this
        # person professionally, which is not the same thing as an image taken
        # from a personal account.
        payload = profile_payload(self.lead)
        forbidden = (
            "facebook", "instagram", "whatsapp", "telegram", "twitter",
            "personal_email", "phone", "mobile", "home_address", "birth",
            "avatar",
        )
        keys = " ".join(payload).lower()
        for word in forbidden:
            self.assertNotIn(word, keys, f"{word!r} leaked into the payload")

    def test_the_official_portrait_is_served(self):
        self.lead.photo_url = "https://s7d1.scene7.com/is/image/wbcollab/picture-1"
        self.lead.bio = "Mohini Kak is a Senior Health Specialist…"
        self.lead.save()
        payload = profile_payload(self.lead)
        self.assertTrue(payload["photo_url"])
        self.assertTrue(payload["bio"])


class RosterTests(TestCase):
    def test_enriched_profiles_are_listed_before_bare_names(self):
        TeamLeadProfile.objects.create(slug="never looked", name="Never Looked")
        TeamLeadProfile.objects.create(
            slug="already done", name="Already Done", checked_at=timezone.now()
        )
        rows = roster()
        self.assertEqual(rows[0]["name"], "Already Done")
        self.assertTrue(rows[0]["enriched"])
        self.assertFalse(rows[1]["enriched"])


class EndpointTests(TestCase):
    def test_an_unknown_person_is_a_404_not_an_empty_page(self):
        self.assertEqual(self.client.get("/api/team-leads/nobody-here/").status_code, 404)

    def test_a_known_person_resolves_by_their_url_id(self):
        TeamLeadProfile.objects.create(slug="mohini kak", name="Mohini Kak")
        response = self.client.get("/api/team-leads/mohini-kak/")
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.json()["name"], "Mohini Kak")

    def test_a_name_nobody_looked_up_still_has_a_page(self):
        # The difference between "nothing found" and "nobody looked" is what
        # the page tells the reader, so it must not 404.
        TeamLeadProfile.objects.create(slug="bare name", name="Bare Name")
        payload = self.client.get("/api/team-leads/bare-name/").json()
        self.assertIsNone(payload["checked_at"])
        self.assertEqual(payload["title"], "")
