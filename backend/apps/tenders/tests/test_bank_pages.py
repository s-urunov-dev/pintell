"""Reading the World Bank's own author page for a staff member.

This is the one third-party fetch in the enrichment, and it is first-party in
the sense that matters: the employer publishing about its employee, on its own
domain, under its own byline. It needs no model and no key, so it is also the
cheapest source in the system — one GET, a deterministic URL.

The URL shape is the fragile part and the reason for most of these tests: it
keys on the *first* name's initial, which is not the obvious guess.
"""

from __future__ import annotations

from unittest.mock import patch

import requests
from django.test import SimpleTestCase

from apps.tenders.services import bank_pages

PAGE = """
<html><head><title>Mohini Kak | Senior Health Specialist </title></head>
<body>
  <img src="/content/dam/sites/blogs/logos/logo.png">
  <h1>Mohini Kak</h1>
  <span>This page in: English Hindi English</span>
  Mohini Kak is a Senior Health Specialist and is currently responsible for
  World Bank health and nutrition operations across several countries, with a
  core focus on maternal and child nutrition in South Asia.
  <img src="https://s7d1.scene7.com/is/image/wbcollab/picture-12269-1493314388?qlt=90">
  More Posts By Mohini
  Legal Privacy Notice Access to Information Jobs Contact
  © 2026 The World Bank Group, All Rights Reserved.
</body></html>
"""


class FakeResponse:
    def __init__(self, status_code=200, text=""):
        self.status_code = status_code
        self.text = text


class UrlTests(SimpleTestCase):
    def test_the_path_uses_the_first_names_initial(self):
        # Koji Nishida lives under /k/, not /n/ — checked against the live
        # site, where /n/koji-nishida is a 404.
        self.assertEqual(
            bank_pages.author_url("Koji Nishida"),
            "https://blogs.worldbank.org/en/team/k/koji-nishida",
        )

    def test_middle_names_are_dropped_from_the_path(self):
        self.assertEqual(
            bank_pages.author_url("Solene Marie Paule Rougeaux"),
            "https://blogs.worldbank.org/en/team/s/solene-rougeaux",
        )

    def test_a_single_name_has_no_author_url(self):
        self.assertEqual(bank_pages.author_url("Madonna"), "")
        self.assertEqual(bank_pages.author_url(""), "")


class FetchTests(SimpleTestCase):
    def _fetch(self, response):
        with patch.object(bank_pages, "_session") as session:
            session.return_value.get.return_value = response
            return bank_pages.fetch_author_page("Mohini Kak")

    def test_a_published_page_yields_title_bio_and_portrait(self):
        page = self._fetch(FakeResponse(text=PAGE))
        self.assertTrue(page.found)
        self.assertEqual(page.title, "Senior Health Specialist")
        self.assertIn("maternal and child nutrition", page.bio)
        self.assertTrue(page.photo_url.startswith("https://s7d1.scene7.com/"))

    def test_the_footer_does_not_become_part_of_the_biography(self):
        # Observed live: without this the bio ended "…University of Mumbai,
        # India. More Posts By Mohini Legal Privacy Notice Access to
        # Information Jobs Contact © 2026 The World Bank Group…".
        page = self._fetch(FakeResponse(text=PAGE))
        for leak in ("More Posts By", "Privacy Notice", "All Rights Reserved"):
            self.assertNotIn(leak, page.bio)
        self.assertTrue(page.bio.endswith("South Asia."))

    def test_the_portrait_is_not_confused_with_page_furniture(self):
        # The template carries several logos; only the CDN image is a person.
        page = self._fetch(FakeResponse(text=PAGE))
        self.assertNotIn("logo", page.photo_url)

    def test_staff_who_do_not_blog_are_a_normal_outcome(self):
        page = self._fetch(FakeResponse(status_code=404, text="not found"))
        self.assertFalse(page.found)
        self.assertEqual(page.bio, "")
        # The URL is still reported, so a caller can record what was tried.
        self.assertTrue(page.url)

    def test_a_transport_failure_never_raises(self):
        with patch.object(bank_pages, "_session") as session:
            session.return_value.get.side_effect = requests.ConnectionError("down")
            page = bank_pages.fetch_author_page("Mohini Kak")
        self.assertFalse(page.found)

    def test_a_page_without_a_biography_reports_none(self):
        page = self._fetch(FakeResponse(text="<html><title>X | Y</title><body>hi</body></html>"))
        self.assertEqual(page.bio, "")
