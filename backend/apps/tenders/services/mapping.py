"""Translate an upstream notice payload into TenderNotice field values.

Upstream records are irregular: roughly a quarter of them carry the
``contact_*`` / ``submission_deadline_*`` block and the rest do not, and date
formats differ per field. Everything here therefore fails soft — a missing or
malformed value becomes ``""``/``None``, never an exception.
"""

from __future__ import annotations

import hashlib
import json
from datetime import date, datetime
from datetime import timezone as dt_timezone
from typing import Any

from django.utils import timezone

from ..deadlines import resolve_deadline_instant
from ..sanitizers import sanitize_html

# Upstream "noticedate" looks like "28-Jul-2026".
_DATE_FORMATS = ("%d-%b-%Y", "%Y-%m-%d", "%d/%m/%Y", "%m/%d/%Y")

# Max lengths mirror the model's CharField definitions.
_CHAR_LIMITS = {
    "notice_id": 64,
    "notice_type": 255,
    "notice_status": 64,
    "notice_language": 64,
    "deadline_time": 32,
    "country": 255,
    "project_id": 64,
    "bid_reference_no": 255,
    "procurement_group": 32,
    "procurement_method_code": 32,
    "procurement_method_name": 255,
    "contact_name": 255,
    "contact_organization": 512,
    "contact_email": 320,
    "contact_phone_no": 128,
    "contact_country": 255,
    "contact_web_url": 512,
}

# Upstream key -> model field, for the plain string columns.
_STRING_FIELDS = {
    "notice_type": "notice_type",
    "notice_status": "notice_status",
    "notice_lang_name": "notice_language",
    "submission_deadline_time": "deadline_time",
    "project_ctry_name": "country",
    "project_id": "project_id",
    "project_name": "project_name",
    "bid_reference_no": "bid_reference_no",
    "bid_description": "bid_description",
    "procurement_group": "procurement_group",
    "procurement_method_code": "procurement_method_code",
    "procurement_method_name": "procurement_method_name",
    "contact_name": "contact_name",
    "contact_organization": "contact_organization",
    "contact_email": "contact_email",
    "contact_phone_no": "contact_phone_no",
    "contact_address": "contact_address",
    "contact_ctry_name": "contact_country",
    "contact_web_url": "contact_web_url",
}


def clean_str(value: Any, max_length: int | None = None) -> str:
    if value is None:
        return ""
    if isinstance(value, (list, tuple)):
        value = " ".join(str(v) for v in value if v is not None)
    text = str(value).strip()
    if text.lower() in {"none", "null", "n/a"}:
        return ""
    if max_length and len(text) > max_length:
        text = text[:max_length]
    return text


def parse_date(value: Any) -> date | None:
    """Parse ``noticedate``-style values ("28-Jul-2026") into a date."""
    text = clean_str(value)
    if not text:
        return None
    if "T" in text:  # tolerate an ISO timestamp in a date field
        dt = parse_datetime(text)
        return dt.date() if dt else None
    for fmt in _DATE_FORMATS:
        try:
            return datetime.strptime(text, fmt).date()
        except ValueError:
            continue
    return None


def parse_datetime(value: Any) -> datetime | None:
    """Parse ISO-8601 timestamps such as ``2026-08-12T00:00:00Z``."""
    text = clean_str(value)
    if not text:
        return None
    normalised = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(normalised)
    except ValueError:
        parsed_date = parse_date(text)
        if parsed_date is None:
            return None
        dt = datetime.combine(parsed_date, datetime.min.time())
    if timezone.is_naive(dt):
        dt = timezone.make_aware(dt, dt_timezone.utc)
    return dt


def compute_content_hash(payload: dict[str, Any]) -> str:
    """Stable SHA-256 over the upstream payload, ignoring key order."""
    canonical = json.dumps(payload, sort_keys=True, ensure_ascii=False, default=str)
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def map_notice(payload: dict[str, Any]) -> dict[str, Any] | None:
    """Return model field values for ``payload`` (``None`` if unusable)."""
    notice_id = clean_str(payload.get("id"), _CHAR_LIMITS["notice_id"])
    if not notice_id:
        return None

    values: dict[str, Any] = {"notice_id": notice_id}

    for source_key, field_name in _STRING_FIELDS.items():
        values[field_name] = clean_str(payload.get(source_key), _CHAR_LIMITS.get(field_name))

    values["notice_date"] = parse_date(payload.get("noticedate"))
    values["submission_date"] = parse_datetime(payload.get("submission_date"))
    values["deadline_date"] = parse_datetime(payload.get("submission_deadline_date"))
    # Resolved here, at write time, because "is this tender still open" is a
    # SQL question and the resolution needs a per-country time zone that SQL
    # does not have. Doing it on read instead is what the product did before,
    # and it is why the list and the detail page disagreed on the closing day.
    values["deadline_at"] = resolve_deadline_instant(
        values["deadline_date"],
        values["deadline_time"],
        values["country"],
    )

    raw_text = payload.get("notice_text") or ""
    values["notice_text_raw"] = raw_text if isinstance(raw_text, str) else str(raw_text)
    values["notice_text_sanitized"] = sanitize_html(values["notice_text_raw"])

    values["content_hash"] = compute_content_hash(payload)
    values["last_synced_at"] = timezone.now()
    return values
