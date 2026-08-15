"""Root URL configuration."""

from django.conf import settings
from django.contrib import admin
from django.urls import include, path

from apps.core.views import HealthView, ServiceRootView

urlpatterns = [
    path("", ServiceRootView.as_view(), name="service-root"),
    path("admin/", admin.site.urls),
    path("api/health/", HealthView.as_view(), name="health"),
    path("api/", include(("apps.tenders.urls", "tenders"), namespace="tenders")),
    # The expert directory. Under /api/ beside the tenders it serves, and
    # public for the same reason they are: it publishes what each person
    # publishes about themselves, and a vendor needs to read it before deciding
    # whether the product is worth an account.
    path("api/", include(("apps.experts.urls", "experts"), namespace="experts")),
    # Vendor profiles and eligibility assessments. Kept under its own prefix
    # rather than mixed into /api/: the tenders API is a read-only mirror of
    # published data, while this one accepts what a vendor says about itself,
    # and the two have different privacy and retention questions attached.
    path(
        "api/compliance/",
        include(("apps.compliance.urls", "compliance"), namespace="compliance"),
    ),
    # Semantic retrieval over the mirror. Its own version prefix and its own
    # namespace, sharing nothing with the three above: what it serves is a
    # rebuildable index rather than the mirror itself, and its payloads carry
    # page coordinates a deployed viewer draws against — so a breaking change
    # here ships as /api/v2/ rather than as a surprise. See apps/rag_indexer.
    path("api/v1/", include(("apps.rag_indexer.urls", "rag"), namespace="rag")),
    # Operator console API (staff-only). The Django admin above stays as the
    # low-level developer tool; this is what the React console at /console uses.
    path(
        "api/admin/",
        include(("apps.adminpanel.urls", "adminpanel"), namespace="adminpanel"),
    ),
]

if settings.DEBUG:
    # Login/logout controls for DRF's browsable API during development.
    urlpatterns += [path("api-auth/", include("rest_framework.urls"))]
