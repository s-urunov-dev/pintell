"""The directory as a client sees it: filtered, sorted, and saying no more."""

from __future__ import annotations

from django.urls import reverse
from rest_framework.test import APITestCase

from apps.experts.models import Expert, ExpertType


class DirectoryApiTests(APITestCase):
    fixtures = ["expert_types"]

    def setUp(self):
        self.leader = Expert.objects.create(
            full_name="Aziza Karimova",
            linkedin_url="https://www.linkedin.com/in/aziza-karimova",
        )
        self.leader.types.set(ExpertType.objects.filter(slug="team-leader"))

        self.gender = Expert.objects.create(full_name="Bekzod Rahimov")
        self.gender.types.set(ExpertType.objects.filter(slug="gender-specialist"))

        self.both = Expert.objects.create(full_name="Chorshanbe Yusupov")
        self.both.types.set(
            ExpertType.objects.filter(slug__in=["team-leader", "gender-specialist"])
        )

    def test_the_list_is_public(self):
        """No session: a vendor reads the directory before deciding to sign up."""
        response = self.client.get(reverse("experts:expert-list"))

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.data["count"], 3)

    def test_filtering_by_role_returns_everyone_holding_it_once(self):
        response = self.client.get(reverse("experts:expert-list"), {"role": "team-leader"})

        names = [row["full_name"] for row in response.data["results"]]
        self.assertEqual(names, ["Aziza Karimova", "Chorshanbe Yusupov"])

    def test_two_roles_are_a_union_and_a_person_appears_once(self):
        """One seat, several acceptable roles — and no duplicated person."""
        response = self.client.get(
            reverse("experts:expert-list"),
            {"role": ["team-leader", "gender-specialist"]},
        )

        names = [row["full_name"] for row in response.data["results"]]
        self.assertEqual(len(names), len(set(names)))
        self.assertEqual(response.data["count"], 3)

    def test_filtering_by_family_reaches_every_role_under_it(self):
        response = self.client.get(
            reverse("experts:expert-list"), {"family": "environmental-and-social"}
        )

        names = {row["full_name"] for row in response.data["results"]}
        self.assertEqual(names, {"Bekzod Rahimov", "Chorshanbe Yusupov"})

    def test_the_client_chooses_the_order(self):
        response = self.client.get(
            reverse("experts:expert-list"), {"ordering": "-full_name"}
        )

        names = [row["full_name"] for row in response.data["results"]]
        self.assertEqual(names[0], "Chorshanbe Yusupov")

    def test_searching_by_name(self):
        response = self.client.get(reverse("experts:expert-list"), {"search": "Bekzod"})

        self.assertEqual(response.data["count"], 1)

    def test_a_row_carries_the_roles_it_is_rendered_with(self):
        response = self.client.get(reverse("experts:expert-list"), {"search": "Aziza"})

        row = response.data["results"][0]
        self.assertEqual(row["linkedin_url"], "https://www.linkedin.com/in/aziza-karimova")
        self.assertEqual(row["roles"][0]["slug"], "team-leader")
        self.assertEqual(row["roles"][0]["family"], "project-management")

    def test_signal_terms_never_leave_the_server(self):
        """Search vocabulary is not a statement about any tender (Q15)."""
        listing = self.client.get(reverse("experts:expert-type-list"))
        experts = self.client.get(reverse("experts:expert-list"))

        for payload in (str(listing.data), str(experts.data)):
            self.assertNotIn("signal_terms", payload)
            self.assertNotIn("entitlement matrix", payload)

    def test_the_taxonomy_comes_back_as_families_holding_roles(self):
        response = self.client.get(reverse("experts:expert-type-list"))

        self.assertEqual(len(response.data), 5)
        first = response.data[0]
        self.assertEqual(first["slug"], "project-management")
        self.assertEqual(len(first["roles"]), 6)

    def test_each_role_reports_how_thin_the_directory_is_there(self):
        response = self.client.get(reverse("experts:expert-type-list"))

        counts = {
            role["slug"]: role["expert_count"]
            for family in response.data
            for role in family["roles"]
        }
        self.assertEqual(counts["team-leader"], 2)
        self.assertEqual(counts["auditor"], 0)
