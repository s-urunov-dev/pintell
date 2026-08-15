"""Upstream payloads are irregular; mapping must fail soft, never raise."""

from datetime import date

from django.test import SimpleTestCase

from apps.tenders.services.mapping import (
    compute_content_hash,
    map_notice,
    parse_date,
    parse_datetime,
)

FULL_PAYLOAD = {
    "id": "OP00458234",
    "notice_type": "Request for Expression of Interest",
    "noticedate": "22-Jul-2026",
    "notice_lang_name": "English",
    "notice_status": "Published",
    "submission_deadline_date": "2026-08-12T00:00:00Z",
    "submission_deadline_time": "17:00",
    "project_ctry_name": "Western and Central Africa",
    "project_id": "P176932",
    "project_name": "Digital Transformation for Africa",
    "bid_reference_no": "RW-SAA-473606-CS-LCS",
    "bid_description": "Recruitment of Graphic Design Consultant",
    "procurement_group": "CS",
    "procurement_method_code": "LCS",
    "procurement_method_name": "Least Cost Selection",
    "contact_email": "buyer@example.org",
    "submission_date": "2026-07-22T00:00:00Z",
    "notice_text": "<p>Body</p><script>alert(1)</script>",
}

# A real "Contract Award" record: no contact block, no deadline.
MINIMAL_PAYLOAD = {
    "id": "OP00459233",
    "notice_type": "Contract Award",
    "noticedate": "28-Jul-2026",
    "project_ctry_name": "Micronesia, Federated States of",
    "notice_text": "<div class='row'><h4>Contract Award</h4></div>",
}


class ParseHelpersTests(SimpleTestCase):
    def test_parse_worldbank_date_format(self):
        self.assertEqual(parse_date("22-Jul-2026"), date(2026, 7, 22))

    def test_parse_date_rejects_garbage(self):
        self.assertIsNone(parse_date("not a date"))
        self.assertIsNone(parse_date(None))

    def test_parse_datetime_is_timezone_aware(self):
        parsed = parse_datetime("2026-08-12T00:00:00Z")
        self.assertIsNotNone(parsed)
        self.assertIsNotNone(parsed.tzinfo)

    def test_content_hash_is_order_independent(self):
        self.assertEqual(
            compute_content_hash({"a": 1, "b": 2}),
            compute_content_hash({"b": 2, "a": 1}),
        )

    def test_content_hash_changes_with_content(self):
        self.assertNotEqual(
            compute_content_hash({"a": 1}), compute_content_hash({"a": 2})
        )


class MapNoticeTests(SimpleTestCase):
    def test_maps_full_payload(self):
        values = map_notice(FULL_PAYLOAD)
        self.assertEqual(values["notice_id"], "OP00458234")
        self.assertEqual(values["notice_date"], date(2026, 7, 22))
        self.assertEqual(values["country"], "Western and Central Africa")
        self.assertEqual(values["procurement_method_code"], "LCS")
        self.assertEqual(values["contact_email"], "buyer@example.org")
        self.assertIsNotNone(values["deadline_date"])

    def test_sanitizes_notice_text_on_write(self):
        values = map_notice(FULL_PAYLOAD)
        self.assertNotIn("script", values["notice_text_sanitized"].lower())
        self.assertIn("Body", values["notice_text_sanitized"])
        # The raw copy is retained for auditing but never served.
        self.assertIn("script", values["notice_text_raw"].lower())

    def test_missing_optional_blocks_become_blank(self):
        values = map_notice(MINIMAL_PAYLOAD)
        self.assertEqual(values["contact_email"], "")
        self.assertIsNone(values["deadline_date"])
        self.assertEqual(values["notice_id"], "OP00459233")

    def test_payload_without_id_is_rejected(self):
        self.assertIsNone(map_notice({"notice_type": "Contract Award"}))

    def test_long_values_are_truncated_to_column_width(self):
        values = map_notice({**MINIMAL_PAYLOAD, "procurement_method_code": "X" * 90})
        self.assertLessEqual(len(values["procurement_method_code"]), 32)
