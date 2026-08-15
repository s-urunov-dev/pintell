"""The spreadsheet round trip through the admin: out, edited, back in.

The properties under test are the ones that make it safe to run twice, and the
one that makes it safe to expose at all — it is staff-only.
"""

from __future__ import annotations

from datetime import date
from decimal import Decimal

from django.contrib.auth import get_user_model
from django.test import TestCase, override_settings
from django.urls import reverse

from apps.tenders.categories import CategorySource
from apps.tenders.models import ContractAward, TenderNotice

# Rendering an admin page asks the staticfiles storage to hash `admin/css/
# base.css`, which only exists after `collectstatic` — a deployment artefact
# these tests have no business depending on. The plain storage skips the
# manifest; the real image runs collectstatic in its entrypoint.
PLAIN_STATIC = override_settings(STORAGES={
    "default": {"BACKEND": "django.core.files.storage.FileSystemStorage"},
    "staticfiles": {"BACKEND": "django.contrib.staticfiles.storage.StaticFilesStorage"},
})

CHANGELIST = reverse("admin:tenders_tendernotice_changelist")
IMPORT_URL = reverse("admin:tenders_tendernotice_import_categories")


def _notice(notice_id: str, **overrides) -> TenderNotice:
    fields = {
        "notice_type": "Contract Award",
        "country": "Uzbekistan",
        "category": "consulting",
        "subcategory": "other",
        "category_source": CategorySource.RULES,
        "bid_description": "Procurement of tractors",
        "procurement_method_code": "ICB",
    }
    notice = TenderNotice.objects.create(notice_id=notice_id, **{**fields, **overrides})
    ContractAward.objects.create(
        notice=notice, supplier_name="WINNER LLC", currency="UZS",
        contract_price=Decimal("100.00"), award_date=date(2026, 6, 1),
    )
    return notice


def _csv(*rows: str) -> bytes:
    return ("notice_id,category,subcategory\n" + "\n".join(rows) + "\n").encode()


@PLAIN_STATIC
class StaffOnlyTests(TestCase):
    def test_a_stranger_cannot_reach_the_import_page(self):
        """The reason this lives in the admin rather than on an API route."""
        response = self.client.get(IMPORT_URL)
        self.assertEqual(response.status_code, 302)
        self.assertIn("/admin/login/", response["Location"])

    def test_a_stranger_cannot_post_corrections(self):
        _notice("OP1")
        self.client.post(IMPORT_URL, {"file": _file(_csv("OP1,supply,"))})
        self.assertEqual(TenderNotice.objects.get(notice_id="OP1").category, "consulting")


@PLAIN_STATIC
class AdminRoundTripTests(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.staff = get_user_model().objects.create_superuser(
            username="operator", email="op@example.com", password="pw"
        )

    def setUp(self):
        self.client.force_login(self.staff)

    def _import(self, payload: bytes):
        return self.client.post(IMPORT_URL, {"file": _file(payload)}, follow=True)

    def test_the_export_action_carries_enough_to_judge_the_row(self):
        """You cannot classify "Lot 2" from its id, so the title rides along."""
        _notice("OP1")
        response = self.client.post(CHANGELIST, {
            "action": "export_for_category_review",
            "_selected_action": ["OP1"],
        })
        text = b"".join(response.streaming_content).decode()
        self.assertIn("notice_id,category,subcategory,title", text)
        self.assertIn("Procurement of tractors", text)
        self.assertIn("WINNER LLC", text)

    def test_the_import_page_lists_the_legal_values(self):
        """A person editing in Excel has no dropdown to read them off."""
        page = self.client.get(IMPORT_URL).content.decode()
        self.assertIn("consulting", page)
        self.assertIn("engineering", page)
        self.assertIn("Consulting only", page)

    def test_a_correction_is_applied_and_recorded_as_a_human_decision(self):
        _notice("OP1")
        self._import(_csv("OP1,supply,"))

        notice = TenderNotice.objects.get(notice_id="OP1")
        self.assertEqual(notice.category, "supply")
        self.assertEqual(notice.category_source, CategorySource.MANUAL)
        self.assertIsNone(notice.category_confidence)

    def test_re_uploading_an_unedited_export_changes_nothing(self):
        """The property that makes the round trip safe to repeat.

        Without it one accidental re-upload would stamp the whole archive
        `manual` and freeze it against every later automatic correction.
        """
        _notice("OP1")
        response = self._import(_csv("OP1,consulting,other"))

        self.assertContains(response, "0 corrected, 1 already matched")
        self.assertEqual(
            TenderNotice.objects.get(notice_id="OP1").category_source,
            CategorySource.RULES,
        )

    def test_one_bad_value_rejects_the_file_rather_than_half_applying_it(self):
        _notice("OP1")
        _notice("OP2")
        response = self._import(_csv("OP1,supply,", "OP2,spaceflight,"))

        self.assertContains(response, "spaceflight")
        self.assertEqual(TenderNotice.objects.get(notice_id="OP1").category, "consulting")

    def test_a_sub_direction_outside_consulting_is_dropped(self):
        """It would be a claim about a trade the contract is not in."""
        _notice("OP1")
        self._import(_csv("OP1,supply,audit"))
        self.assertEqual(TenderNotice.objects.get(notice_id="OP1").subcategory, "")

    def test_a_row_a_person_already_decided_is_not_overwritten(self):
        _notice("OP1", category_source=CategorySource.MANUAL, category="supply")
        response = self._import(_csv("OP1,it,"))

        self.assertContains(response, "1 left alone")
        self.assertEqual(TenderNotice.objects.get(notice_id="OP1").category, "supply")

    def test_an_unknown_id_is_counted_rather_than_failing_the_upload(self):
        response = self._import(_csv("NOPE,supply,"))
        self.assertContains(response, "1 unknown id")

    def test_a_missing_column_names_what_it_expected(self):
        response = self._import(b"id,category\nOP1,supply\n")
        self.assertContains(response, "notice_id")


def _file(payload: bytes):
    from django.core.files.uploadedfile import SimpleUploadedFile

    return SimpleUploadedFile("categories.csv", payload, content_type="text/csv")
