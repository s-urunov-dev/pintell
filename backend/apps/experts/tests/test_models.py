"""What the directory refuses to store, and what it stores despite the spelling."""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import IntegrityError, transaction
from django.db.models import ProtectedError
from django.test import TestCase

from apps.experts.linkedin import normalise_profile_url
from apps.experts.models import Expert, ExpertType


class LinkedInNormalisationTests(TestCase):
    """One profile, however it was copied, reduces to one string."""

    def test_the_ways_one_profile_is_pasted_all_agree(self):
        canonical = "https://www.linkedin.com/in/jane-doe"
        for raw in (
            "linkedin.com/in/jane-doe",
            "https://www.linkedin.com/in/jane-doe/",
            "http://linkedin.com/in/Jane-Doe",
            "https://uz.linkedin.com/in/jane-doe?originalSubdomain=uz",
            "  https://www.linkedin.com/in/jane-doe/?trk=public_profile  ",
            "https://www.linkedin.com/in/jane-doe/details/experience/",
        ):
            with self.subTest(raw=raw):
                self.assertEqual(normalise_profile_url(raw), canonical)

    def test_the_legacy_profile_form_is_kept(self):
        """/pub/ links still resolve, and are still on CVs."""
        self.assertEqual(
            normalise_profile_url("https://www.linkedin.com/pub/jane-doe"),
            "https://www.linkedin.com/pub/jane-doe",
        )

    def test_an_empty_link_is_allowed(self):
        self.assertEqual(normalise_profile_url(""), "")
        self.assertEqual(normalise_profile_url("   "), "")

    def test_links_that_are_not_a_person_are_refused(self):
        for raw in (
            "https://example.com/in/jane-doe",
            "https://www.linkedin.com/company/acme",
            "https://www.linkedin.com/",
            "https://linkedin.com.evil.example/in/jane-doe",
        ):
            with self.subTest(raw=raw):
                with self.assertRaises(ValidationError):
                    normalise_profile_url(raw)


class ExpertTests(TestCase):
    def setUp(self):
        self.family = ExpertType.objects.create(
            slug="environmental-and-social", name="Environmental and social"
        )
        self.role = ExpertType.objects.create(
            slug="gender-specialist", name="Gender Specialist", parent=self.family
        )

    def test_the_link_is_canonicalised_on_save(self):
        expert = Expert.objects.create(
            full_name="  Jane Doe  ", linkedin_url="linkedin.com/in/Jane-Doe/"
        )

        expert.refresh_from_db()
        self.assertEqual(expert.full_name, "Jane Doe")
        self.assertEqual(expert.linkedin_url, "https://www.linkedin.com/in/jane-doe")

    def test_the_same_profile_cannot_be_listed_twice(self):
        """The duplicate the directory exists to prevent."""
        Expert.objects.create(
            full_name="Jane Doe", linkedin_url="https://www.linkedin.com/in/jane-doe"
        )

        with self.assertRaises(IntegrityError), transaction.atomic():
            Expert.objects.create(
                full_name="J. Doe", linkedin_url="uz.linkedin.com/in/jane-doe/"
            )

    def test_several_experts_may_have_no_link_at_all(self):
        """Blank is not a value, so it cannot collide with another blank."""
        Expert.objects.create(full_name="Jane Doe")
        Expert.objects.create(full_name="John Roe")

        self.assertEqual(Expert.objects.filter(linkedin_url="").count(), 2)

    def test_an_expert_holds_several_roles(self):
        expert = Expert.objects.create(full_name="Jane Doe")
        auditor = ExpertType.objects.create(
            slug="auditor", name="Auditor", parent=self.family
        )
        expert.types.set([self.role, auditor])

        self.assertEqual(expert.types.count(), 2)
        self.assertEqual(self.role.experts.get(), expert)


class ExpertTypeDepthTests(TestCase):
    """The two-level rule, checked on both paths that can create a row."""

    def setUp(self):
        self.family = ExpertType.objects.create(slug="legal", name="Legal")
        self.role = ExpertType.objects.create(
            slug="ppp-lawyer", name="PPP Lawyer", parent=self.family
        )

    def test_a_role_cannot_be_given_a_role_as_its_family(self):
        deeper = ExpertType(slug="ppp-junior", name="Junior", parent=self.role)

        with self.assertRaises(ValidationError):
            deeper.save()

    def test_the_admin_form_path_reports_the_same_refusal(self):
        deeper = ExpertType(slug="ppp-junior", name="Junior", parent=self.role)

        with self.assertRaises(ValidationError):
            deeper.full_clean()

    def test_a_type_cannot_be_its_own_family(self):
        loop = ExpertType(slug="loop", name="Loop")
        loop.parent_id = "loop"

        with self.assertRaises(ValidationError):
            loop.save()

    def test_a_family_with_roles_cannot_be_deleted_by_accident(self):
        with self.assertRaises(ProtectedError):
            self.family.delete()
