"""Django admin for the compliance tables.

An operator's tool, and mostly a *reading* tool. Extraction writes requirements
and runs; editing one by hand here would silently break the link between a row
and the run that produced it, which is the link the accuracy measurement rests
on (D6). So those two models are read-only in the admin, and only vendor
profiles — data a human enters anyway — stay editable.
"""

from __future__ import annotations

from django.contrib import admin
from django.utils.html import format_html

from .models import (
    ExtractionRun,
    TenderExpertPosition,
    TenderRequirement,
    VendorProfile,
)


@admin.register(ExtractionRun)
class ExtractionRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "notice_id",
        "layers",
        "model",
        "status",
        "requirement_count",
        "cost_usd",
        "duration_ms",
        "created_at",
    )
    list_filter = ("status", "layers", "model", "created_at")
    search_fields = ("notice__notice_id", "model", "prompt_version")
    date_hierarchy = "created_at"
    ordering = ("-created_at",)
    list_per_page = 50
    # Every field is a record of something that already happened; there is
    # nothing here a later edit could make more true.
    readonly_fields = tuple(
        field.name for field in ExtractionRun._meta.fields
    )

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("notice")

    @admin.display(description="requirements")
    def requirement_count(self, obj: ExtractionRun) -> int:
        return obj.requirements.count()


@admin.register(TenderRequirement)
class TenderRequirementAdmin(admin.ModelAdmin):
    list_display = (
        "key",
        "notice_id",
        "layer",
        "grounding_flag",
        "is_mandatory",
        "applies_to",
        "quote_preview",
    )
    # `grounding` first: the operator question this table answers most often is
    # "how much of what we extracted is actually grounded".
    list_filter = ("grounding", "layer", "is_mandatory", "applies_to", "created_at")
    search_fields = ("key", "label", "notice__notice_id", "evidence_quote", "source")
    ordering = ("-created_at",)
    list_per_page = 50
    readonly_fields = tuple(field.name for field in TenderRequirement._meta.fields)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("notice", "run")

    @admin.display(description="grounding", ordering="grounding")
    def grounding_flag(self, obj: TenderRequirement):
        """Colour the one state that keeps a row out of every assessment."""
        if obj.grounding == TenderRequirement.Grounding.NOT_FOUND:
            return format_html(
                '<strong style="color:#b3261e">{}</strong>', obj.get_grounding_display()
            )
        return obj.get_grounding_display()

    @admin.display(description="evidence")
    def quote_preview(self, obj: TenderRequirement) -> str:
        quote = obj.evidence_quote
        return f"{quote[:80]}…" if len(quote) > 80 else quote or "—"


@admin.register(VendorProfile)
class VendorProfileAdmin(admin.ModelAdmin):
    list_display = (
        "name",
        "country",
        "declared_scalars",
        "declared_collections",
        "consented_at",
        "updated_at",
    )
    list_filter = ("country", "created_at")
    search_fields = ("name", "country")
    ordering = ("name",)
    readonly_fields = ("created_at", "updated_at")

    @admin.display(description="values declared")
    def declared_scalars(self, obj: VendorProfile) -> int:
        return len(obj.scalars or {})

    @admin.display(description="records declared")
    def declared_collections(self, obj: VendorProfile) -> str:
        """How much of the portfolio is filled in, per record type.

        A count rather than the JSON itself: the point of glancing at this list
        is to see which profiles are complete enough to assess, and a profile
        that has declared nothing is the normal starting state, not a fault.
        """
        collections = obj.collections or {}
        if not collections:
            return "—"
        return ", ".join(
            f"{entity}: {len(records)}" for entity, records in sorted(collections.items())
        )


@admin.register(TenderExpertPosition)
class TenderExpertPositionAdmin(admin.ModelAdmin):
    """The expert positions extraction read out of a tender.

    Read-only for the same reason ``TenderRequirement`` is: the row belongs to
    the run that produced it, and editing one by hand would break the link the
    accuracy measurement rests on (D6). The directory these positions point at
    *is* editable — that is ``apps.experts`` — and the split is the point.
    """

    list_display = (
        "title",
        "notice_id",
        "role",
        "count",
        "is_mandatory",
        "layer",
        "grounding_flag",
        "quote_preview",
    )
    # ``role`` first with a null filter available: "what are tenders asking for
    # that our taxonomy cannot file" is the question this table answers that no
    # other one can, and it is the one that decides whether a 37th role is
    # needed (D20).
    list_filter = ("role", "grounding", "layer", "is_mandatory", "created_at")
    search_fields = ("title", "notice__notice_id", "evidence_quote")
    ordering = ("-created_at",)
    list_per_page = 50
    readonly_fields = tuple(field.name for field in TenderExpertPosition._meta.fields)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    def get_queryset(self, request):
        return super().get_queryset(request).select_related("notice", "run", "role")

    @admin.display(description="grounding", ordering="grounding")
    def grounding_flag(self, obj: TenderExpertPosition):
        if obj.grounding == TenderRequirement.Grounding.NOT_FOUND:
            return format_html(
                '<strong style="color:#b3261e">{}</strong>', obj.get_grounding_display()
            )
        return obj.get_grounding_display()

    @admin.display(description="evidence")
    def quote_preview(self, obj: TenderExpertPosition) -> str:
        quote = obj.evidence_quote
        return f"{quote[:80]}…" if len(quote) > 80 else quote or "—"
