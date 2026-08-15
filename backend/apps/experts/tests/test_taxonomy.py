"""The shipped taxonomy loads, and keeps the shape the directory relies on."""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management import call_command
from django.test import TestCase

from apps.experts.models import ExpertType

FIXTURE = Path(__file__).resolve().parents[1] / "fixtures" / "expert_types.json"


class ExpertTypeFixtureTests(TestCase):
    """The fixture is the product's vocabulary; a broken one is a broken page."""

    def test_fixture_loads_the_whole_taxonomy(self):
        call_command("loaddata", "expert_types", verbosity=0)

        self.assertEqual(ExpertType.objects.families().count(), 5)
        self.assertEqual(ExpertType.objects.roles().count(), 36)

    def test_loading_twice_updates_rather_than_duplicates(self):
        """Reloading after an edit must be safe — that is why the slug is the key."""
        call_command("loaddata", "expert_types", verbosity=0)
        before = ExpertType.objects.count()

        call_command("loaddata", "expert_types", verbosity=0)

        self.assertEqual(ExpertType.objects.count(), before)

    def test_every_role_hangs_off_a_family(self):
        """Nothing sits at a third level: an expert's role is always a leaf."""
        call_command("loaddata", "expert_types", verbosity=0)

        for role in ExpertType.objects.roles().select_related("parent"):
            with self.subTest(role=role.slug):
                self.assertIsNone(role.parent.parent_id)

    def test_families_carry_no_signal_terms(self):
        """Terms belong to the role that is actually being looked for."""
        call_command("loaddata", "expert_types", verbosity=0)

        for family in ExpertType.objects.families():
            with self.subTest(family=family.slug):
                self.assertEqual(family.signal_terms, [])

    def test_positions_are_unique_within_each_family(self):
        """Two roles sharing a position would order arbitrarily between runs."""
        call_command("loaddata", "expert_types", verbosity=0)

        for family in ExpertType.objects.families():
            positions = list(family.children.values_list("position", flat=True))
            with self.subTest(family=family.slug):
                self.assertEqual(len(positions), len(set(positions)))

    def test_fixture_file_declares_no_duplicate_slugs(self):
        """A repeated key would silently overwrite a role on load."""
        rows = json.loads(FIXTURE.read_text())
        slugs = [row["pk"] for row in rows]

        self.assertEqual(len(slugs), len(set(slugs)))
