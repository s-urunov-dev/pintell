"""URL routes for the tenders app."""

from django.urls import path
from rest_framework.routers import DefaultRouter

from .views import (
    AwardListView,
    CompanyDetailView,
    CompanyListView,
    SimilarAwardsView,
    TeamLeadDetailView,
    TeamLeadListView,
    TenderNoticeViewSet,
)

router = DefaultRouter()
router.register("tenders", TenderNoticeViewSet, basename="tender")

# Produces:
#   /api/tenders/            -> tender-list
#   /api/tenders/{id}/       -> tender-detail
#   /api/tenders/facets/     -> tender-facets
#   /api/tenders/stats/      -> tender-stats
urlpatterns = router.urls + [
    path("awards/", AwardListView.as_view(), name="award-list"),
    # Registered by hand rather than as a router `@action` because it reads
    # awards, not notices: the notice id is the lookup, but nothing in the
    # tender serializer is involved.
    path(
        "tenders/<str:notice_id>/similar-awards/",
        SimilarAwardsView.as_view(),
        name="similar-awards",
    ),
    path("companies/", CompanyListView.as_view(), name="company-list"),
    # The company name is the only identity a supplier has upstream, so it is
    # the key. `<path:...>` because names contain slashes, dots and commas.
    path("companies/<path:name>/", CompanyDetailView.as_view(), name="company-detail"),
    path("team-leads/", TeamLeadListView.as_view(), name="team-lead-list"),
    # The id is the folded name hyphenated ("mohini-kak"); see team_leads.py.
    path("team-leads/<str:slug>/", TeamLeadDetailView.as_view(), name="team-lead-detail"),
]
