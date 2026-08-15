"""Infrastructure endpoints: health check and a small service index."""

from __future__ import annotations

from django.conf import settings
from django.db import connection
from rest_framework import status
from rest_framework.response import Response
from rest_framework.reverse import reverse
from rest_framework.views import APIView


class HealthView(APIView):
    """Liveness/readiness probe used by Docker healthchecks."""

    throttle_classes: list = []

    def get(self, request):
        database_ok = True
        try:
            with connection.cursor() as cursor:
                cursor.execute("SELECT 1")
                cursor.fetchone()
        except Exception:  # noqa: BLE001 - the probe must never raise
            database_ok = False

        payload = {
            "status": "ok" if database_ok else "degraded",
            "database": "ok" if database_ok else "unavailable",
        }
        http_status = status.HTTP_200_OK if database_ok else status.HTTP_503_SERVICE_UNAVAILABLE
        return Response(payload, status=http_status)


class ServiceRootView(APIView):
    """Human-friendly index so hitting the bare host is not a 404."""

    throttle_classes: list = []

    def get(self, request):
        return Response(
            {
                "service": "Pintell API",
                "version": "1.0.0",
                "data_source": settings.WORLDBANK["ATTRIBUTION"],
                "license": "https://creativecommons.org/licenses/by/4.0/",
                "endpoints": {
                    "health": reverse("health", request=request),
                    "tenders": reverse("tenders:tender-list", request=request),
                    "facets": reverse("tenders:tender-facets", request=request),
                    "stats": reverse("tenders:tender-stats", request=request),
                    "admin": request.build_absolute_uri("/admin/"),
                },
            }
        )
