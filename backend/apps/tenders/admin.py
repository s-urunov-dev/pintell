"""Django admin — a fast way to browse and debug the mirrored data.

It also carries the one write worth doing by hand: correcting a notice's
direction. `TenderNoticeAdmin` exports the rows you have filtered and selected
as a CSV, and reads the corrected file back — see `csv_io.py` for the round
trip's two safety properties. Django has no built-in import/export; this is
about sixty lines rather than a dependency, and it inherits the admin's login,
which an open endpoint would not.
"""

from __future__ import annotations

from django.contrib import admin, messages
from django.http import StreamingHttpResponse
from django.shortcuts import redirect, render
from django.urls import path
from django.utils.html import format_html
from django.utils.safestring import mark_safe

from .categories import TenderCategory
from .csv_io import (
    ImportError_,
    apply_corrections,
    award_rows_csv,
    parse_corrections,
)
from .subcategories import ConsultingSubcategory
from .models import (
    BackfillPartition,
    ContractAward,
    HarvestedDocument,
    ProjectDocument,
    ProjectProfile,
    SyncRun,
    TeamLeadProfile,
    TenderNotice,
)


@admin.register(TenderNotice)
class TenderNoticeAdmin(admin.ModelAdmin):
    list_display = (
        "notice_id",
        "short_description",
        "country",
        "category",
        "procurement_method_code",
        "notice_date",
        "deadline_date",
        "open_flag",
    )
    list_filter = (
        "category",
        "category_source",
        "notice_type",
        "notice_status",
        "procurement_method_code",
        "procurement_group",
        "source",
        "notice_date",
    )
    search_fields = (
        "notice_id",
        "bid_description",
        "project_name",
        "project_id",
        "bid_reference_no",
        "country",
        "contact_organization",
    )
    date_hierarchy = "notice_date"
    ordering = ("-notice_date", "-notice_id")
    list_per_page = 50
    # The archive holds hundreds of thousands of rows: the admin's "x of y
    # total" line would count the whole table a second time on every filtered
    # page. The paginated count alone is enough here.
    show_full_result_count = False
    readonly_fields = (
        "notice_id", "source", "created_at", "updated_at", "last_synced_at",
        "content_hash", "source_link", "sanitized_preview",
        "category", "category_source", "category_confidence",
        "category_rationale", "category_updated_at",
    )
    fieldsets = (
        ("Identity", {"fields": ("notice_id", "source", "source_link", "notice_type",
                                 "notice_status", "notice_language")}),
        ("Direction", {"fields": ("category", "category_source", "category_confidence",
                                  "category_rationale", "category_updated_at")}),
        ("Project", {"fields": ("country", "project_id", "project_name")}),
        ("Bid", {"fields": ("bid_reference_no", "bid_description",
                            "procurement_group", "procurement_method_code",
                            "procurement_method_name")}),
        ("Dates", {"fields": ("notice_date", "submission_date",
                              "deadline_date", "deadline_time")}),
        ("Contact", {"fields": ("contact_name", "contact_organization",
                                "contact_email", "contact_phone_no",
                                "contact_address", "contact_country",
                                "contact_web_url")}),
        ("Notice body", {"fields": ("sanitized_preview",),
                         "description": "Rendered from the sanitised HTML "
                                        "stored in notice_text_sanitized."}),
        ("Sync", {"fields": ("content_hash", "created_at", "updated_at",
                             "last_synced_at"),
                  "classes": ("collapse",)}),
    )

    @admin.display(description="Description", ordering="bid_description")
    def short_description(self, obj: TenderNotice) -> str:
        text = obj.bid_description or obj.project_name or "—"
        return text if len(text) <= 80 else text[:79] + "…"

    @admin.display(description="Open", boolean=True)
    def open_flag(self, obj: TenderNotice) -> bool:
        return bool(obj.is_open)

    @admin.display(description="World Bank page")
    def source_link(self, obj: TenderNotice):
        if not obj.notice_id:
            return "—"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
            obj.source_url, obj.source_url,
        )

    @admin.display(description="Sanitised notice text")
    def sanitized_preview(self, obj: TenderNotice):
        if not obj.notice_text_sanitized:
            return "—"
        # Safe by construction: the value was passed through the strict
        # allow-list sanitiser before it was written to the database.
        return mark_safe(
            '<div style="max-width:900px;max-height:420px;overflow:auto;'
            'padding:12px;border:1px solid #ddd;border-radius:6px;'
            f'background:#fafafa">{obj.notice_text_sanitized}</div>'
        )

    def has_add_permission(self, request) -> bool:
        # Data is mirrored from upstream; hand-created rows would be lies.
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False



    # -- the spreadsheet round trip ------------------------------------

    change_list_template = "admin/tenders/tendernotice/change_list.html"
    actions = ["export_for_category_review"]

    @admin.action(description="Export selected for category review (CSV)")
    def export_for_category_review(self, request, queryset):
        """Hand the selected rows out as a file a person can edit.

        The selection *is* the query: filter the changelist by direction,
        source or country, tick "select all", and the export is exactly that
        set. No query parameters of our own could describe it as well.
        """
        response = StreamingHttpResponse(
            award_rows_csv(queryset.select_related("award").order_by("notice_id")),
            content_type="text/csv; charset=utf-8",
        )
        response["Content-Disposition"] = 'attachment; filename="categories.csv"'
        return response

    def get_urls(self):
        return [
            path(
                "import-categories/",
                self.admin_site.admin_view(self.import_categories),
                name="tenders_tendernotice_import_categories",
            ),
            *super().get_urls(),
        ]

    def import_categories(self, request):
        """Read a corrected export back, or say which line stopped it.

        `admin_site.admin_view` wraps this, so it is staff-only and CSRF-
        protected like every other admin page — the reason this lives here
        rather than behind an open API route.
        """
        context = {
            **self.admin_site.each_context(request),
            "title": "Import corrected categories",
            "categories": TenderCategory.choices,
            "subcategories": [
                (value, label) for value, label in ConsultingSubcategory.choices if value
            ],
        }
        if request.method != "POST":
            return render(
                request, "admin/tenders/tendernotice/import_categories.html", context
            )

        upload = request.FILES.get("file")
        if upload is None:
            self.message_user(request, "Choose a file first.", messages.ERROR)
            return redirect(request.path)

        raw = upload.read()
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            # Spreadsheets saved on Windows in this region are still cp1251.
            try:
                text = raw.decode("cp1251")
            except UnicodeDecodeError:
                self.message_user(
                    request, "That file is not UTF-8 or CP1251 text.", messages.ERROR
                )
                return redirect(request.path)

        try:
            corrections = parse_corrections(text)
        except ImportError_ as exc:
            # Nothing has been written: parsing validates the whole file first.
            self.message_user(request, str(exc), messages.ERROR)
            return redirect(request.path)

        counts = apply_corrections(corrections)
        self.message_user(
            request,
            f"{counts['changed']} corrected, {counts['unchanged']} already matched, "
            f"{counts['protected']} left alone (already set by hand), "
            f"{counts['unknown']} unknown id(s).",
            messages.SUCCESS if counts["changed"] else messages.INFO,
        )
        return redirect("admin:tenders_tendernotice_changelist")


@admin.register(SyncRun)
class SyncRunAdmin(admin.ModelAdmin):
    list_display = (
        "started_at", "status", "trigger", "duration_seconds",
        "notices_seen", "created_count", "updated_count",
        "unchanged_count", "out_of_scope_count", "pages_fetched", "pages_failed",
    )
    list_filter = ("status", "trigger", "started_at")
    ordering = ("-started_at",)
    readonly_fields = tuple(
        f.name for f in SyncRun._meta.fields
    ) + ("duration_seconds",)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(BackfillPartition)
class BackfillPartitionAdmin(admin.ModelAdmin):
    list_display = (
        "key", "status", "progress_display", "next_offset", "upstream_total",
        "pages_done", "pages_failed", "created_count", "updated_at",
    )
    list_filter = ("status", "kind")
    search_fields = ("key", "label")
    ordering = ("status", "-next_offset")
    list_per_page = 60
    readonly_fields = tuple(f.name for f in BackfillPartition._meta.fields) + (
        "progress_display",
    )

    @admin.display(description="Progress")
    def progress_display(self, obj: BackfillPartition) -> str:
        return f"{obj.progress_percent:.0f}%"

    def has_add_permission(self, request) -> bool:
        # Partitions are planned by the backfill service, not by hand.
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


class ProjectDocumentInline(admin.TabularInline):
    model = ProjectDocument
    extra = 0
    fields = ("title", "doc_type", "doc_date", "pdf_url")
    readonly_fields = fields
    can_delete = False
    show_change_link = False

    def has_add_permission(self, request, obj=None) -> bool:
        return False


@admin.register(ProjectProfile)
class ProjectProfileAdmin(admin.ModelAdmin):
    list_display = (
        "project_id", "name", "country", "status",
        "total_amount_display", "documents_count", "esrs_flag",
        "fetched_at", "error_count", "next_retry_at",
    )
    list_filter = ("status", "country", "source", "error_count")
    search_fields = ("project_id", "name", "implementing_agency")
    ordering = ("-fetched_at",)
    inlines = [ProjectDocumentInline]
    readonly_fields = tuple(f.name for f in ProjectProfile._meta.fields)

    @admin.display(description="ESRS", boolean=True)
    def esrs_flag(self, obj: ProjectProfile) -> bool:
        return obj.has_esrs

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

@admin.register(ContractAward)
class ContractAwardAdmin(admin.ModelAdmin):
    list_display = (
        "notice_id", "supplier_name", "supplier_country",
        "currency", "contract_price", "award_date", "website_flag",
    )
    list_filter = ("supplier_country", "currency", "award_date")
    search_fields = ("notice__notice_id", "supplier_name", "supplier_country")
    ordering = ("-award_date",)
    readonly_fields = tuple(f.name for f in ContractAward._meta.fields)

    @admin.display(description="Website", boolean=True)
    def website_flag(self, obj: ContractAward) -> bool:
        return bool(obj.supplier_website)

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


@admin.register(TeamLeadProfile)
class TeamLeadProfileAdmin(admin.ModelAdmin):
    """Read-only, like the rest — but editable in one respect deliberately.

    A derived address is a guess, and an operator who checks one is the
    cheapest way to turn it into a fact. Everything else stays read-only.
    """

    list_display = ("name", "title", "unit", "work_email", "email_source", "checked_at")
    list_filter = ("email_source", "unit")
    search_fields = ("name", "title", "unit", "work_email")
    ordering = ("name",)
    readonly_fields = tuple(
        f.name for f in TeamLeadProfile._meta.fields
        if f.name not in {"work_email", "email_source"}
    )

    def has_add_permission(self, request) -> bool:
        return False


@admin.register(HarvestedDocument)
class HarvestedDocumentAdmin(admin.ModelAdmin):
    """Read-only view of the linked-document corpus.

    The list is filtered by ``status`` first because that is the question this
    table exists to answer: how much of what the notices point at is actually
    reachable, and what is behind a sign-in wall or published as a scan.
    """

    list_display = (
        "short_url", "kind", "status", "text_chars", "page_count",
        "has_text_layer", "attempts", "last_attempt_at",
    )
    list_filter = ("status", "kind", "has_text_layer", "parser")
    search_fields = ("url", "link_context", "sha256")
    ordering = ("-last_attempt_at",)
    # The extracted body runs to hundreds of thousands of characters; it is
    # what the pipeline reads, not what an operator scrolls through.
    exclude = ("text",)
    readonly_fields = tuple(
        f.name for f in HarvestedDocument._meta.fields if f.name != "text"
    )
    filter_horizontal = ("notices",)

    @admin.display(description="url")
    def short_url(self, obj: HarvestedDocument) -> str:
        return obj.url[:90]

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False


admin.site.site_header = "Pintell"
admin.site.site_title = "Pintell admin"
admin.site.index_title = "Mirrored World Bank procurement data"
