"""Serializers for the operator console API."""

from __future__ import annotations

from django.contrib.auth import get_user_model
from rest_framework import serializers

from apps.compliance import expressions
from apps.compliance.models import TenderRequirement
from apps.tenders.models import (
    BackfillPartition,
    HarvestedDocument,
    SyncRun,
    TenderNotice,
)

User = get_user_model()


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------
class LoginSerializer(serializers.Serializer):
    username = serializers.CharField(max_length=150, trim_whitespace=True)
    password = serializers.CharField(max_length=256, style={"input_type": "password"})


class AdminUserSerializer(serializers.ModelSerializer):
    class Meta:
        model = User
        fields = ("id", "username", "email", "first_name", "last_name",
                  "is_staff", "is_superuser", "last_login")
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------
class SyncRunSerializer(serializers.ModelSerializer):
    duration_seconds = serializers.FloatField(read_only=True, allow_null=True)

    class Meta:
        model = SyncRun
        fields = (
            "id", "started_at", "finished_at", "duration_seconds", "status",
            "trigger", "pages_requested", "pages_fetched", "pages_failed",
            "notices_seen", "created_count", "updated_count", "unchanged_count",
            "skipped_count", "out_of_scope_count", "upstream_total", "error_message",
        )
        read_only_fields = fields


class BackfillPartitionSerializer(serializers.ModelSerializer):
    progress_percent = serializers.FloatField(read_only=True)
    reachable_total = serializers.IntegerField(read_only=True, allow_null=True)
    is_done = serializers.BooleanField(read_only=True)

    class Meta:
        model = BackfillPartition
        fields = (
            "id", "key", "kind", "label", "filters", "status",
            "next_offset", "upstream_total", "reachable_total",
            "progress_percent", "is_done", "pages_done", "pages_failed",
            "created_count", "updated_count", "unchanged_count",
            "last_error", "started_at", "finished_at", "updated_at",
        )
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Notices (inspection view — shows what sanitisation actually did)
# ---------------------------------------------------------------------------
class AdminNoticeListSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="notice_id", read_only=True)
    # Both body lengths are measured in SQL (see AdminNoticeViewSet.get_queryset).
    # Reading them off the model instead would undo the ``defer()`` on the two
    # large text columns and load every body one extra query at a time.
    sanitized_chars = serializers.IntegerField(read_only=True)
    raw_chars = serializers.IntegerField(read_only=True)
    source_url = serializers.CharField(read_only=True)

    class Meta:
        model = TenderNotice
        fields = (
            "id", "notice_type", "notice_status", "country", "project_id",
            "bid_reference_no", "bid_description", "notice_date",
            "deadline_date", "procurement_method_code", "sanitized_chars",
            "raw_chars", "last_synced_at", "updated_at", "source_url",
        )
        read_only_fields = fields


class AdminNoticeDetailSerializer(AdminNoticeListSerializer):
    """Includes both bodies so an operator can audit the sanitiser.

    ``notice_text_raw`` is untrusted third-party HTML. The console renders it
    as escaped text in a code block — never as markup.
    """

    class Meta(AdminNoticeListSerializer.Meta):
        fields = AdminNoticeListSerializer.Meta.fields + (
            "project_name", "notice_language", "submission_date",
            "deadline_time", "procurement_group", "procurement_method_name",
            "contact_name", "contact_organization", "contact_email",
            "contact_phone_no", "contact_address", "contact_country",
            "contact_web_url", "content_hash", "created_at",
            "notice_text_sanitized", "notice_text_raw",
        )
        read_only_fields = fields


# ---------------------------------------------------------------------------
# Action payloads
# ---------------------------------------------------------------------------
class TriggerSyncSerializer(serializers.Serializer):
    pages = serializers.IntegerField(required=False, min_value=1, max_value=200)
    rows = serializers.IntegerField(required=False, min_value=1, max_value=500)
    country = serializers.CharField(required=False, allow_blank=True, max_length=255)
    method = serializers.CharField(required=False, allow_blank=True, max_length=32)

    def to_filters(self) -> dict[str, str]:
        data = self.validated_data
        filters: dict[str, str] = {}
        if data.get("country"):
            filters["project_ctry_name"] = data["country"].strip()
        if data.get("method"):
            filters["procurement_method_code"] = data["method"].strip().upper()
        return filters


class TriggerBackfillSerializer(serializers.Serializer):
    pages = serializers.IntegerField(required=False, min_value=1, max_value=500)
    rows = serializers.IntegerField(required=False, min_value=1, max_value=500)
    partition_key = serializers.CharField(required=False, allow_blank=True, max_length=255)


class TriggerEnrichmentSerializer(serializers.Serializer):
    """Bounds for one enrichment cycle (classify / projects / awards / websites)."""

    classify = serializers.IntegerField(required=False, min_value=0, max_value=500)
    projects = serializers.IntegerField(required=False, min_value=0, max_value=100)
    awards = serializers.IntegerField(required=False, min_value=0, max_value=2000)
    websites = serializers.IntegerField(required=False, min_value=0, max_value=50)



# ---------------------------------------------------------------------------
# The project → notice → document → requirement drill-down
# ---------------------------------------------------------------------------
class AdminProjectSerializer(serializers.Serializer):
    """A World Bank project, assembled from the notices that name it.

    Not built on ``ProjectProfile``, and that is a data fact rather than a
    preference: the profile table is populated by a separate enrichment pass and
    holds 15 rows, while every one of the 25 000 mirrored notices carries a
    ``project_id``. Keying the top of the drill-down on the profile would hide
    546 of 561 projects — including, on any given day, most of the open ones.
    The profile is an enrichment of this row, never its source.
    """

    project_id = serializers.CharField()
    project_name = serializers.CharField()
    country = serializers.CharField()
    notices = serializers.IntegerField()
    open_notices = serializers.IntegerField()
    documents = serializers.IntegerField()
    requirements = serializers.IntegerField()
    latest_notice_date = serializers.DateField(allow_null=True)


class AdminDocumentSerializer(serializers.ModelSerializer):
    """A mirrored document, and — the point of this screen — what it belongs to.

    ``notice_ids`` is a list because identity here is the **URL**, not the
    notice: one Terms of Reference is routinely linked by several notices of the
    same project, and the harvester deliberately stores it once. A serializer
    that flattened it to a single notice would be inventing a relationship the
    corpus does not have, and the operator question — "which tender is this TOR
    for?" — is sometimes answered by more than one id.
    """

    id = serializers.CharField(source="url_hash", read_only=True)
    notice_ids = serializers.SerializerMethodField()
    project_ids = serializers.SerializerMethodField()
    requirements = serializers.SerializerMethodField()

    class Meta:
        model = HarvestedDocument
        fields = (
            "id", "url", "kind", "status", "origin", "link_context",
            "content_type", "byte_size", "text_chars", "page_count",
            "has_text_layer", "parser", "parse_error", "http_status",
            "last_error", "fetched_at", "created_at",
            "notice_ids", "project_ids", "requirements",
        )
        read_only_fields = fields

    def get_notice_ids(self, obj: HarvestedDocument) -> list[str]:
        # `prefetch_related("notices")` in the viewset: without it this is one
        # query per row, and the list page shows fifty.
        return [notice.notice_id for notice in obj.notices.all()]

    def get_project_ids(self, obj: HarvestedDocument) -> list[str]:
        seen = {n.project_id for n in obj.notices.all() if n.project_id}
        return sorted(seen)

    def get_requirements(self, obj: HarvestedDocument) -> int:
        """How many requirements were read out of this document.

        Zero is a meaningful answer and the reason the column exists: a document
        that was fetched and parsed but produced nothing is either genuinely
        free of criteria or a failure of the layer that read it, and those are
        the rows worth looking at.
        """
        return getattr(obj, "requirement_count", 0)


class AdminRequirementSerializer(serializers.ModelSerializer):
    """One extracted requirement, with the tender it belongs to named.

    The notice fields are flattened onto the row rather than nested. An operator
    reading this table is scanning for "which tender is this?" — the answer has
    to be in the row, not one click away, and a nested object would either cost
    a request per row or push the answer off the screen.

    ``summary`` is the expression rendered as a sentence. Without it the console
    would show the stored JSON tree, which for the nested case is four levels of
    braces describing one line of a bidding document. ``expression`` is still
    returned alongside it, because the summary is a convenience and the tree is
    the thing the verdict is actually computed from — an operator auditing a
    wrong verdict needs the second, not the first.
    """

    notice_id = serializers.CharField(source="notice.notice_id", read_only=True)
    notice_title = serializers.CharField(
        source="notice.bid_description", read_only=True
    )
    notice_country = serializers.CharField(source="notice.country", read_only=True)
    notice_deadline = serializers.DateTimeField(
        source="notice.deadline_date", read_only=True
    )
    summary = serializers.SerializerMethodField()
    source_document_id = serializers.PrimaryKeyRelatedField(
        source="source_document", read_only=True
    )

    class Meta:
        model = TenderRequirement
        fields = (
            "id", "notice_id", "notice_title", "notice_country", "notice_deadline",
            "layer", "key", "label", "summary", "expression", "applies_to",
            "is_mandatory", "grounding", "evidence_quote", "source",
            "source_document_id", "created_at",
        )
        read_only_fields = fields

    def get_summary(self, obj: TenderRequirement) -> str:
        """Render the tree, or say plainly that it could not be rendered.

        A stored expression can be malformed — that is the whole reason
        ``parse_node`` raises rather than guessing. This table is the screen an
        operator would be looking at *because* something is wrong, so it must
        survive the bad row and point at it instead of returning a 500 for the
        other forty-nine rows on the page.
        """
        try:
            return expressions.describe(expressions.parse_node(obj.expression))
        except (expressions.ExpressionError, AttributeError, TypeError, ValueError):
            return ""


class TriggerComplianceSerializer(serializers.Serializer):
    """Bounds for one manual extraction cycle over the open tenders.

    ``force`` re-reads notices that already have a run for the same layer set.
    Capped at a small number and defaulting to off, because it is the one flag
    here that can spend money twice on a question already answered.
    """

    limit = serializers.IntegerField(required=False, min_value=1, max_value=200)
    force = serializers.BooleanField(required=False, default=False)
