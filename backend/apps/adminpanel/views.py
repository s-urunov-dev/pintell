"""Operator console API (``/api/admin/``).

Separate from the public tender API in every way that matters: session
authentication, staff-only permission, its own throttle scopes, and endpoints
that expose sync internals rather than public listings.

The Django admin at ``/admin/`` is kept as the low-level developer tool; this
API is what the React console at ``/console`` talks to.
"""

from __future__ import annotations

import logging

from django.contrib.auth import authenticate, login, logout
from django.http import Http404
from django.db.models import Count, Max, Q
from django.db.models.functions import Length
from django.utils.decorators import method_decorator
from django.views.decorators.csrf import ensure_csrf_cookie
from django_filters.rest_framework import DjangoFilterBackend
from rest_framework import status, viewsets
from rest_framework.authentication import SessionAuthentication
from rest_framework.decorators import action
from rest_framework.exceptions import ValidationError
from rest_framework.filters import OrderingFilter, SearchFilter
from rest_framework.generics import get_object_or_404
from rest_framework.permissions import AllowAny
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.compliance.models import TenderRequirement
from apps.compliance.tasks import extract_active_requirements
from apps.core.exceptions import LocalizedAPIException
from apps.core.i18n import resolve_language, translate
from apps.tenders.models import (
    BackfillPartition,
    HarvestedDocument,
    SyncRun,
    TenderNotice,
)
from apps.tenders.services.backfill import ensure_partitions
from apps.tenders.tasks import (
    backfill_tender_archive,
    enrich_focus_notices,
    sync_procurement_notices,
)

from .compliance_status import compliance_status
from .permissions import IsStaffUser
from .serializers import (
    AdminNoticeDetailSerializer,
    AdminNoticeListSerializer,
    AdminDocumentSerializer,
    AdminProjectSerializer,
    AdminRequirementSerializer,
    AdminUserSerializer,
    BackfillPartitionSerializer,
    LoginSerializer,
    SyncRunSerializer,
    TriggerBackfillSerializer,
    TriggerComplianceSerializer,
    TriggerEnrichmentSerializer,
    TriggerSyncSerializer,
)
from .services import (
    TaskDispatchError,
    dashboard_overview,
    dispatch_task,
    resanitize_notice,
    reset_partition,
    system_status,
)

logger = logging.getLogger(__name__)


class ServiceUnavailable(LocalizedAPIException):
    """A dependency (broker, database, upstream) refused the work.

    Raised with `message_params={"detail": …}` so the operator still sees the
    concrete failure inside the localised sentence.
    """

    status_code = status.HTTP_503_SERVICE_UNAVAILABLE
    default_code = "service_unavailable"
    message_code = "task_dispatch_failed"


class ConsoleViewMixin:
    """Session auth + staff-only, applied to every console endpoint."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsStaffUser]


# ---------------------------------------------------------------------------
# Authentication
# ---------------------------------------------------------------------------
@method_decorator(ensure_csrf_cookie, name="get")
class CsrfView(APIView):
    """Hands the SPA a CSRF cookie before it posts credentials."""

    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]
    throttle_classes: list = []

    def get(self, request):
        language = resolve_language(request)
        return Response({"detail": translate("csrf_cookie_set", language)})


class LoginView(APIView):
    """Session login for staff accounts.

    Throttled on its own scope so the console is not a password-guessing
    oracle, and non-staff accounts are rejected even when the password is
    correct — a valid public user must not reach operator tooling.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [AllowAny]
    throttle_scope = "admin_login"

    def post(self, request):
        serializer = LoginSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        language = resolve_language(request)

        user = authenticate(
            request,
            username=serializer.validated_data["username"],
            password=serializer.validated_data["password"],
        )
        # Same message for "no such user", "wrong password" and "inactive":
        # the console must not confirm which usernames exist.
        if user is None or not user.is_active:
            logger.info("Console login failed for %r", serializer.validated_data["username"])
            return Response(
                {
                    "error": {
                        "code": "invalid_credentials",
                        "message": translate("invalid_credentials", language),
                        "status": status.HTTP_400_BAD_REQUEST,
                        "language": language,
                    }
                },
                status=status.HTTP_400_BAD_REQUEST,
            )

        if not user.is_staff:
            logger.warning("Non-staff console login attempt by %r", user.get_username())
            return Response(
                {
                    "error": {
                        "code": "not_staff",
                        "message": translate("not_staff", language),
                        "status": status.HTTP_403_FORBIDDEN,
                        "language": language,
                    }
                },
                status=status.HTTP_403_FORBIDDEN,
            )

        login(request, user)
        return Response({"user": AdminUserSerializer(user).data})


class LogoutView(ConsoleViewMixin, APIView):
    def post(self, request):
        logout(request)
        return Response(status=status.HTTP_204_NO_CONTENT)


class MeView(ConsoleViewMixin, APIView):
    def get(self, request):
        return Response({"user": AdminUserSerializer(request.user).data})


# ---------------------------------------------------------------------------
# Dashboard / system
# ---------------------------------------------------------------------------
class OverviewView(ConsoleViewMixin, APIView):
    def get(self, request):
        return Response(dashboard_overview())


class SystemStatusView(ConsoleViewMixin, APIView):
    def get(self, request):
        return Response(system_status())


class ComplianceStatusView(ConsoleViewMixin, APIView):
    """`GET /api/admin/compliance/` — what the automatic extraction is doing.

    Built to be polled: counts and timestamps only, no work triggered by
    reading it. The console refreshes it on a timer so an operator watching a
    sync land can see the extraction follow it.
    """

    def get(self, request):
        return Response(compliance_status())


class TriggerComplianceView(ConsoleViewMixin, APIView):
    """Queue one extraction cycle over the open tenders, now.

    The schedule already does this after every sync; this exists for the two
    cases a schedule cannot serve — a demo, and the minute after someone
    changes a setting and wants to see whether it took.
    """

    throttle_scope = "admin_action"

    def post(self, request):
        serializer = TriggerComplianceSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        try:
            task_id = dispatch_task(
                extract_active_requirements,
                limit=data.get("limit"),
                force=data.get("force", False),
            )
        except TaskDispatchError as exc:
            raise ServiceUnavailable(message_params={"detail": exc}) from exc

        return Response({"queued": True, "task_id": task_id},
                        status=status.HTTP_202_ACCEPTED)


# ---------------------------------------------------------------------------
# Sync runs
# ---------------------------------------------------------------------------
class SyncRunViewSet(ConsoleViewMixin, viewsets.ReadOnlyModelViewSet):
    queryset = SyncRun.objects.all()
    serializer_class = SyncRunSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status", "trigger"]
    search_fields = ["trigger", "error_message"]
    ordering_fields = ["started_at", "finished_at", "created_count", "status"]
    ordering = ["-started_at"]


# ---------------------------------------------------------------------------
# Backfill partitions
# ---------------------------------------------------------------------------
class BackfillPartitionViewSet(ConsoleViewMixin, viewsets.ReadOnlyModelViewSet):
    queryset = BackfillPartition.objects.all()
    serializer_class = BackfillPartitionSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["status", "kind"]
    search_fields = ["key", "label"]
    ordering_fields = ["key", "status", "next_offset", "updated_at", "upstream_total"]
    ordering = ["status", "key"]

    @action(detail=False, methods=["post"], url_path="rescan")
    def rescan(self, request):
        """Register partitions for countries discovered since the last scan."""
        created = ensure_partitions()
        return Response({"created": created, "total": BackfillPartition.objects.count()})

    @action(detail=True, methods=["post"], url_path="reset")
    def reset(self, request, pk=None):
        """Walk this partition again from offset 0."""
        partition = reset_partition(self.get_object())
        return Response(self.get_serializer(partition).data)

    @action(detail=True, methods=["post"], url_path="run")
    def run(self, request, pk=None):
        """Queue one backfill slice for this partition."""
        partition = self.get_object()
        serializer = TriggerBackfillSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            task_id = dispatch_task(
                backfill_tender_archive,
                max_pages=serializer.validated_data.get("pages"),
                rows_per_page=serializer.validated_data.get("rows"),
                partition_key=partition.key,
            )
        except TaskDispatchError as exc:
            raise ServiceUnavailable(message_params={"detail": exc}) from exc

        return Response(
            {"queued": True, "task_id": task_id, "partition": partition.key},
            status=status.HTTP_202_ACCEPTED,
        )


# ---------------------------------------------------------------------------
# Notices (sanitiser audit view)
# ---------------------------------------------------------------------------
class AdminNoticeViewSet(ConsoleViewMixin, viewsets.ReadOnlyModelViewSet):
    lookup_field = "notice_id"
    lookup_url_kwarg = "notice_id"
    lookup_value_regex = "[A-Za-z0-9_-]+"
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = [
        "country",
        "notice_type",
        "procurement_method_code",
        "notice_status",
        # Reaches the middle level of the drill-down directly.
        "project_id",
    ]
    search_fields = ["notice_id", "bid_description", "project_name", "bid_reference_no"]
    ordering_fields = ["notice_date", "deadline_date", "updated_at", "last_synced_at"]
    ordering = ["-last_synced_at"]

    def get_queryset(self):
        # The console shows how many characters survived sanitisation, which is
        # a length — not the text. Computing it in SQL keeps the two large
        # bodies out of the list query entirely; reading them per row would be
        # two extra queries for every notice on the page.
        queryset = TenderNotice.objects.annotate(
            sanitized_chars=Length("notice_text_sanitized"),
            raw_chars=Length("notice_text_raw"),
        )
        if self.action == "list":
            # Both bodies are large and only needed on the detail screen.
            queryset = queryset.defer("notice_text_sanitized", "notice_text_raw")
        return queryset

    def get_serializer_class(self):
        if self.action == "retrieve":
            return AdminNoticeDetailSerializer
        return AdminNoticeListSerializer

    @action(detail=True, methods=["post"], url_path="resanitize")
    def resanitize(self, request, notice_id=None):
        """Re-apply the sanitiser to the stored raw HTML for this notice."""
        notice = get_object_or_404(TenderNotice, pk=notice_id)
        return Response(resanitize_notice(notice))


class AdminProjectViewSet(ConsoleViewMixin, viewsets.ViewSet):
    """The top of the drill-down: project → notice → document → requirement.

    The console had no way in from above. An operator could search notices and
    could search requirements, but could not answer "what is this project, what
    did it publish, and which of those documents did we actually read" without
    already knowing an id — which is the state the World Bank's own search
    leaves you in, and the thing this product exists to improve on.

    A ``ViewSet`` over an aggregate rather than a ``ModelViewSet``: there is no
    project table worth listing (see ``AdminProjectSerializer`` for why), so the
    rows are grouped out of the notices themselves.
    """

    def _base(self, request):
        queryset = TenderNotice.objects.all()
        if request.query_params.get("focus") != "all":
            # The console is about the corpus the product serves. `all` is
            # available because "why is this project missing" is a real
            # question and the answer is usually scope.
            queryset = queryset.focus()
        search = (request.query_params.get("search") or "").strip()
        if search:
            queryset = queryset.filter(
                Q(project_id__icontains=search)
                | Q(project_name__icontains=search)
                | Q(country__icontains=search)
            )
        return queryset

    def list(self, request):
        rows = (
            self._base(request)
            .values("project_id")
            .annotate(
                notices=Count("notice_id", distinct=True),
                requirements=Count("requirements", distinct=True),
                documents=Count("harvested_documents", distinct=True),
                latest_notice_date=Max("notice_date"),
            )
            .order_by("-latest_notice_date", "project_id")
        )

        # `project_name` and `country` are attributes of the project carried on
        # every one of its notices; taking them per group in the same query
        # would need a second aggregate each. One extra query for the page is
        # cheaper and, unlike Max(name), cannot show a name from a different
        # notice than the count came from.
        page = list(rows[:200])
        labels = {
            row["project_id"]: row
            for row in TenderNotice.objects.filter(
                project_id__in=[r["project_id"] for r in page]
            )
            .values("project_id")
            .annotate(
                project_name=Max("project_name"),
                country=Max("country"),
                open_notices=Count(
                    "notice_id",
                    filter=Q(notice_id__in=TenderNotice.objects.focus().values("notice_id")),
                    distinct=True,
                ),
            )
        }
        for row in page:
            label = labels.get(row["project_id"], {})
            row["project_name"] = label.get("project_name") or ""
            row["country"] = label.get("country") or ""
            row["open_notices"] = label.get("open_notices") or 0

        return Response(AdminProjectSerializer(page, many=True).data)

    def retrieve(self, request, pk=None):
        """One project, with every notice it published.

        Not paginated: the largest project in the mirror publishes far fewer
        notices than a page size, and an operator opening a project wants the
        whole list rather than the first twenty-five of it.
        """
        notices = (
            TenderNotice.objects.filter(project_id=pk)
            .annotate(
                requirement_count=Count("requirements", distinct=True),
                document_count=Count("harvested_documents", distinct=True),
            )
            .defer("notice_text_sanitized", "notice_text_raw")
            .order_by("-notice_date")
        )
        if not notices.exists():
            raise Http404(f"No project {pk!r}")

        first = notices.first()
        return Response(
            {
                "project_id": pk,
                "project_name": first.project_name,
                "country": first.country,
                "notices": [
                    {
                        "notice_id": n.notice_id,
                        "bid_description": n.bid_description,
                        "notice_type": n.notice_type,
                        "notice_status": n.notice_status,
                        "notice_date": n.notice_date,
                        "deadline_date": n.deadline_date,
                        "is_open": n.is_open,
                        "requirements": n.requirement_count,
                        "documents": n.document_count,
                    }
                    for n in notices
                ],
            }
        )


class AdminDocumentViewSet(ConsoleViewMixin, viewsets.ReadOnlyModelViewSet):
    """The mirrored documents — the level of the drill-down that was missing.

    There was no way to see a TOR in the console at all, and no way to tell
    which notice or project one belonged to without querying the database. That
    is the level where the compliance claim is actually made: L3 reads these,
    and a requirement's evidence quote points into one.
    """

    serializer_class = AdminDocumentSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["kind", "status", "origin", "has_text_layer"]
    search_fields = ["url", "link_context", "notices__notice_id", "notices__project_id"]
    ordering_fields = ["created_at", "fetched_at", "text_chars", "byte_size"]
    ordering = ["-created_at"]

    def get_queryset(self):
        queryset = (
            HarvestedDocument.objects.prefetch_related("notices")
            .annotate(requirement_count=Count("requirements", distinct=True))
            .defer("text")
        )
        notice_id = self.request.query_params.get("notice_id")
        if notice_id:
            queryset = queryset.filter(notices__notice_id=notice_id)
        project_id = self.request.query_params.get("project_id")
        if project_id:
            queryset = queryset.filter(notices__project_id=project_id)
        return queryset.distinct()


class AdminRequirementViewSet(ConsoleViewMixin, viewsets.ReadOnlyModelViewSet):
    """What the extraction actually produced, row by row.

    The compliance screen reported counts only — "2 requirements" against a
    notice id — which answers "did it run" and not "what does this tender
    demand". Those are different operator questions, and the second one was
    answerable only through the Django admin or the public per-notice API.

    Read-only, and there is no create/update/delete anywhere near it: a
    requirement is what a model extracted from a quoted source, so an operator
    editing one by hand would produce a row whose ``evidence_quote`` no longer
    supports it — a claim with a citation that contradicts it, which is worse
    than no row at all.
    """

    serializer_class = AdminRequirementSerializer
    filter_backends = [DjangoFilterBackend, SearchFilter, OrderingFilter]
    filterset_fields = ["layer", "grounding", "is_mandatory", "applies_to", "key"]
    search_fields = [
        "key",
        "label",
        "evidence_quote",
        "notice__notice_id",
        "notice__bid_description",
    ]
    ordering_fields = ["created_at", "layer", "key", "grounding"]
    ordering = ["-created_at"]

    def get_queryset(self):
        # `select_related` because every row renders four notice fields; without
        # it this is fifty extra queries per page.
        return TenderRequirement.objects.select_related("notice").all()

    @action(detail=False, methods=["get"], url_path="notices")
    def notices(self, request):
        """The tenders that have requirements, for the filter dropdown.

        Built from the requirement table rather than from the notice table, so
        the list only ever offers tenders that would actually return rows —
        a filter that can select an empty result is a filter that wastes a click.
        """
        rows = (
            TenderRequirement.objects.values(
                "notice__notice_id", "notice__bid_description"
            )
            .annotate(requirements=Count("id"))
            .order_by("notice__notice_id")
        )
        return Response(
            [
                {
                    "notice_id": row["notice__notice_id"],
                    "title": row["notice__bid_description"] or "",
                    "requirements": row["requirements"],
                }
                for row in rows
            ]
        )


# ---------------------------------------------------------------------------
# Actions
# ---------------------------------------------------------------------------
class TriggerSyncView(ConsoleViewMixin, APIView):
    """Queue an incremental sync, optionally narrowed to a country/method."""

    throttle_scope = "admin_action"

    def post(self, request):
        serializer = TriggerSyncSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        try:
            task_id = dispatch_task(
                sync_procurement_notices,
                max_pages=serializer.validated_data.get("pages"),
                rows_per_page=serializer.validated_data.get("rows"),
                trigger="console",
                filters=serializer.to_filters() or None,
            )
        except TaskDispatchError as exc:
            raise ServiceUnavailable(message_params={"detail": exc}) from exc

        return Response({"queued": True, "task_id": task_id},
                        status=status.HTTP_202_ACCEPTED)


class TriggerEnrichmentView(ConsoleViewMixin, APIView):
    """Queue one enrichment cycle.

    Classifies the focus feed by direction, mirrors project documents and the
    ESRS, parses contract awards, and looks up supplier websites — each step
    bounded by the numbers supplied here.
    """

    throttle_scope = "admin_action"

    def post(self, request):
        serializer = TriggerEnrichmentSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        kwargs = {
            "classify_limit": data.get("classify"),
            "project_limit": data.get("projects", 20),
            "award_limit": data.get("awards", 200),
            "website_limit": data.get("websites"),
        }

        try:
            task_id = dispatch_task(enrich_focus_notices, **kwargs)
        except TaskDispatchError as exc:
            raise ServiceUnavailable(message_params={"detail": exc}) from exc

        return Response({"queued": True, "task_id": task_id},
                        status=status.HTTP_202_ACCEPTED)


class TriggerBackfillView(ConsoleViewMixin, APIView):
    """Queue one backfill slice (next partition, or a named one)."""

    throttle_scope = "admin_action"

    def post(self, request):
        serializer = TriggerBackfillSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)

        partition_key = (serializer.validated_data.get("partition_key") or "").strip()
        if partition_key and not BackfillPartition.objects.filter(key=partition_key).exists():
            language = resolve_language(request)
            # A list, matching the shape serializer field errors arrive in, so
            # the client has one thing to render per field.
            raise ValidationError(
                {
                    "partition_key": [
                        translate("unknown_partition", language, partition=partition_key)
                    ]
                },
                code="unknown_partition",
            )

        try:
            task_id = dispatch_task(
                backfill_tender_archive,
                max_pages=serializer.validated_data.get("pages"),
                rows_per_page=serializer.validated_data.get("rows"),
                partition_key=partition_key or None,
            )
        except TaskDispatchError as exc:
            raise ServiceUnavailable(message_params={"detail": exc}) from exc

        return Response(
            {"queued": True, "task_id": task_id, "partition": partition_key or "auto"},
            status=status.HTTP_202_ACCEPTED,
        )
