"""Documents a vendor supplies, rather than ones the harvester found.

Nothing here reaches the network. The upload path never did; the URL path is
``harvest.harvest_document`` unchanged, so these tests hand it the same stub
session the harvester's own tests use — the point being to prove intake reuses
that path rather than to re-test the fetching it already covers.
"""

from __future__ import annotations

import copy
import tempfile
from unittest import mock

from django.test import TestCase, override_settings
from django.utils import timezone

from apps.tenders.models import HarvestedDocument, TenderNotice
from apps.tenders.services import harvest, intake

from .test_harvest import FakeResponse, FakeSession

TOR_TEXT = (
    b"Terms of Reference\n\n"
    b"The Consultant shall demonstrate an average annual turnover of at least "
    b"USD 2,000,000 (two million United States Dollars) over the last three "
    b"(3) years, and shall have completed at least two (2) similar assignments "
    b"within the last five (5) years. Proposals shall be accompanied by the "
    b"documentation listed in Section 4 of this document.\n"
)


def _harvest_settings(tmp):
    settings = copy.deepcopy(harvest.settings.HARVEST)
    settings["DIR"] = __import__("pathlib").Path(tmp)
    return settings


class IntakeTestCase(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.override = override_settings(HARVEST=_harvest_settings(self.tmp))
        self.override.enable()
        self.addCleanup(self.override.disable)

        self.notice = TenderNotice.objects.create(
            notice_id="OP-INTAKE-1",
            notice_type="Request for Expression of Interest",
            country="Uzbekistan",
            deadline_date=timezone.now() + timezone.timedelta(days=10),
            notice_text_sanitized="<p>Detailed terms of reference are available on request.</p>",
            contact_email="procurement@example.uz",
        )


class RefusingWhatCannotBeRead(IntakeTestCase):
    """A submission that could never yield text is refused before it is stored."""

    def test_an_empty_file_is_refused(self):
        with self.assertRaises(intake.IntakeRejected):
            intake.accept_upload(self.notice, payload=b"", filename="tor.pdf")

    def test_a_format_no_parser_reads_is_refused_rather_than_stored(self):
        """Storing it would report a document with no text — 'we lost your file'."""
        with self.assertRaises(intake.IntakeRejected) as caught:
            intake.accept_upload(self.notice, payload=b"MZ\x90", filename="tor.exe")
        self.assertIn("unsupported", str(caught.exception))

    def test_a_file_over_the_cap_is_refused(self):
        settings = _harvest_settings(self.tmp)
        settings["MAX_BYTES"] = 10
        with override_settings(HARVEST=settings), self.assertRaises(intake.IntakeRejected):
            intake.accept_upload(self.notice, payload=TOR_TEXT, filename="tor.txt")

    def test_nothing_refused_reaches_the_corpus(self):
        for payload, name in ((b"", "a.pdf"), (b"MZ", "a.exe")):
            with self.assertRaises(intake.IntakeRejected):
                intake.accept_upload(self.notice, payload=payload, filename=name)
        self.assertEqual(HarvestedDocument.objects.count(), 0)


class TakingWhatAVendorSupplies(IntakeTestCase):
    def test_an_uploaded_document_joins_the_corpus_attached_to_its_notice(self):
        result = intake.accept_upload(self.notice, payload=TOR_TEXT, filename="tor.txt")

        self.assertTrue(result.created)
        self.assertTrue(result.readable)
        self.assertEqual(result.problem, "")
        document = result.document
        self.assertEqual(document.origin, HarvestedDocument.Origin.CLIENT_SUPPLIED)
        self.assertEqual(document.kind, HarvestedDocument.Kind.TOR)
        self.assertIn("average annual turnover", document.text)
        self.assertEqual(list(document.notices.all()), [self.notice])

    def test_no_http_status_is_invented_for_a_file_nobody_fetched(self):
        """A synthetic 200 would make the corpus report claim a fetch that never happened."""
        result = intake.accept_upload(self.notice, payload=TOR_TEXT, filename="tor.txt")
        self.assertIsNone(result.document.http_status)

    def test_the_same_file_twice_is_one_document(self):
        """Identity is the bytes: two vendors with the same TOR share one row."""
        first = intake.accept_upload(self.notice, payload=TOR_TEXT, filename="tor.txt")
        second = intake.accept_upload(self.notice, payload=TOR_TEXT, filename="renamed.txt")

        self.assertTrue(first.created)
        self.assertFalse(second.created)
        self.assertEqual(first.document.pk, second.document.pk)
        self.assertEqual(HarvestedDocument.objects.count(), 1)

    def test_a_second_notice_can_share_a_document_a_vendor_already_supplied(self):
        other = TenderNotice.objects.create(
            notice_id="OP-INTAKE-2",
            notice_type="Request for Expression of Interest",
            country="Uzbekistan",
        )
        intake.accept_upload(self.notice, payload=TOR_TEXT, filename="tor.txt")
        result = intake.accept_upload(other, payload=TOR_TEXT, filename="tor.txt")

        self.assertEqual(
            set(result.document.notices.values_list("pk", flat=True)),
            {self.notice.pk, other.pk},
        )

    def test_a_scan_is_reported_to_the_vendor_and_the_bytes_are_kept(self):
        """'Your scan has no text layer' is something a vendor can act on."""
        result = intake.accept_upload(
            self.notice, payload=b"%PDF-1.4 no text here", filename="scan.pdf"
        )

        self.assertFalse(result.readable)
        self.assertTrue(result.problem)
        self.assertEqual(result.document.status, HarvestedDocument.Status.NO_TEXT)
        self.assertTrue(result.document.stored_path, "the file is kept for OCR")

    def test_who_supplied_it_is_recorded(self):
        result = intake.accept_upload(
            self.notice, payload=TOR_TEXT, filename="tor.txt", submitted_by="Acme LLC"
        )
        self.assertIn("Acme LLC", result.document.link_context)


class TakingALinkAVendorWasSent(IntakeTestCase):
    def test_a_link_is_fetched_through_the_harvester_not_a_second_fetcher(self):
        session = FakeSession(FakeResponse(TOR_TEXT, content_type="text/plain"))
        with mock.patch.object(harvest, "_assert_fetchable", return_value=None):
            result = intake.accept_url(
                self.notice, url="https://example.com/tor.txt", session=session
            )

        self.assertTrue(result.readable)
        self.assertEqual(result.document.origin, HarvestedDocument.Origin.CLIENT_SUPPLIED)
        self.assertEqual(session.requested, ["https://example.com/tor.txt"])

    def test_a_share_link_is_rewritten_by_the_harvester_rules(self):
        """A Drive viewer page serves HTML; the export URL serves the file."""
        session = FakeSession(FakeResponse(TOR_TEXT, content_type="text/plain"))
        with mock.patch.object(harvest, "_assert_fetchable", return_value=None):
            intake.accept_url(
                self.notice,
                url="https://drive.google.com/file/d/1AbCdEfGhIjK/view?usp=sharing",
                session=session,
            )

        self.assertIn("uc?export=download", session.requested[0])

    def test_an_address_inside_the_network_is_refused(self):
        """The URL is typed by a stranger — the SSRF guard is the whole defence."""
        session = FakeSession(FakeResponse(TOR_TEXT))
        result = intake.accept_url(
            self.notice, url="http://169.254.169.254/latest/meta-data/", session=session
        )

        self.assertFalse(result.readable)
        self.assertEqual(result.document.status, HarvestedDocument.Status.SKIPPED)
        self.assertEqual(session.requested, [], "nothing was requested")

    def test_an_empty_url_is_refused(self):
        with self.assertRaises(intake.IntakeRejected):
            intake.accept_url(self.notice, url="   ")


class KeepingSuppliedDocumentsOutOfTheHarvestQueue(IntakeTestCase):
    def test_an_uploaded_file_is_never_queued_for_fetching(self):
        """There is no URL behind it; queuing one would spend an attempt to learn that."""
        intake.accept_upload(self.notice, payload=TOR_TEXT, filename="tor.txt")

        self.assertEqual(harvest.select_pending(10), [])

    def test_a_harvested_document_is_still_queued(self):
        HarvestedDocument.objects.create(
            url_hash="c" * 64, url="https://example.com/other.pdf"
        )
        self.assertEqual(len(harvest.select_pending(10)), 1)


class TellingTheVendorWhereToLook(IntakeTestCase):
    def test_a_notice_with_nothing_readable_needs_a_document(self):
        self.assertTrue(intake.needs_a_document(self.notice))

    def test_a_notice_whose_document_we_already_hold_does_not(self):
        """L1 finding nothing is not a reason to send a vendor asking for a file we have."""
        intake.accept_upload(self.notice, payload=TOR_TEXT, filename="tor.txt")
        self.assertFalse(intake.needs_a_document(self.notice))

    def test_supplied_documents_are_distinguishable_from_harvested_ones(self):
        harvested = HarvestedDocument.objects.create(
            url_hash="d" * 64,
            url="https://example.com/linked.pdf",
            status=HarvestedDocument.Status.FETCHED,
            text="x" * 500,
            text_chars=500,
        )
        harvested.notices.add(self.notice)
        intake.accept_upload(self.notice, payload=TOR_TEXT, filename="tor.txt")

        self.assertEqual(self.notice.harvested_documents.usable().count(), 2)
        self.assertEqual(intake.supplied_for(self.notice).count(), 1)
