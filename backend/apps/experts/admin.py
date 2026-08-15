"""Django admin for the expert directory — the tool that maintains it.

Unlike the compliance tables, which are read-only here because extraction wrote
them (D6), both models on this page are meant to be edited: the directory is
hand-curated, and this is where the curating happens until a dedicated screen
exists. So the full create / read / update / delete set is available, and the
work goes into making the two mistakes that matter hard to make — tagging an
expert with a family instead of a role, and creating a second row for someone
already listed.
"""

from __future__ import annotations

from django.contrib import admin
from django.db.models import Count
from django.utils.html import format_html

from .models import Expert, ExpertType


class ExpertTypeInline(admin.TabularInline):
    """The roles under a family, edited on the family's own page.

    Because that is how the taxonomy is actually read and extended: someone
    adds a role to a family, having just looked at the six already there.
    """

    model = ExpertType
    fk_name = "parent"
    extra = 0
    fields = ("slug", "name", "signal_terms", "position")
    ordering = ("position", "name")
    verbose_name = "role"
    verbose_name_plural = "roles in this family"


@admin.register(ExpertType)
class ExpertTypeAdmin(admin.ModelAdmin):
    list_display = ("name", "slug", "family", "expert_count", "signal_preview")
    list_filter = ("parent",)
    search_fields = ("name", "slug")
    ordering = ("position", "name")
    list_select_related = ("parent",)
    inlines = (ExpertTypeInline,)

    def get_queryset(self, request):
        # One count for the whole page instead of one query per row: the
        # taxonomy is 41 rows and every one of them would otherwise ask.
        return super().get_queryset(request).annotate(_expert_count=Count("experts"))

    @admin.display(description="family", ordering="parent__name")
    def family(self, obj: ExpertType) -> str:
        return obj.parent.name if obj.parent else "— (family)"

    @admin.display(description="experts", ordering="_expert_count")
    def expert_count(self, obj: ExpertType) -> int:
        return obj._expert_count

    @admin.display(description="signal terms")
    def signal_preview(self, obj: ExpertType) -> str:
        terms = obj.signal_terms or []
        head = ", ".join(str(term) for term in terms[:4])
        return f"{head} …" if len(terms) > 4 else head or "—"


@admin.register(Expert)
class ExpertAdmin(admin.ModelAdmin):
    list_display = ("full_name", "roles", "profile_link", "updated_at")
    # Filtering by role is the question this page is asked most often: "who do
    # we have for resettlement".
    list_filter = ("types", "types__parent")
    search_fields = ("full_name", "linkedin_url")
    ordering = ("full_name",)
    # A two-panel picker rather than a multi-select box: 36 roles do not fit in
    # a scrolling list anyone can read.
    filter_horizontal = ("types",)
    readonly_fields = ("created_at", "updated_at")

    def get_queryset(self, request):
        return super().get_queryset(request).prefetch_related("types__parent")

    @admin.display(description="roles")
    def roles(self, obj: Expert) -> str:
        names = [expert_type.name for expert_type in obj.types.all()]
        return ", ".join(names) if names else "—"

    @admin.display(description="LinkedIn", ordering="linkedin_url")
    def profile_link(self, obj: Expert):
        """The link itself, clickable — the column is only useful if it opens."""
        if not obj.linkedin_url:
            return "—"
        return format_html(
            '<a href="{}" target="_blank" rel="noopener noreferrer">{}</a>',
            obj.linkedin_url,
            obj.linkedin_url.removeprefix("https://www.linkedin.com/"),
        )
