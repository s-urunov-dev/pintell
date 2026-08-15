"""Routes for the operator console API (mounted at /api/admin/)."""

from django.urls import include, path
from rest_framework.routers import DefaultRouter

from .views import (
    AdminDocumentViewSet,
    AdminNoticeViewSet,
    AdminProjectViewSet,
    AdminRequirementViewSet,
    BackfillPartitionViewSet,
    ComplianceStatusView,
    CsrfView,
    LoginView,
    LogoutView,
    MeView,
    OverviewView,
    SyncRunViewSet,
    SystemStatusView,
    TriggerBackfillView,
    TriggerComplianceView,
    TriggerEnrichmentView,
    TriggerSyncView,
)

router = DefaultRouter()
router.register("sync-runs", SyncRunViewSet, basename="admin-sync-run")
router.register("partitions", BackfillPartitionViewSet, basename="admin-partition")
# The drill-down, top down: a project groups notices, a notice links
# documents, a document is what L3 read a requirement out of.
router.register("projects", AdminProjectViewSet, basename="admin-project")
router.register("documents", AdminDocumentViewSet, basename="admin-document")
router.register("notices", AdminNoticeViewSet, basename="admin-notice")
# What the extraction produced, not just how much of it: the compliance
# screen reports counts, this answers "what does this tender demand".
router.register(
    "requirements", AdminRequirementViewSet, basename="admin-requirement"
)

urlpatterns = [
    # Auth
    path("auth/csrf/", CsrfView.as_view(), name="admin-csrf"),
    path("auth/login/", LoginView.as_view(), name="admin-login"),
    path("auth/logout/", LogoutView.as_view(), name="admin-logout"),
    path("auth/me/", MeView.as_view(), name="admin-me"),
    # Read models
    path("overview/", OverviewView.as_view(), name="admin-overview"),
    path("system/", SystemStatusView.as_view(), name="admin-system"),
    # What the automatic compliance extraction is doing right now. Polled by
    # the console, so it stays a read of counts and never queues work.
    path("compliance/", ComplianceStatusView.as_view(), name="admin-compliance"),
    # Operations
    path("actions/sync/", TriggerSyncView.as_view(), name="admin-trigger-sync"),
    path("actions/backfill/", TriggerBackfillView.as_view(), name="admin-trigger-backfill"),
    path("actions/enrich/", TriggerEnrichmentView.as_view(), name="admin-trigger-enrich"),
    path(
        "actions/extract/",
        TriggerComplianceView.as_view(),
        name="admin-trigger-extract",
    ),
    path("", include(router.urls)),
]
