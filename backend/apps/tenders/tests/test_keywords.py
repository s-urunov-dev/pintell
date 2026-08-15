"""A keyword must occur as a word, not inside a longer one.

Every case here was taken from the deployed mirror, not invented: the counts
in the docstrings are how many of the 11,771 consulting notices the substring
test got wrong on that keyword alone.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.tenders.keywords import contains


class FalseHitsTests(SimpleTestCase):
    def test_information_is_not_the_french_for_training(self):
        """3,594 notices. `formation` scored Training on every *information*."""
        self.assertFalse(contains("formation", "provision of information systems"))
        self.assertTrue(contains("formation", "formation professionnelle des cadres"))

    def test_a_registry_is_not_a_geographic_information_system(self):
        """3,137 notices, mostly *registry*, *registration* and *legislation*."""
        self.assertFalse(contains("gis", "support to the land registry and registration"))
        self.assertTrue(contains("gis", "gis mapping of the district"))

    def test_an_enterprise_is_not_an_erp(self):
        """2,088 notices — *enterprise* and *interpersonal*."""
        self.assertFalse(contains("erp", "enterprise development and interpersonal skills"))
        self.assertTrue(contains("erp", "erp implementation for the treasury"))

    def test_an_auditorium_is_not_an_audit(self):
        """The one that reached a user: furniture for a moot courtroom was
        shown as a competitor contract to a firm reading an audit tender."""
        self.assertFalse(contains("audit", "auditorium and moot courtroom furniture"))

    def test_prevention_is_not_an_event(self):
        self.assertFalse(contains("event", "prevention of soil erosion"))


class InflectionsStillMatchTests(SimpleTestCase):
    """The leading boundary is what fixes the bug; the suffix list is what
    stops the fix costing as many true matches as it saves."""

    def test_a_plural_still_matches(self):
        self.assertTrue(contains("audit", "external audits of the project"))

    def test_an_agent_noun_still_matches(self):
        self.assertTrue(contains("audit", "the auditor shall report annually"))

    def test_a_longer_derived_form_still_matches(self):
        self.assertTrue(contains("architect", "architecture and urban design"))
        self.assertTrue(contains("digital", "digitalization of the registry"))

    def test_a_phrase_is_matched_as_a_substring(self):
        """Two words cannot hide inside one, so phrases keep the cheap test —
        and keep matching mid-sentence."""
        self.assertTrue(contains("supply of", "contract for the supply of medical equipment"))
