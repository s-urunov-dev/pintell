"""The public expert directory API.

Read-only, unauthenticated, and deliberately dull: a filtered, sorted, paginated
list of people and the taxonomy they are filed under. It is the answer to the
question the compliance verdict raises and cannot answer — *this tender needs a
Resettlement Specialist and you have not named one; where do I find one* — and
it is reachable on its own, because a vendor also arrives already knowing what
they are missing.

**Why it is public.** Everything served here is what each person publishes about
their own professional life: a name, a link to the profile they wrote, and the
roles they work as. Nothing is behind the session because nothing here is about
the vendor asking. Contact details would be a different question and are not
stored (D19).

**Sorting is the client's, within a fixed set.** ``ordering`` accepts the two
orders a directory is actually read in — by name, and by how recently the row
changed — and nothing else. An open ``order_by`` over user input is an
invitation to sort by a column that is not indexed, on a table that will grow.
"""

from __future__ import annotations

from django.db.models import Count, Prefetch
from rest_framework import viewsets
from rest_framework.permissions import AllowAny

from apps.core.pagination import StandardResultsSetPagination

from .models import Expert, ExpertType
from .serializers import ExpertFamilySerializer, ExpertSerializer, ExpertTypeSerializer


class ExpertTypeViewSet(viewsets.ReadOnlyModelViewSet):
    """`GET /api/experts/types/` — the taxonomy, as families with their roles.

    Returned nested rather than flat because that is how it is rendered: a
    filter panel of five headings, each opening onto its roles. A flat list
    would make every client rebuild the tree from ``family``, and they would
    each rebuild it slightly differently.

    ``expert_count`` rides along on each role. It is what turns the filter panel
    from a list of options into a map of where the directory is thin — a role
    with no one under it is a gap worth seeing rather than a dead filter.
    """

    permission_classes = (AllowAny,)
    serializer_class = ExpertFamilySerializer
    pagination_class = None  # 5 rows; a page boundary here would be noise.
    lookup_field = "slug"

    def get_queryset(self):
        roles = (
            ExpertType.objects.roles()
            .annotate(expert_count=Count("experts"))
            .order_by("position", "name")
        )
        return (
            ExpertType.objects.families()
            .prefetch_related(Prefetch("children", queryset=roles))
            .order_by("position", "name")
        )


class ExpertViewSet(viewsets.ReadOnlyModelViewSet):
    """`GET /api/experts/` and `GET /api/experts/{id}/`.

    Query params: ``role`` (slug, repeatable), ``family`` (slug), ``search``,
    ``ordering``, ``page``, ``page_size``.

    ``role`` repeated is a union, not an intersection — "show me anyone who is a
    Team Leader or a Project Manager" is what a vendor filling one seat means,
    and requiring a person to hold every named role would return nobody in the
    common case and quietly look like an empty directory.
    """

    permission_classes = (AllowAny,)
    serializer_class = ExpertSerializer
    pagination_class = StandardResultsSetPagination
    # An expert is addressed by its integer id, and saying so is what keeps
    # ``/api/experts/types/`` reachable: DRF's default lookup regex matches any
    # non-slash run, so the detail route would claim "types" as a primary key
    # and answer 404 for the taxonomy — the sibling route registered right
    # after it.
    lookup_value_regex = "[0-9]+"
    search_fields = ("full_name",)
    ordering_fields = ("full_name", "updated_at")
    ordering = ("full_name",)

    def get_queryset(self):
        queryset = Expert.objects.prefetch_related("types__parent")

        roles = [slug for slug in self.request.query_params.getlist("role") if slug]
        if roles:
            # ``distinct`` because the join multiplies a person by their matching
            # roles: someone who is both a Team Leader and a Project Manager
            # would otherwise appear twice in a search for either.
            queryset = queryset.filter(types__slug__in=roles).distinct()

        family = self.request.query_params.get("family")
        if family:
            queryset = queryset.filter(types__parent__slug=family).distinct()

        return queryset
