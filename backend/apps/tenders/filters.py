"""Query-parameter filters for the tender list endpoint."""

from __future__ import annotations

import django_filters as filters
from django.conf import settings
from django.db.models import Q
from django.utils import timezone

from .categories import TenderCategory
from .models import TenderNotice
from .regions import group_countries


class TenderNoticeFilter(filters.FilterSet):
    """Simple, explicit filters — no matching heuristics at this stage."""

    country = filters.CharFilter(field_name="country", lookup_expr="iexact")
    country_contains = filters.CharFilter(field_name="country", lookup_expr="icontains")
    procurement_method = filters.CharFilter(method="filter_procurement_method")
    notice_type = filters.CharFilter(field_name="notice_type", lookup_expr="iexact")
    subcategory = filters.CharFilter(field_name="subcategory", lookup_expr="iexact")
    consulting_audience = filters.CharFilter(
        field_name="consulting_audience", lookup_expr="iexact"
    )
    notice_status = filters.CharFilter(field_name="notice_status", lookup_expr="iexact")
    project_id = filters.CharFilter(field_name="project_id", lookup_expr="iexact")

    notice_date_after = filters.DateFilter(field_name="notice_date", lookup_expr="gte")
    notice_date_before = filters.DateFilter(field_name="notice_date", lookup_expr="lte")
    deadline_after = filters.DateTimeFilter(field_name="deadline_date", lookup_expr="gte")
    deadline_before = filters.DateTimeFilter(field_name="deadline_date", lookup_expr="lte")

    has_deadline = filters.BooleanFilter(method="filter_has_deadline")
    is_open = filters.BooleanFilter(method="filter_is_open")

    # --- stage-1 product filters -----------------------------------------
    country_group = filters.CharFilter(method="filter_country_group")
    category = filters.CharFilter(field_name="category", lookup_expr="iexact")
    source = filters.CharFilter(field_name="source", lookup_expr="iexact")
    focus = filters.BooleanFilter(method="filter_focus")
    has_award = filters.BooleanFilter(method="filter_has_award")

    # --- parent project ---------------------------------------------------
    # These read through the `project_ref` join, so they only ever match
    # notices whose project has been mirrored. `has_project` is what tells the
    # two apart: no match here can mean "different project" or "not mirrored
    # yet", and a client filtering on financing deserves to know which.
    #
    # `icontains` on the team lead is deliberate: upstream publishes several
    # names in one comma-separated string, so an exact match would find nobody.
    project_team_lead = filters.CharFilter(
        field_name="project_ref__team_lead", lookup_expr="icontains"
    )
    project_status = filters.CharFilter(
        field_name="project_ref__status", lookup_expr="iexact"
    )
    project_agency = filters.CharFilter(
        field_name="project_ref__implementing_agency", lookup_expr="icontains"
    )
    project_amount_min = filters.NumberFilter(
        field_name="project_ref__total_amount_usd", lookup_expr="gte"
    )
    project_amount_max = filters.NumberFilter(
        field_name="project_ref__total_amount_usd", lookup_expr="lte"
    )
    has_project = filters.BooleanFilter(method="filter_has_project")

    class Meta:
        model = TenderNotice
        fields = [
            "country",
            "country_group",
            "procurement_method",
            "notice_type",
            "subcategory",
            "consulting_audience",
            "notice_status",
            "project_id",
            "category",
            "source",
            "project_team_lead",
            "project_status",
            "project_agency",
        ]

    def filter_country_group(self, queryset, name, value):
        """Restrict to a named country group, e.g. ``?country_group=cis_plus``."""
        countries = group_countries(value)
        # An unknown group must return nothing rather than everything —
        # silently ignoring it would show a user the wrong region.
        return queryset.filter(country__in=countries) if countries else queryset.none()

    def filter_focus(self, queryset, name, value):
        """The actionable feed: open deadline + opportunity types + region.

        This is the aggregation the first product stage is defined by, exposed
        as a single switch so the UI and the API cannot drift apart.
        """
        if value is None:
            return queryset

        now = timezone.now()
        countries = group_countries(settings.FOCUS_COUNTRY_GROUP)

        if value:
            focused = queryset.filter(notice_type__in=TenderNotice.OPPORTUNITY_TYPES)
            if settings.FOCUS_OPEN_ONLY:
                focused = focused.filter(deadline_date__gte=now)
            if countries:
                focused = focused.filter(country__in=countries)
            return focused

        # The complement, written as the negation of each rule rather than as
        # "not in (every focus id)": the subquery form materialises the whole
        # focus feed for every request, while this stays a plain WHERE clause.
        # A missing deadline is spelled out because SQL's NULL comparisons
        # would otherwise drop undated notices from both sides of the split.
        complement = ~Q(notice_type__in=TenderNotice.OPPORTUNITY_TYPES)
        if settings.FOCUS_OPEN_ONLY:
            complement |= Q(deadline_date__lt=now) | Q(deadline_date__isnull=True)
        if countries:
            complement |= ~Q(country__in=countries)
        return queryset.filter(complement)

    def filter_has_award(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.filter(award__isnull=not value)

    def filter_has_project(self, queryset, name, value):
        """Whether the parent project has been mirrored yet."""
        if value is None:
            return queryset
        return queryset.filter(project_ref__isnull=not value)

    def filter_procurement_method(self, queryset, name, value):
        """Accept either the code (``RFQ``) or the full name."""
        value = (value or "").strip()
        if not value:
            return queryset
        return queryset.filter(
            Q(procurement_method_code__iexact=value)
            | Q(procurement_method_name__iexact=value)
        )

    def filter_has_deadline(self, queryset, name, value):
        if value is None:
            return queryset
        return queryset.exclude(deadline_date__isnull=value)

    def filter_is_open(self, queryset, name, value):
        if value is None:
            return queryset
        now = timezone.now()
        if value:
            return queryset.filter(deadline_date__gte=now)
        return queryset.filter(deadline_date__lt=now)
