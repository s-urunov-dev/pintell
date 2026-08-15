"""Read-only serializers for the public tender API.

Two shapes: a slim one for lists (no notice body, so pages stay light) and a
full one for the detail view. ``notice_text_raw`` is never exposed — only the
sanitised body leaves the backend.
"""

from __future__ import annotations

from rest_framework import serializers

from . import esrs
from .contacts import build_contacts
from .deadlines import resolve_deadline
from .models import (
    ContractAward,
    ProjectDocument,
    ProjectProfile,
    SyncRun,
    TenderNotice,
)
from .notice_links import extract_links, find_tor_email, mentions_tor
from .sanitizers import html_to_text
from .services.ai.people import profiles_for


class TenderNoticeListSerializer(serializers.ModelSerializer):
    id = serializers.CharField(source="notice_id", read_only=True)
    source_url = serializers.CharField(read_only=True)
    is_open = serializers.BooleanField(read_only=True, allow_null=True)
    days_until_deadline = serializers.IntegerField(read_only=True, allow_null=True)
    deadline = serializers.SerializerMethodField()

    class Meta:
        model = TenderNotice
        fields = (
            "id",
            "source",
            "notice_type",
            "notice_status",
            "notice_date",
            "deadline_date",
            "deadline_time",
            "deadline",
            "country",
            "project_id",
            "project_name",
            "bid_reference_no",
            "bid_description",
            "procurement_group",
            "procurement_method_code",
            "procurement_method_name",
            "category",
            "category_source",
            "category_confidence",
            "subcategory",
            "subcategory_confidence",
            "consulting_audience",
            "is_open",
            "days_until_deadline",
            "source_url",
        )
        read_only_fields = fields

    def get_deadline(self, obj: TenderNotice) -> dict | None:
        """The deadline as a real instant, so the UI can count down to it.

        ``None`` when the country is not one we can place on the map or no
        date was published — the client then falls back to whole days rather
        than showing a clock it cannot justify.

        **The instant is the stored column, not a fresh derivation of it**, and
        that is the only thing here worth arguing about. ``resolve_deadline``
        is called for what it alone knows — the zone, the published wall clock,
        whether the zone is a guess — but ``at`` comes from ``deadline_at``,
        because that is the value the rest of the system already committed to:
        ``bidding_open()`` filters on it and ``days_until_deadline`` counts from
        it. Recomputing it here made the countdown a *second* opinion, and two
        opinions derived from the same three fields still diverge, because
        ``save()`` reads ``deadline_date`` as it was handed in and this reads it
        back out of Postgres in UTC. A value written as midnight in a +05:00
        zone comes back as 19:00 the day before, and ``.date()`` then names a
        different day: the card said "closes today" while the panel counted down
        to tomorrow, off by exactly twenty-four hours. One instant, computed at
        one write choke point, is what makes the two agree by construction.
        """
        resolved = resolve_deadline(obj.deadline_date, obj.deadline_time, obj.country)
        if resolved is None:
            return None
        return {
            # The fallback is for a row saved before the column existed; every
            # write path fills it (`TenderNotice.save`, `services.mapping`).
            "at": obj.deadline_at or resolved.at,
            "timezone": resolved.timezone,
            "local_time": resolved.local_time,
            "approximate": resolved.approximate,
        }


class ProjectDocumentSerializer(serializers.ModelSerializer):
    """One published project document — title first, PDF one click away."""

    class Meta:
        model = ProjectDocument
        fields = (
            "guid", "title", "doc_type", "doc_date", "language",
            "pdf_url", "text_url", "page_url",
        )
        read_only_fields = fields


class ProjectProfileSerializer(serializers.ModelSerializer):
    """Project dashboard: financing, agency, and the ESRS summary."""

    has_esrs = serializers.BooleanField(read_only=True)
    esrs = serializers.SerializerMethodField()

    class Meta:
        model = ProjectProfile
        fields = (
            "project_id", "source", "name", "country", "status",
            "lending_instrument", "implementing_agency", "team_lead",
            "sectors", "themes",
            "total_amount_display", "total_amount_usd", "commitment_amount_usd",
            "board_approval_date", "closing_date", "documents_count",
            "project_url", "has_esrs", "esrs",
            "fetched_at", "documents_fetched_at",
        )
        read_only_fields = fields

    def get_esrs(self, obj: ProjectProfile) -> dict | None:
        if not obj.has_esrs:
            return None
        return {
            "title": obj.esrs_title,
            "report_no": obj.esrs_report_no,
            "date": obj.esrs_date,
            "pdf_url": obj.esrs_pdf_url,
            "page_url": obj.esrs_page_url,
        }


class ProjectDetailSerializer(ProjectProfileSerializer):
    documents = ProjectDocumentSerializer(many=True, read_only=True)

    class Meta(ProjectProfileSerializer.Meta):
        fields = ProjectProfileSerializer.Meta.fields + ("documents",)
        read_only_fields = fields


class ContractAwardSerializer(serializers.ModelSerializer):
    """Who won, for how much — the competitor-analysis payload."""

    class Meta:
        model = ContractAward
        fields = (
            "supplier_name", "supplier_reference", "supplier_address",
            "supplier_country", "supplier_website", "supplier_website_source",
            "currency", "bid_price_opening", "evaluated_price", "contract_price",
            "award_date", "contract_duration",
            "awarded_bidders", "evaluated_bidders", "rejected_bidders",
        )
        read_only_fields = fields


class TenderNoticeDetailSerializer(TenderNoticeListSerializer):
    contact = serializers.SerializerMethodField()
    contacts = serializers.SerializerMethodField()
    award = ContractAwardSerializer(read_only=True)
    project = serializers.SerializerMethodField()
    tor = serializers.SerializerMethodField()

    class Meta(TenderNoticeListSerializer.Meta):
        fields = TenderNoticeListSerializer.Meta.fields + (
            "notice_language",
            "submission_date",
            "contact",
            "contacts",
            "category_rationale",
            "category_updated_at",
            "award",
            "project",
            "tor",
            # Sanitised on write with a strict allow-list (see sanitizers.py).
            "notice_text_sanitized",
            "created_at",
            "updated_at",
            "last_synced_at",
        )
        read_only_fields = fields

    # Three fields need the same two expensive things — the flattened body and
    # the parent project — so each is computed once and reused. Keyed by notice
    # id rather than stored on self, because a serializer instance is reusable.
    def _body_text(self, obj: TenderNotice) -> str:
        cache = self.context.setdefault("_body_text", {})
        if obj.pk not in cache:
            cache[obj.pk] = html_to_text(obj.notice_text_sanitized)
        return cache[obj.pk]

    def _profile(self, obj: TenderNotice) -> ProjectProfile | None:
        cache = self.context.setdefault("_profile", {})
        if obj.pk not in cache:
            cache[obj.pk] = self._lookup_profile(obj)
        return cache[obj.pk]

    @staticmethod
    def _lookup_profile(obj: TenderNotice) -> ProjectProfile | None:
        """The mirrored project, from the join when there is one.

        ``project_ref`` is normally already joined in by the viewset, so this
        costs nothing. The fallback covers the gap between a project being
        mirrored and its notices being linked — a window of one write, but a
        reader inside it should still see the project.
        """
        if obj.project_ref_id:
            return obj.project_ref
        return (
            ProjectProfile.objects.filter(pk=obj.project_id).first()
            if obj.project_id
            else None
        )

    def get_project(self, obj: TenderNotice) -> dict | None:
        """The parent project, when it has been mirrored already."""
        profile = self._profile(obj)
        if profile is None:
            return None
        return ProjectProfileSerializer(profile, context=self.context).data

    def get_contacts(self, obj: TenderNotice) -> dict:
        """Everyone reachable about this notice, strongest source first.

        Three tiers, described in :mod:`apps.tenders.contacts`: the notice's own
        contact fields, the people named in its address block, and the Bank's
        team leads for the parent project. The tiers are sent whole and in
        order; which of them to show, and how loudly, is the client's decision.

        Team-lead enrichment is read from storage only — this endpoint never
        starts a lookup, so a cold profile renders as a bare name rather than
        making the page wait on a web search. The ESRS contacts are read from
        the mirror on the same terms: one indexed lookup against a document that
        is already fetched, and nothing at all when it is not.
        """
        profile = self._profile(obj)
        names = (
            [" ".join(part.split()) for part in profile.team_lead.split(",")]
            if profile and profile.team_lead
            else []
        )

        result = build_contacts(
            obj,
            profile,
            body_text=self._body_text(obj),
            enrichment=profiles_for(names),
            esrs_contacts=esrs.contacts_for(profile) if profile else (),
        )
        return {
            "total": result["total"],
            "groups": [
                {
                    "tier": group["tier"],
                    "priority": group["priority"],
                    "contacts": [_contact_payload(c) for c in group["contacts"]],
                }
                for group in result["groups"]
            ],
        }

    def get_tor(self, obj: TenderNotice) -> dict:
        """Where to get the Terms of Reference, read out of the notice body.

        Detail only: it means re-flattening the whole body, which would be
        wasted work on a list of twenty cards that do not show it.
        """
        text = self._body_text(obj)
        links = extract_links(text)
        return {
            "links": [
                {"url": link.url, "kind": link.kind, "context": link.context}
                for link in links
            ],
            # Only useful when nothing was linked, but the frontend decides
            # that — sending both keeps this endpoint free of display logic.
            "email": find_tor_email(text),
            "mentioned": mentions_tor(text),
        }

    def get_contact(self, obj: TenderNotice) -> dict[str, str]:
        """The notice's own contact fields, unchanged.

        Superseded by ``contacts`` (which carries this as tier 1) but kept as
        published: it is the shape existing clients read, and dropping it would
        break them for no gain.
        """
        return {
            "name": obj.contact_name,
            "organization": obj.contact_organization,
            "email": obj.contact_email,
            "phone": obj.contact_phone_no,
            "address": obj.contact_address,
            "country": obj.contact_country,
            "web_url": obj.contact_web_url,
        }


def _contact_payload(contact) -> dict:
    """One :class:`apps.tenders.contacts.Contact` as JSON.

    Written out by hand rather than via ``dataclasses.asdict`` so the wire
    format is a decision rather than a side effect of the dataclass.
    """
    return {
        "name": contact.name,
        "role": contact.role,
        "organization": contact.organization,
        "email": contact.email,
        "alternate_emails": list(contact.alternate_emails),
        "phone": contact.phone,
        "address": contact.address,
        "country": contact.country,
        "website": contact.website,
        "purpose": contact.purpose,
        "context": contact.context,
        "source": contact.source,
        "same_as_primary": contact.same_as_primary,
        "email_confirmed": contact.email_confirmed,
        "links": list(contact.links),
        "summary": contact.summary,
        "profile_id": contact.profile_id,
    }


class SyncRunSerializer(serializers.ModelSerializer):
    duration_seconds = serializers.FloatField(read_only=True, allow_null=True)

    class Meta:
        model = SyncRun
        fields = (
            "id",
            "started_at",
            "finished_at",
            "duration_seconds",
            "status",
            "trigger",
            "pages_fetched",
            "pages_failed",
            "notices_seen",
            "created_count",
            "updated_count",
            "unchanged_count",
            "upstream_total",
        )
        read_only_fields = fields
