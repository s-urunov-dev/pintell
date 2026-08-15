"""Phase two: reading the document a vendor obtained after asking the contact.

Phase one reads what we could reach on our own. For most notices that ends with
nothing, because the criteria are in a Terms of Reference the notice only names
(D12). Phase two reads what the vendor was sent when they wrote to the contact
the notice publishes (D17).

No network and no model. L3 is replaced by a scripted stand-in so these tests
are about the *phase* — which documents are read, what is skipped, what is
recorded — rather than about extraction, which ``test_l3`` covers.
"""

from __future__ import annotations

import copy
import tempfile
from contextlib import contextmanager
from types import ModuleType

from django.test import TestCase, override_settings
from django.urls import reverse
from django.utils import timezone
from rest_framework import status
from rest_framework.test import APITestCase

from apps.compliance import pipeline
from apps.compliance.extraction import Extracted, LayerResult
from apps.compliance.models import ExtractionRun, TenderRequirement
from apps.tenders.models import HarvestedDocument, TenderNotice
from apps.tenders.services import harvest, intake

from .test_pipeline import _layer

TOR_TEXT = (
    "Terms of Reference\n\n"
    "The Consultant shall demonstrate an average annual turnover of at least "
    "USD 2,000,000 (two million United States Dollars) over the last three (3) "
    "years, and shall have completed at least two (2) similar assignments "
    "within the last five (5) years.\n"
)


def _requirement(key: str, quote: str, document_id: str | None = None) -> Extracted:
    return Extracted(
        key=key,
        label=key.replace("_", " ").title(),
        expression={"kind": "scalar", "key": key, "op": ">=", "value": 2000000},
        evidence_quote=quote,
        source="tor:chunk 1",
        source_document_id=document_id,
    )


@contextmanager
def scripted_l3(result_for):
    """Stand in for L3, recording which documents it was handed.

    Installed through ``test_pipeline._layer`` rather than by patching
    ``sys.modules`` directly: once a submodule has been imported it is bound on
    the package, and ``from . import l3`` finds it there without consulting
    ``sys.modules`` at all — which silently leaves the real layer in place.
    """
    seen: list[tuple[str, frozenset[str]]] = []

    stub = ModuleType("apps.compliance.l3")

    def extract(
        document,
        *,
        reference_year=None,
        exclude_keys=(),
        config=None,
        client=None,
        role_slugs=None,
    ):
        seen.append((document.pk, frozenset(exclude_keys)))
        return result_for(document)

    stub.extract = extract
    with _layer("l3", stub):
        yield seen


def _harvest_settings(tmp):
    settings = copy.deepcopy(harvest.settings.HARVEST)
    settings["DIR"] = __import__("pathlib").Path(tmp)
    return settings



def sign_in_vendor(client, email: str = "vendor@example.uz"):
    """A vendor account with a profile, signed in on ``client``.

    Both endpoints exercised below are vendor actions rather than public reads,
    so the session is part of the fixture rather than part of what is tested;
    the auth rules themselves are covered in ``test_auth``.
    """
    from django.contrib.auth import get_user_model

    from apps.compliance.models import VendorProfile

    user = get_user_model().objects.create_user(username=email, password="vendor-pass-123")
    VendorProfile.objects.create(user=user, name="Acme")
    client.force_login(user)
    return user


class SuppliedTestCase(TestCase):
    def setUp(self):
        self.tmp = tempfile.mkdtemp()
        self.override = override_settings(HARVEST=_harvest_settings(self.tmp))
        self.override.enable()
        self.addCleanup(self.override.disable)

        self.notice = TenderNotice.objects.create(
            notice_id="OP-SUPPLIED-1",
            notice_type="Request for Expression of Interest",
            country="Uzbekistan",
            notice_date=timezone.now().date(),
            deadline_date=timezone.now() + timezone.timedelta(days=10),
            notice_text_sanitized=(
                "<p>The Terms of Reference may be obtained from the address below.</p>"
            ),
            contact_email="procurement@example.uz",
            contact_name="Procurement Unit",
        )

    def upload(self, text: str = TOR_TEXT, name: str = "tor.txt"):
        return intake.accept_upload(
            self.notice, payload=text.encode("utf-8"), filename=name
        )

    def harvested(self, text: str = TOR_TEXT) -> HarvestedDocument:
        document = HarvestedDocument.objects.create(
            url_hash="h" * 64,
            url="https://example.com/linked.pdf",
            kind=HarvestedDocument.Kind.TOR,
            status=HarvestedDocument.Status.FETCHED,
            text=text,
            text_chars=len(text),
        )
        document.notices.add(self.notice)
        return document


class WhenNothingHasBeenSupplied(SuppliedTestCase):
    def test_no_run_is_recorded_because_nothing_was_read(self):
        """An empty run row would put a pass with no source into the ablation."""
        self.assertIsNone(pipeline.extract_from_supplied(self.notice))
        self.assertEqual(ExtractionRun.objects.count(), 0)

    def test_a_harvested_document_alone_is_not_phase_two(self):
        """Phase one already read it; re-reading it here would bill it twice."""
        self.harvested()
        self.assertIsNone(pipeline.extract_from_supplied(self.notice))


class ReadingWhatTheVendorSupplied(SuppliedTestCase):
    def test_the_supplied_document_is_read_and_its_criteria_stored(self):
        supplied = self.upload().document

        with scripted_l3(
            lambda doc: LayerResult(
                requirements=[
                    _requirement("annual_turnover_avg", "average annual turnover of at least", doc.pk)
                ],
                model="claude-haiku-4-5",
            )
        ) as seen:
            run = pipeline.extract_from_supplied(self.notice)

        self.assertEqual([pk for pk, _ in seen], [supplied.pk])
        self.assertEqual(run.layers, "L3")
        self.assertEqual(run.status, ExtractionRun.Status.OK)
        row = TenderRequirement.objects.get()
        self.assertEqual(row.layer, TenderRequirement.Layer.L3)
        self.assertEqual(row.source_document_id, supplied.pk)
        self.assertEqual(row.grounding, TenderRequirement.Grounding.VERIFIED)

    def test_only_supplied_documents_are_read(self):
        """A harvested document belongs to phase one, whatever else is attached."""
        self.harvested()
        supplied = self.upload().document

        with scripted_l3(lambda doc: LayerResult()) as seen:
            pipeline.extract_from_supplied(self.notice)

        self.assertEqual([pk for pk, _ in seen], [supplied.pk])

    def test_a_quote_absent_from_the_supplied_document_is_withheld(self):
        supplied = self.upload().document

        with scripted_l3(
            lambda doc: LayerResult(
                requirements=[_requirement("liquid_assets", "a sentence nobody wrote", doc.pk)]
            )
        ):
            pipeline.extract_from_supplied(self.notice)

        row = TenderRequirement.objects.get()
        self.assertEqual(row.grounding, TenderRequirement.Grounding.NOT_FOUND)
        self.assertFalse(row.is_usable)


class NotPayingTwiceForWhatIsKnown(SuppliedTestCase):
    def test_keys_phase_one_established_are_not_asked_for_again(self):
        run = ExtractionRun.objects.create(notice=self.notice, layers="L1")
        TenderRequirement.objects.create(
            notice=self.notice,
            run=run,
            layer=TenderRequirement.Layer.L1,
            key="annual_turnover_avg",
            expression={"kind": "scalar", "key": "annual_turnover_avg", "op": ">=", "value": 1},
            evidence_quote="turnover",
            grounding=TenderRequirement.Grounding.VERIFIED,
        )
        self.upload()

        with scripted_l3(lambda doc: LayerResult()) as seen:
            pipeline.extract_from_supplied(self.notice)

        _, excluded = seen[0]
        self.assertIn("annual_turnover_avg", excluded)

    def test_a_key_whose_only_row_failed_grounding_is_still_missing(self):
        """Skipping it would leave an unverifiable claim and no second attempt."""
        run = ExtractionRun.objects.create(notice=self.notice, layers="L2")
        TenderRequirement.objects.create(
            notice=self.notice,
            run=run,
            layer=TenderRequirement.Layer.L2,
            key="liquid_assets",
            expression={"kind": "scalar", "key": "liquid_assets", "op": ">=", "value": 1},
            evidence_quote="invented",
            grounding=TenderRequirement.Grounding.NOT_FOUND,
        )
        self.upload()

        with scripted_l3(lambda doc: LayerResult()) as seen:
            pipeline.extract_from_supplied(self.notice)

        _, excluded = seen[0]
        self.assertNotIn("liquid_assets", excluded)


class RunningEveryTimeAVendorSupplies(SuppliedTestCase):
    def test_a_second_upload_is_read_even_though_an_L3_run_exists(self):
        """Skipping it would mean an upload silently did nothing."""
        self.upload()
        with scripted_l3(lambda doc: LayerResult()):
            first = pipeline.extract_from_supplied(self.notice)

        self.upload(TOR_TEXT + "The Consultant shall hold ISO 9001 certification.", "second.txt")
        with scripted_l3(lambda doc: LayerResult()) as seen:
            second = pipeline.extract_from_supplied(self.notice)

        self.assertIsNotNone(second)
        self.assertNotEqual(first.pk, second.pk, "runs are never overwritten")
        self.assertEqual(len(seen), 2, "both supplied documents are read")


class TheSubmissionEndpoint(APITestCase):
    def setUp(self):
        # Handing over a document is a vendor action, so it needs an account
        # (D17): the session is what bounds who can fill the harvest volume.
        self.vendor = sign_in_vendor(self.client)
        self.tmp = tempfile.mkdtemp()
        self.override = override_settings(HARVEST=_harvest_settings(self.tmp))
        self.override.enable()
        self.addCleanup(self.override.disable)

        self.notice = TenderNotice.objects.create(
            notice_id="OP-SUPPLIED-API",
            notice_type="Request for Expression of Interest",
            country="Uzbekistan",
            contact_email="procurement@example.uz",
        )
        self.url = reverse(
            "compliance:notice-documents", kwargs={"notice_id": self.notice.pk}
        )

    def test_a_submission_with_neither_a_file_nor_a_link_is_rejected(self):
        response = self.client.post(self.url, {}, format="json")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_unknown_document_kind_is_rejected(self):
        response = self.client.post(
            self.url, {"url": "https://example.com/a.pdf", "kind": "invoice"}, format="json"
        )
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)

    def test_an_uploaded_document_is_accepted_and_read(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload = SimpleUploadedFile("tor.txt", TOR_TEXT.encode("utf-8"), "text/plain")
        with scripted_l3(
            lambda doc: LayerResult(
                requirements=[
                    _requirement(
                        "annual_turnover_avg",
                        "average annual turnover of at least",
                        doc.pk,
                    )
                ]
            )
        ):
            response = self.client.post(self.url, {"file": upload}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertTrue(body["document"]["readable"])
        self.assertEqual(body["extraction"]["requirements_found"], 1)
        self.assertEqual(len(body["requirements"]), 1)

    def test_an_unreadable_scan_is_reported_rather_than_failed(self):
        """The vendor did nothing wrong; 'no text layer' is something they can act on."""
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload = SimpleUploadedFile("scan.pdf", b"%PDF-1.4 nothing", "application/pdf")
        response = self.client.post(self.url, {"file": upload}, format="multipart")

        self.assertEqual(response.status_code, status.HTTP_201_CREATED)
        body = response.json()
        self.assertFalse(body["document"]["readable"])
        self.assertTrue(body["document"]["problem"])
        self.assertIsNone(body["extraction"])

    def test_a_rejected_format_is_a_client_error(self):
        from django.core.files.uploadedfile import SimpleUploadedFile

        upload = SimpleUploadedFile("payload.exe", b"MZ\x90", "application/octet-stream")
        response = self.client.post(self.url, {"file": upload}, format="multipart")
        self.assertEqual(response.status_code, status.HTTP_400_BAD_REQUEST)


class TellingTheReaderWhoToAsk(APITestCase):
    def setUp(self):
        self.vendor = sign_in_vendor(self.client)
        self.tmp = tempfile.mkdtemp()
        self.override = override_settings(HARVEST=_harvest_settings(self.tmp))
        self.override.enable()
        self.addCleanup(self.override.disable)

        self.notice = TenderNotice.objects.create(
            notice_id="OP-CONTACT-1",
            notice_type="Request for Expression of Interest",
            country="Uzbekistan",
            contact_email="procurement@example.uz",
            contact_name="Procurement Unit",
        )
        self.url = reverse(
            "compliance:notice-requirements", kwargs={"notice_id": self.notice.pk}
        )

    def test_a_notice_with_no_document_publishes_who_to_write_to(self):
        """An empty list alone reads as 'this tender asks for nothing' — the opposite."""
        body = self.client.get(self.url).json()

        self.assertFalse(body["documents"]["can_extract"])
        self.assertEqual(body["documents"]["contact"]["email"], "procurement@example.uz")

    def test_a_notice_whose_document_we_hold_does_not_republish_the_contact(self):
        """Sending vendors to ask for a file we have spreads an address for nothing."""
        intake.accept_upload(self.notice, payload=TOR_TEXT.encode("utf-8"), filename="tor.txt")

        body = self.client.get(self.url).json()

        self.assertTrue(body["documents"]["can_extract"])
        self.assertEqual(body["documents"]["supplied_by_vendors"], 1)

    def test_the_verdict_says_who_to_ask_too_not_only_the_criteria_list(self):
        """`unrated` is where a vendor is stuck, so it is where the way out belongs."""
        body = self.client.post(
            reverse("compliance:notice-assessment", kwargs={"notice_id": self.notice.pk})
        ).json()

        self.assertEqual(body["status"], "unrated")
        self.assertFalse(body["documents"]["can_extract"])
        self.assertEqual(body["documents"]["contact"]["email"], "procurement@example.uz")
