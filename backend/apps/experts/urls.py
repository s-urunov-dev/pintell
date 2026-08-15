"""URL routes for the expert directory."""

from rest_framework.routers import DefaultRouter

from .views import ExpertTypeViewSet, ExpertViewSet

router = DefaultRouter()
router.register("experts", ExpertViewSet, basename="expert")
# Registered under ``experts/types`` rather than at the top level: the taxonomy
# is not a thing on its own, it is how this directory is indexed, and a route
# named ``/api/types/`` would say nothing about what it types.
router.register("experts/types", ExpertTypeViewSet, basename="expert-type")

# Produces, under /api/:
#   experts/              -> expert-list      (role, family, search, ordering)
#   experts/{id}/         -> expert-detail
#   experts/types/        -> expert-type-list (families, each with its roles)
#   experts/types/{slug}/ -> expert-type-detail
urlpatterns = router.urls
