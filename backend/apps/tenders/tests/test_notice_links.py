"""Finding the Terms of Reference inside the notice body.

The wording used by borrowers is what identifies a link — the URL itself is an
opaque Google Drive id. These cases are lifted from the live corpus, including
the two that silently broke the first implementation: HTML-escaped query
strings, and a notice that lists several e-mail addresses of which only one
takes TOR requests.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.tenders.notice_links import (
    KIND_BIDDING,
    KIND_OTHER,
    KIND_TOR,
    extract_links,
    find_tor_email,
    mentions_tor,
)


class ClassificationTests(SimpleTestCase):
    def test_reads_the_sentence_before_the_link(self):
        links = extract_links(
            "The detailed Terms of Reference (TOR) for the assignment can be "
            "found at the following link: https://docs.google.com/document/d/1Na/edit"
        )
        self.assertEqual([link.kind for link in links], [KIND_TOR])
        self.assertEqual(links[0].url, "https://docs.google.com/document/d/1Na/edit")

    def test_recognises_the_abbreviation_in_any_casing(self):
        for wording in ["Terms of Reference", "ToR", "TOR", "TORs"]:
            with self.subTest(wording=wording):
                links = extract_links(f"Follow the link to read the {wording}: https://x.io/a")
                self.assertEqual(links[0].kind, KIND_TOR)

    def test_bidding_documents_are_a_separate_kind(self):
        links = extract_links(
            "The bidding document may be downloaded from https://borrower.gov.uz/bid"
        )
        self.assertEqual(links[0].kind, KIND_BIDDING)

    def test_an_undescribed_link_is_not_promoted(self):
        """A bare URL is the borrower's home page as often as anything else."""
        links = extract_links("Visit https://minfin.gov.uz for more information.")
        self.assertEqual(links[0].kind, KIND_OTHER)

    def test_tor_outranks_bidding_when_both_are_named(self):
        links = extract_links(
            "The bidding document and the Terms of Reference are at https://x.io/a"
        )
        self.assertEqual(links[0].kind, KIND_TOR)

    def test_context_does_not_reach_into_the_previous_paragraph(self):
        """180 characters back — far enough for the sentence, no further."""
        text = (
            "The detailed Terms of Reference are published separately. "
            + "Consultants must submit their expressions of interest in a sealed "
            "envelope, clearly marked with the assignment title and delivered to "
            "the address below before the stated deadline, in accordance with the "
            "procedures of the Borrower. "
            + "See https://borrower.gov.uz/news"
        )
        self.assertEqual(extract_links(text)[0].kind, KIND_OTHER)


class OrderingTests(SimpleTestCase):
    def test_tor_comes_first_regardless_of_position_in_the_text(self):
        links = extract_links(
            "Registration form: https://x.io/form . "
            "The Terms of Reference can be found at https://x.io/tor ."
        )
        self.assertEqual(
            [(link.kind, link.url) for link in links],
            [(KIND_TOR, "https://x.io/tor"), (KIND_BIDDING, "https://x.io/form")],
        )

    def test_a_repeated_url_keeps_its_most_specific_reading(self):
        """The same link often appears once described and once bare."""
        links = extract_links(
            "See https://x.io/tor below. "
            "The Terms of Reference for the assignment: https://x.io/tor"
        )
        self.assertEqual(len(links), 1)
        self.assertEqual(links[0].kind, KIND_TOR)


class UrlCleaningTests(SimpleTestCase):
    def test_escaped_ampersands_are_decoded(self):
        """Six of twenty-nine live notices carry these; `&amp;` breaks the link."""
        links = extract_links(
            "Terms of Reference: https://docs.google.com/document/d/13r/edit"
            "?usp=sharing&amp;ouid=104755491&amp;rtpof=true"
        )
        self.assertEqual(
            links[0].url,
            "https://docs.google.com/document/d/13r/edit"
            "?usp=sharing&ouid=104755491&rtpof=true",
        )

    def test_sentence_punctuation_is_not_part_of_the_url(self):
        links = extract_links("The ToR is at https://x.io/a/b.")
        self.assertEqual(links[0].url, "https://x.io/a/b")

    def test_a_balanced_bracket_inside_the_url_is_kept(self):
        links = extract_links("The ToR is at https://x.io/a_(final)")
        self.assertEqual(links[0].url, "https://x.io/a_(final)")

    def test_non_http_schemes_are_ignored(self):
        self.assertEqual(extract_links("Write to mailto:a@b.io or ftp://x.io/f"), [])

    def test_nbsp_between_the_colon_and_the_link_still_reads_as_tor(self):
        links = extract_links(
            "Terms of Reference (TOR) can be found at the following link:&nbsp;https://x.io/a"
        )
        self.assertEqual(links[0].kind, KIND_TOR)


class EmailFallbackTests(SimpleTestCase):
    def test_returns_only_the_address_introduced_by_tor_wording(self):
        """A notice lists several addresses; the wrong one goes nowhere."""
        text = (
            "Expressions of interest must be delivered to submissions@pmu.uz . "
            "The Terms of Reference may be requested by e-mail from tor@pmu.uz ."
        )
        self.assertEqual(find_tor_email(text), "tor@pmu.uz")

    def test_the_sentence_full_stop_is_not_part_of_the_address(self):
        """The domain match is greedy, so "…@it-park.uz." arrives with a dot."""
        self.assertEqual(
            find_tor_email("The ToR can be requested from udip@it-park.uz."),
            "udip@it-park.uz",
        )

    def test_an_unrelated_address_is_not_offered(self):
        self.assertEqual(find_tor_email("Contact the PIU at info@pmu.uz ."), "")

    def test_empty_text_is_handled(self):
        self.assertEqual(find_tor_email(""), "")
        self.assertEqual(extract_links(""), [])
        self.assertFalse(mentions_tor(""))


class MentionTests(SimpleTestCase):
    def test_detects_a_tor_named_without_a_link_or_an_address(self):
        """Five live notices do this — saying so beats an empty panel."""
        self.assertTrue(
            mentions_tor("Detailed Terms of Reference will be shared with shortlisted firms.")
        )

    def test_does_not_fire_on_a_word_that_merely_contains_tor(self):
        self.assertFalse(mentions_tor("Sector monitoring and contractor supervision."))
