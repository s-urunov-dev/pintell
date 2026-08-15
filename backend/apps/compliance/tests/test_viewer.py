"""Pointing at the sentence a criterion was read from.

The card already carries the quote, which is the claim's warrant; what it
cannot do is let a vendor check it against the paragraph around it. These cases
cover the machinery that can: the line index over a PDF, the locator that finds
a stored quote in it, and the choice of which text to open beside the criteria
in the first place.

The rule under all of them is that a highlight is never a new claim. It is the
already-verified quote, located by exact match — so a quote that cannot be found
produces no highlight rather than a box on the nearest similar line.
"""

from __future__ import annotations

import shutil
import tempfile
from pathlib import Path

from django.contrib.auth import get_user_model
from django.test import SimpleTestCase, TestCase

from apps.compliance import spans, viewer
from apps.compliance.models import ExtractionRun, TenderRequirement
from apps.tenders.models import HarvestedDocument, TenderNotice

TURNOVER = (
    "The Bidder shall have an average annual turnover of USD 22,400,000 "
    "over the last three years."
)


def make_span(span_id: str, text: str, page: int = 1, order: int = 0) -> spans.Span:
    return spans.Span(
        span_id=span_id,
        page=page,
        order=order,
        text=text,
        x0=72.0,
        top=100.0 + order * 14,
        x1=520.0,
        bottom=112.0 + order * 14,
        page_width=612.0,
        page_height=792.0,
    )


class LocatorTests(SimpleTestCase):
    """Finding a stored quote among indexed lines."""

    def setUp(self):
        self.spans = [
            make_span("p1_l0", "SECTION III - EVALUATION AND QUALIFICATION", order=0),
            make_span("p1_l1", "The Bidder shall have an average annual", order=1),
            make_span("p1_l2", "turnover of USD 22,400,000 over the last three years.", order=2),
            make_span("p1_l3", "Bids must be accompanied by a bid security.", order=3),
        ]

    def test_a_quote_spanning_two_lines_returns_both(self):
        """A requirement's evidence is a sentence and a PDF line is not one, so
        the straddling case is the ordinary case rather than the edge."""
        found = spans.Locator.over_spans(self.spans).locate(TURNOVER)

        self.assertEqual(found, ["p1_l1", "p1_l2"])

    def test_a_quote_inside_one_line_returns_only_that_line(self):
        found = spans.Locator.over_spans(self.spans).locate("bid security")

        self.assertEqual(found, ["p1_l3"])

    def test_a_quote_that_is_not_there_locates_nothing(self):
        """No fuzzy fallback. A near-match would put a box on a line that does
        not say what the card says, and the vendor would read it as proof."""
        found = spans.Locator.over_spans(self.spans).locate("ISO 9001 certification is required.")

        self.assertEqual(found, [])

    def test_case_and_typography_do_not_prevent_a_match(self):
        """The same document prints a heading in capitals and the sentence in
        sentence case, and mixes ' with ’ freely."""
        found = spans.Locator.over_spans(self.spans).locate("section iii - evaluation")

        self.assertEqual(found, ["p1_l0"])

    def test_an_empty_quote_locates_nothing(self):
        self.assertEqual(spans.Locator.over_spans(self.spans).locate(""), [])

    def test_offsets_survive_lines_that_canonicalise_to_nothing(self):
        """A line of whitespace collapses away, and every offset after it would
        shift if the map were built from the raw text."""
        padded = [
            make_span("p1_l0", "   ", order=0),
            make_span("p1_l1", "Bids must be accompanied by a bid security.", order=1),
        ]

        self.assertEqual(spans.Locator.over_spans(padded).locate("bid security"), ["p1_l1"])

    def test_locate_all_omits_what_it_could_not_find(self):
        """Absence is the state the client renders as "no highlight"; two
        spellings of it is how a UI starts treating one as an error."""
        found = spans.locate_all(
            [(1, TURNOVER), (2, "A requirement nobody wrote down.")], self.spans
        )

        self.assertIn(1, found)
        self.assertNotIn(2, found)


class _SourceCase(TestCase):
    """Fixtures shared by the two suites below.

    Document texts are padded past ``HarvestedDocument.MIN_USEFUL_CHARS``
    on purpose: below it a document is not readable and is not a candidate
    source at all, so a short fixture would test the wrong thing."""

    def setUp(self):
        self.notice = TenderNotice.objects.create(
            notice_id="OP-SRC",
            country="Uzbekistan",
            notice_text_sanitized=f"<p>Invitation for bids.</p><p>{TURNOVER}</p>",
        )
        self.run = ExtractionRun.objects.create(
            notice=self.notice, layers="L1", status=ExtractionRun.Status.OK
        )

    def _requirement(self, quote: str) -> TenderRequirement:
        return TenderRequirement.objects.create(
            notice=self.notice,
            run=self.run,
            layer=TenderRequirement.Layer.L1,
            key="annual_turnover_avg",
            expression={"kind": "scalar", "key": "annual_turnover_avg",
                        "op": ">=", "value": 22400000},
            evidence_quote=quote,
            grounding=TenderRequirement.Grounding.VERIFIED,
        )

    def _document(self, text: str, kind: str = HarvestedDocument.Kind.TOR):
        document = HarvestedDocument.objects.create(
            url_hash=f"hash-{kind}-{len(text)}",
            url=f"https://example.org/{kind}.pdf",
            kind=kind,
            status=HarvestedDocument.Status.FETCHED,
            text=text,
            text_chars=len(text),
        )
        document.notices.add(self.notice)
        return document


class ChooseSourceTests(_SourceCase):
    """Which text is opened beside the criteria."""

    def test_the_notice_body_is_opened_when_that_is_where_the_criteria_are(self):
        """The common case in this corpus: L1 reads the notice body and most
        notices link nothing readable at all."""
        rows = [self._requirement(TURNOVER)]

        source = viewer.choose_source(self.notice, rows)

        self.assertEqual(source.kind, viewer.NOTICE_BODY)

    def test_a_tie_goes_to_the_document_not_the_notice(self):
        """A Terms of Reference normally restates the notice's qualification
        list word for word, so the two tie at every criterion — and the old
        tie-break quietly opened the announcement beside a page whose own strip
        said a TOR was held. Measured on OP00460178: 11/11 both ways."""
        tor = self._document(f"Terms of Reference. {TURNOVER} " + "Filler. " * 30)
        rows = [self._requirement(TURNOVER)]

        source = viewer.choose_source(self.notice, rows)

        self.assertEqual(source.kind, viewer.DOCUMENT)
        self.assertEqual(source.document.pk, tor.pk)

    def test_the_notice_still_wins_when_it_evidences_strictly_more(self):
        """The case that matters: most notices link nothing that states a
        criterion, and a document winning on rank alone would open an empty
        one beside a full list."""
        self._document("Something else entirely. " * 20)
        rows = [self._requirement(TURNOVER)]

        source = viewer.choose_source(self.notice, rows)

        self.assertEqual(source.kind, viewer.NOTICE_BODY)

    def test_the_document_wins_when_it_is_what_evidences_the_criteria(self):
        """A rule that always preferred the notice would open a page that
        cannot show a single one of the sentences being claimed."""
        self._document("Something else entirely. " * 20)
        tor = self._document(
            f"Terms of Reference. {TURNOVER} Further conditions apply. " + "Filler. " * 30,
            kind=HarvestedDocument.Kind.BIDDING,
        )
        rows = [self._requirement(TURNOVER)]
        # Take the notice body out of the running so the only evidence is a file.
        self.notice.notice_text_sanitized = "<p>Invitation for bids.</p>"

        source = viewer.choose_source(self.notice, rows)

        self.assertEqual(source.kind, viewer.DOCUMENT)
        self.assertEqual(source.document.pk, tor.pk)

    def test_a_notice_with_nothing_readable_has_no_source(self):
        self.notice.notice_text_sanitized = ""

        self.assertIsNone(viewer.choose_source(self.notice, []))

    def test_a_reader_with_no_extracted_criteria_still_gets_the_tender(self):
        """Exactly the reader who wants to read it themselves."""
        source = viewer.choose_source(self.notice, [])

        self.assertIsNotNone(source)


class PayloadTests(_SourceCase):
    """What the split view is handed."""

    def test_a_text_source_carries_the_character_range_of_each_quote(self):
        """Computed here so no client has to reimplement `canonical` to find
        the quote in the text it was sent."""
        row = self._requirement(TURNOVER)

        payload = viewer.payload_for(self.notice, [row])

        block, start, end = payload["ranges"][str(row.pk)][0]
        self.assertIn("22,400,000", payload["blocks"][block]["text"][start:end])

    def test_the_source_keeps_the_paragraphs_the_notice_had(self):
        """The pane is read beside the tender page, and a wall of run-together
        text is a different document even when every character matches."""
        payload = viewer.payload_for(self.notice, [])

        self.assertEqual(
            [block["text"] for block in payload["blocks"]],
            ["Invitation for bids.", TURNOVER],
        )

    def test_a_quote_spanning_two_paragraphs_is_marked_in_both(self):
        """Not rare — it is the shape of every criterion whose heading and
        threshold sit in separate paragraphs, which is most of them. The reader
        has to see where it starts and where it ends.

        The quote carries the full stop the paragraph break becomes, because
        that is what `canonical` produced when the extractor copied it. Getting
        that one character wrong is what silently lost every multi-paragraph
        highlight in the first two attempts at this."""
        self.notice.notice_text_sanitized = (
            "<p>3) Financial Capacity.</p><p>Minimum average annual turnover of USD 200,000.</p>"
        )
        row = self._requirement(
            "3) Financial Capacity. Minimum average annual turnover of USD 200,000."
        )

        payload = viewer.payload_for(self.notice, [row])

        self.assertEqual([hit[0] for hit in payload["ranges"][str(row.pk)]], [0, 1])

    def test_a_paragraph_ending_in_a_space_does_not_shift_the_offsets(self):
        """The five characters that broke the second attempt: `canonical` keeps
        that space, so the boundary reads "text . " and not "text. "."""
        self.notice.notice_text_sanitized = (
            "<p>Uzbekistan Digital Inclusion Project </p><p>Credit No: 7425-UZ.</p>"
        )
        row = self._requirement("Credit No: 7425-UZ.")

        payload = viewer.payload_for(self.notice, [row])

        block, start, end = payload["ranges"][str(row.pk)][0]
        self.assertEqual(payload["blocks"][block]["text"][start:end], "Credit No: 7425-UZ.")

    def test_the_two_location_shapes_are_never_both_populated(self):
        row = self._requirement(TURNOVER)

        payload = viewer.payload_for(self.notice, [row])

        self.assertTrue(payload["ranges"])
        self.assertFalse(payload["highlights"])

    def test_a_quote_that_is_not_in_the_source_gets_no_range(self):
        """The requirement is still shown with its verified quote; only the
        pointer is missing."""
        row = self._requirement("A sentence from a different tender.")

        payload = viewer.payload_for(self.notice, [row])

        self.assertNotIn(str(row.pk), payload["ranges"])

    def test_a_document_with_no_page_geometry_says_so_rather_than_inventing_one(self):
        self.notice.notice_text_sanitized = ""
        self._document(f"Terms of Reference. {TURNOVER} " + "Filler. " * 30)
        row = self._requirement(TURNOVER)

        payload = viewer.payload_for(self.notice, [row])

        self.assertEqual(payload["problem"], viewer.UNSUPPORTED)
        self.assertEqual(payload["spans"], [])
        # And it still answers the question, in characters rather than points.
        self.assertIn(str(row.pk), payload["ranges"])

    def test_a_notice_with_no_source_returns_an_empty_payload_not_an_error(self):
        self.notice.notice_text_sanitized = ""

        payload = viewer.payload_for(self.notice, [])

        self.assertIsNone(payload["document"])
        self.assertEqual(payload["source"], "")


class DocumentFileTests(TestCase):
    """Who may read the bytes."""

    def setUp(self):
        self.notice = TenderNotice.objects.create(notice_id="OP-FILE", country="Uzbekistan")
        self.supplied = HarvestedDocument.objects.create(
            url_hash="supplied-hash",
            url="upload://tor.pdf",
            kind=HarvestedDocument.Kind.TOR,
            status=HarvestedDocument.Status.FETCHED,
            origin=HarvestedDocument.Origin.CLIENT_SUPPLIED,
            text="x" * 500,
            text_chars=500,
        )
        self.supplied.notices.add(self.notice)

    def _store(self, payload: bytes) -> None:
        """Put a blob where the document says its bytes are.

        A real temporary file rather than a patched reader, because the path
        resolution in ``viewer.stored_bytes`` — absolute path, or relative to
        the harvest volume — is part of what these cases are checking.
        """
        directory = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, directory, True)
        path = Path(directory) / "document.pdf"
        path.write_bytes(payload)
        self.supplied.stored_path = str(path)
        self.supplied.save(update_fields=["stored_path"])

    def test_a_document_a_vendor_handed_over_is_not_public(self):
        """It reached us because a vendor asked the borrower and passed on what
        they were sent (D17). It is published nowhere, and serving it to
        anonymous callers would turn a private hand-over into a public mirror.
        """
        response = self.client.get(f"/api/compliance/documents/{self.supplied.pk}/file/")

        self.assertEqual(response.status_code, 404)

    def test_a_signed_in_vendor_can_read_a_supplied_document(self):
        """The regression this guards. The project sets DRF's default
        authentication classes to an empty list, so a view that does not name
        one sees AnonymousUser even for a valid session — and the vendor was
        refused the very file they had uploaded a moment earlier."""
        self._store(b"%PDF-1.4 pretend")
        user = get_user_model().objects.create_user(
            username="v@example.com", email="v@example.com", password="pass-12345"
        )
        self.client.force_login(user)

        response = self.client.get(f"/api/compliance/documents/{self.supplied.pk}/file/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(b"".join(response.streaming_content), b"%PDF-1.4 pretend")

    def test_the_bytes_are_refused_to_an_anonymous_caller_even_when_present(self):
        """Paired with the case above so the two 404s cannot be confused: this
        one has a file on disk and is refused anyway."""
        self._store(b"%PDF-1.4 pretend")

        response = self.client.get(f"/api/compliance/documents/{self.supplied.pk}/file/")

        self.assertEqual(response.status_code, 404)

    def test_an_unknown_document_is_a_404_rather_than_a_500(self):
        response = self.client.get("/api/compliance/documents/nope/file/")

        self.assertEqual(response.status_code, 404)
