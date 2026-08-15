"""Read-only API for tender notices.

Only GET is exposed — the whole app is a mirror of an upstream open-data
source, so there is nothing here for a client to mutate. Correcting a notice's
direction is a staff job and lives in the Django admin (D41), behind a login,
rather than as an open endpoint here.
"""

from __future__ import annotations


from django.conf import settings
from django.core.cache import cache
from django.db.models import Count, F, Max, Min, Q
from django.utils import timezone
from rest_framework import viewsets
from rest_framework.decorators import action
from rest_framework.exceptions import NotFound
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.pagination import StandardResultsSetPagination

from .award_feed import ROLES, award_rows, participants, similar_awards
from .categories import TenderCategory
from .companies import company_awards, company_rows, company_summary
from .filters import TenderNoticeFilter
from .models import ProjectProfile, SyncRun, TeamLeadProfile, TenderNotice
from .regions import CIS_PLUS_COUNTRIES, COUNTRY_GROUPS
from .consulting import ConsultingAudience
from .subcategories import ConsultingSubcategory
from .serializers import (
    ContractAwardSerializer,
    ProjectDetailSerializer,
    SyncRunSerializer,
    TenderNoticeDetailSerializer,
    TenderNoticeListSerializer,
)
from .services.backfill import backfill_progress
from .services.projects import request_project_sync
from .team_leads import profile_payload, roster, slug_from_url

FACETS_CACHE_KEY = "tenders:facets:v1"
FACETS_CACHE_TTL = 300  # seconds
FACET_LIMIT = 300

# /stats/ is loaded next to every tender list page, and each of its numbers is
# an aggregate over the whole archive. A short TTL keeps the figures live
# enough (the sync itself runs every 30 minutes) at one scan per minute
# instead of one per visitor.
STATS_CACHE_KEY = "tenders:stats:v1"
STATS_CACHE_TTL = 60  # seconds


class TenderNoticeViewSet(viewsets.ReadOnlyModelViewSet):
    """`GET /api/tenders/` and `GET /api/tenders/{id}/`.

    Supported query params:
      ``country``, ``procurement_method`` (code or name), ``notice_type``,
      ``notice_status``, ``project_id``, ``is_open``, ``has_deadline``,
      ``deadline_after``/``deadline_before``, ``notice_date_after``/``_before``,
      ``search``, ``ordering``, ``page``, ``page_size``.

    Filters that read through the parent project — ``project_team_lead``,
    ``project_status``, ``project_agency``, ``project_amount_min``/``_max``,
    ``has_project`` — only match notices whose project has been mirrored.
    """

    lookup_field = "notice_id"
    lookup_url_kwarg = "notice_id"
    lookup_value_regex = "[A-Za-z0-9_-]+"
    filterset_class = TenderNoticeFilter
    search_fields = (
        "bid_description",
        "project_name",
        "bid_reference_no",
        "project_id",
        "contact_organization",
    )
    ordering_fields = (
        "notice_date", "deadline_date", "country", "created_at", "project_amount",
    )
    # Upstream guarantees no ordering, so "newest" is decided locally by
    # notice_date, with the primary key as a stable tie-breaker.
    ordering = ("-notice_date", "-notice_id")

    def get_queryset(self):
        queryset = TenderNotice.objects.all()
        if self.action == "list":
            # The notice body is large and unused by the list UI.
            queryset = queryset.defer("notice_text_sanitized", "notice_text_raw")
            queryset = self._annotate_ordering(queryset)
        elif self.action == "documents":
            # Only the project key is read here — never the notice itself.
            queryset = queryset.only("notice_id", "project_id")
        else:
            # The detail payload embeds the award and the parent project, so
            # both are joined in rather than fetched by two more queries.
            queryset = queryset.select_related("award", "project_ref")
        return queryset

    def _annotate_ordering(self, queryset):
        """Add the project join only when the request actually sorts on it.

        ``?ordering=project_amount`` reads through ``project_ref``, and giving
        it a short public name means an annotation rather than making callers
        spell out the relation. Annotating unconditionally would put a LEFT
        JOIN on every list request instead of the few that sort by financing.
        """
        ordering = self.request.query_params.get("ordering", "")
        if "project_amount" in ordering:
            queryset = queryset.annotate(
                project_amount=F("project_ref__total_amount_usd")
            )
        return queryset

    def get_serializer_class(self):
        if self.action == "retrieve":
            return TenderNoticeDetailSerializer
        return TenderNoticeListSerializer

    @action(detail=False, methods=["get"], url_path="facets")
    def facets(self, request):
        """Distinct values for the frontend filter dropdowns (cached 5 min)."""
        payload = cache.get(FACETS_CACHE_KEY)
        if payload is None:
            # Both the category facet and the country-group facet describe the
            # same focus feed, so each grouping is asked for exactly once and
            # the focus total is summed from the category rows instead of
            # costing another COUNT.
            focus = TenderNotice.objects.focus()
            category_counts = _group_counts(focus, "category")
            country_counts = _group_counts(focus, "country")
            # Sub-directions only exist inside Consulting, so they are counted
            # over that slice rather than the whole feed.
            consulting = focus.filter(category=TenderCategory.CONSULTING)
            sub_counts = _group_counts(consulting, "subcategory")
            audience_counts = _group_counts(consulting, "consulting_audience")
            payload = {
                "countries": _facet_values("country"),
                "procurement_methods": _method_facets(),
                "notice_types": _facet_values("notice_type"),
                "categories": _category_facets(category_counts),
                "subcategories": _subcategory_facets(sub_counts),
                "consulting_audiences": _audience_facets(audience_counts),
                "country_groups": _country_group_facets(country_counts),
                "total_notices": TenderNotice.objects.count(),
                "focus_notices": sum(category_counts.values()),
            }
            cache.set(FACETS_CACHE_KEY, payload, FACETS_CACHE_TTL)
        return Response(payload)

    @action(detail=True, methods=["get"], url_path="documents")
    def documents(self, request, notice_id=None):
        """Every document of the notice's parent project, titles included.

        This is the "open everything belonging to the main project" view: the
        notice's ``project_id`` is the key, and each entry carries a readable
        title plus a direct PDF link.
        """
        notice = self.get_object()
        if not notice.project_id:
            return Response({"project": None, "documents": []})

        profile = (
            ProjectProfile.objects.prefetch_related("documents")
            .filter(pk=notice.project_id)
            .first()
        )
        if profile is None:
            # Not mirrored yet — say so explicitly instead of pretending the
            # project has no documents, and queue the mirror. The periodic
            # cycle only walks the focus feed, so without this a notice outside
            # that feed would report "pending" forever.
            queued = request_project_sync(notice.project_id)
            return Response(
                {
                    "project": None,
                    "documents": [],
                    "pending": True,
                    # False means a mirror is already in flight (or the queue is
                    # unreachable), not that nothing will happen.
                    "queued": queued,
                    "project_id": notice.project_id,
                }
            )

        return Response(ProjectDetailSerializer(profile, context={"request": request}).data)

    @action(detail=False, methods=["get"], url_path="stats")
    def stats(self, request):
        """Headline numbers plus the state of the most recent sync run."""
        payload = cache.get(STATS_CACHE_KEY)
        if payload is None:
            payload = _build_stats()
            cache.set(STATS_CACHE_KEY, payload, STATS_CACHE_TTL)
        return Response(payload)


def _build_stats() -> dict[str, object]:
    """Every headline number, in as few table scans as possible.

    Each block below is one query: the archive-wide figures come from a single
    aggregate (conditional counts instead of one COUNT per number), and the
    focus feed needs three — one aggregate plus the two groupings it reports.
    """
    now = timezone.now()

    aggregates = TenderNotice.objects.aggregate(
        total=Count("notice_id"),
        latest_notice_date=Max("notice_date"),
        earliest_notice_date=Min("notice_date"),
        last_synced_at=Max("last_synced_at"),
        open_notices=Count("notice_id", filter=Q(deadline_date__gte=now)),
    )
    # DISTINCT cannot be folded into the aggregate above without counting
    # distinct values of every other column too, so it stays on its own.
    countries = (
        TenderNotice.objects.exclude(country="").values("country").distinct().count()
    )
    last_run = SyncRun.objects.order_by("-started_at").first()

    return {
        "total_notices": aggregates["total"],
        "open_notices": aggregates["open_notices"],
        "countries": countries,
        "latest_notice_date": aggregates["latest_notice_date"],
        "earliest_notice_date": aggregates["earliest_notice_date"],
        "last_synced_at": aggregates["last_synced_at"],
        "last_sync_run": dict(SyncRunSerializer(last_run).data) if last_run else None,
        # Progress of the historical archive walk (see services/backfill.py).
        # The notice total is handed over so it is not counted twice.
        "archive": backfill_progress(notices_stored=aggregates["total"]),
        # The stage-1 aggregation: open opportunities in the focus region.
        "focus": _focus_stats(now),
        "data_source": {
            "name": settings.WORLDBANK["ATTRIBUTION"],
            "url": settings.WORLDBANK["API_URL"],
            "license": "CC BY 4.0",
            "license_url": "https://creativecommons.org/licenses/by/4.0/",
        },
    }


def _facet_values(field: str) -> list[dict[str, object]]:
    rows = (
        TenderNotice.objects.exclude(**{field: ""})
        .values(field)
        .annotate(count=Count("notice_id"))
        .order_by("-count", field)[:FACET_LIMIT]
    )
    return [{"value": row[field], "count": row["count"]} for row in rows]


def _group_counts(queryset, field: str) -> dict[str, int]:
    """``{value: count}`` for one column — a single GROUP BY."""
    return {
        row[field]: row["count"]
        for row in queryset.values(field).annotate(count=Count("notice_id"))
    }


def _focus_stats(now) -> dict[str, object]:
    """Headline numbers for the actionable feed (three queries, not six)."""
    focus = TenderNotice.objects.focus()
    labels = dict(TenderCategory.choices)

    # "Closing today" replaced "closing within seven days" on the headline
    # tile: a week's warning is a browsing statistic, and the number a bidder
    # acts on is the one that runs out tonight.
    #
    # Measured against `deadline_at` — the resolved instant — for the same
    # reason `open()` is: `deadline_date` is midnight UTC of the closing day,
    # so the old seven-day count included tenders that had already closed and
    # excluded ones closing this evening. The day ends in the deployment's own
    # zone (UTC here), which is up to five hours out for a reader in Tashkent
    # between midnight and 05:00; the alternative is each notice's own local
    # day, which would make "today" mean something different on every row of
    # one list.
    end_of_today = timezone.localtime(now).replace(
        hour=23, minute=59, second=59, microsecond=999999
    )

    totals = focus.aggregate(
        total=Count("notice_id"),
        closing_today=Count("notice_id", filter=Q(deadline_at__lte=end_of_today)),
        classified=Count("notice_id", filter=~Q(category=TenderCategory.UNKNOWN)),
    )
    by_category = _group_counts(focus, "category")
    by_country = _group_counts(focus, "country")

    return {
        "country_group": settings.FOCUS_COUNTRY_GROUP,
        "notice_types": list(TenderNotice.OPPORTUNITY_TYPES),
        "open_only": settings.FOCUS_OPEN_ONLY,
        "total": totals["total"],
        "closing_today": totals["closing_today"],
        "classified": totals["classified"],
        "by_category": [
            {"value": key, "label": labels.get(key, key), "count": count}
            for key, count in sorted(by_category.items(), key=lambda kv: -kv[1])
        ],
        "countries": [
            {"value": country, "count": count}
            for country, count in sorted(by_country.items(), key=lambda kv: -kv[1])
        ],
    }


def _category_facets(counts: dict[str, int]) -> list[dict[str, object]]:
    """Category counts within the actionable feed — the only place they matter."""
    labels = dict(TenderCategory.choices)
    return [
        {
            "value": category,
            "label": labels.get(category, category),
            "count": count,
        }
        for category, count in sorted(counts.items(), key=lambda kv: -kv[1])
    ]


def _subcategory_facets(counts: dict[str, int]) -> list[dict[str, object]]:
    """Sub-direction counts inside Consulting.

    The unclassified bucket is dropped: an empty `subcategory` means the notice
    is not consulting at all, so offering it as a filter would be meaningless.
    """
    labels = dict(ConsultingSubcategory.choices)
    return [
        {
            "value": subcategory,
            "label": labels.get(subcategory, subcategory),
            "count": count,
        }
        for subcategory, count in sorted(counts.items(), key=lambda kv: -kv[1])
        if subcategory
    ]


def _audience_facets(counts: dict[str, int]) -> list[dict[str, object]]:
    """Firms vs individual consultants, counted inside Consulting.

    The blank bucket is dropped like the sub-direction's, but it means
    something different and larger here: not "this is not consulting" but
    "the selection method does not say". Offering it as a filter would invite
    a user to read an unanswered question as an answer.
    """
    labels = dict(ConsultingAudience.choices)
    return [
        {"value": audience, "label": labels.get(audience, audience), "count": count}
        for audience, count in sorted(counts.items(), key=lambda kv: -kv[1])
        if audience
    ]


def _country_group_facets(counts: dict[str, int]) -> list[dict[str, object]]:
    """Curated country groups, with per-country counts for the focus region."""
    return [
        {
            "value": key,
            "label": group["label"],
            "description": group["description"],
            "countries": [
                {
                    "name": country.name,
                    "flag": country.flag,
                    "note": country.note,
                    "count": counts.get(country.name, 0),
                }
                for country in CIS_PLUS_COUNTRIES
            ]
            if key == "cis_plus"
            else [{"name": name, "count": counts.get(name, 0)} for name in group["countries"]],
        }
        for key, group in COUNTRY_GROUPS.items()
    ]


def _method_facets() -> list[dict[str, object]]:
    rows = (
        TenderNotice.objects.exclude(procurement_method_code="")
        .values("procurement_method_code", "procurement_method_name")
        .annotate(count=Count("notice_id"))
        .order_by("-count", "procurement_method_code")[:FACET_LIMIT]
    )
    return [
        {
            "value": row["procurement_method_code"],
            "label": row["procurement_method_name"] or row["procurement_method_code"],
            "count": row["count"],
        }
        for row in rows
    ]


# ---------------------------------------------------------------------------
# Companies (competitor view)
# ---------------------------------------------------------------------------
class CompanyListView(APIView):
    """`GET /api/companies/` — suppliers that have won contracts.

    A roster ordered by wins, not a ranking: see `apps.tenders.companies` for
    why the archive does not support scoring these.

    Query params: ``search``, ``country``, ``category``, ``ordering``
    (``wins``/``name``/``latest``/``value``, ``-`` for descending),
    ``page``, ``page_size``.
    """

    def get(self, request):
        rows = company_rows(
            search=(request.query_params.get("search") or "").strip(),
            country=(request.query_params.get("country") or "").strip(),
            category=(request.query_params.get("category") or "").strip(),
            ordering=request.query_params.get("ordering") or "-wins",
        )

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(
            [
                {
                    "name": row["supplier_name"],
                    "country": row["country"] or "",
                    "website": row["website"] or "",
                    "website_source": row["website_source"] or "",
                    "wins": row["wins"],
                    "first_award": row["first_award"],
                    "latest_award": row["latest_award"],
                    # Only the USD subset is summed; `usd_awards` says how many
                    # of the wins that total actually covers.
                    "total_usd": row["total_usd"],
                    "usd_awards": row["usd_awards"],
                }
                for row in page
            ]
        )


class CompanyDetailView(APIView):
    """`GET /api/companies/{name}/` — one supplier and every contract it won."""

    def get(self, request, name: str):
        summary = company_summary(name)
        if summary is None:
            raise NotFound()

        awards = company_awards(name)
        return Response(
            {
                **summary,
                "awards": [
                    {
                        **ContractAwardSerializer(award).data,
                        "notice_id": award.notice_id,
                        "notice_title": award.notice.bid_description
                        or award.notice.project_name,
                        "notice_country": award.notice.country,
                        "notice_category": award.notice.category,
                    }
                    for award in awards
                ],
            }
        )


# ---------------------------------------------------------------------------
# Contract awards (finished tenders)
# ---------------------------------------------------------------------------
class AwardListView(APIView):
    """`GET /api/awards/` — contracts that have been decided.

    The competitor record as a browsable feed: who won, who else was in the
    room, and for how much. Every row is a notice the mirror already holds, so
    this reads and never fetches.

    Query params: ``search`` (matched across every company named, not only the
    winner), ``country``, ``country_group``, ``category``, ``subcategory``,
    ``role`` (``awardee``/``evaluated``/``rejected``), ``page``, ``page_size``.
    """

    def get(self, request):
        params = request.query_params
        role = (params.get("role") or "").strip().lower()
        rows = award_rows(
            search=(params.get("search") or "").strip(),
            country=(params.get("country") or "").strip(),
            country_group=(params.get("country_group") or "").strip(),
            category=(params.get("category") or "").strip(),
            subcategory=(params.get("subcategory") or "").strip(),
            role=role if role in ROLES else "",
        )

        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(
            [_award_payload(award) for award in page]
        )


class SimilarAwardsView(APIView):
    """`GET /api/tenders/{id}/similar-awards/` — contracts awarded for work
    like this tender's.

    Retrieved by meaning over the semantic index (D45), where this was a
    category filter before (D42). Each row therefore carries two fields the
    filtered version deliberately had none of: `match_score`, the cosine
    similarity, and `match_passage`, **the sentence that matched**.

    The passage is not decoration and it is the reason the score is allowed on
    the row at all. D42 removed ranking because a weighted number is not
    something the reader of a row can check; a cosine distance is worse on that
    axis, not better. What makes it acceptable here is that the row now shows
    the text the match was made on, so there is something to judge besides the
    figure. A client rendering the score without the passage would be putting
    back exactly what D42 took away.

    Empty is a normal answer — a tender that was never indexed, or an index
    that cannot answer, gets no panel rather than a widened one.
    """

    def get(self, request, notice_id: str):
        notice = TenderNotice.objects.filter(pk=notice_id).first()
        if notice is None:
            raise NotFound()

        matches = similar_awards(notice)
        return Response({
            "count": len(matches),
            "results": [_award_payload(award) for award in matches],
        })


def _award_payload(award) -> dict[str, object]:
    """One finished contract, with every company it named.

    `match_score` and `match_passage` are present only when the caller is the
    similarity panel — `similar_awards` attaches them to the instance. On the
    awards feed, where rows are not the answer to "like what?", they are absent
    rather than null: a score of `None` invites a client to render an empty
    relevance chip on a list that was never ranked.
    """
    notice = award.notice
    payload: dict[str, object] = {
        "notice_id": award.notice_id,
        "title": notice.bid_description or notice.project_name,
        "country": notice.country,
        "category": notice.category,
        "subcategory": notice.subcategory,
        "project_name": notice.project_name,
        # The upstream page, so a vendor can check the record at its source —
        # the whole feed is a claim about someone else's business.
        "source_url": notice.source_url,
        "award_date": award.award_date,
        "currency": award.currency,
        "contract_price": award.contract_price,
        "contract_duration": award.contract_duration,
        "participants": participants(award),
    }
    if hasattr(award, "match_passage"):
        payload["match_score"] = round(float(award.match_score), 4)
        payload["match_passage"] = award.match_passage
    return payload


class TeamLeadListView(APIView):
    """`GET /api/team-leads/` — the World Bank staff behind the mirrored projects.

    Query params: ``search``.
    """

    def get(self, request):
        rows = roster(search=(request.query_params.get("search") or "").strip())
        paginator = StandardResultsSetPagination()
        page = paginator.paginate_queryset(rows, request, view=self)
        return paginator.get_paginated_response(page)


class TeamLeadDetailView(APIView):
    """`GET /api/team-leads/{id}/` — one team lead, their projects and tenders.

    Serves only what has already been stored: the endpoint never starts a
    lookup, so a name nobody has enriched yet still resolves — to a page that
    says so, rather than to a spinner or a 404.
    """

    def get(self, request, slug: str):
        profile = TeamLeadProfile.objects.filter(pk=slug_from_url(slug)).first()
        if profile is None:
            raise NotFound()
        return Response(profile_payload(profile))
