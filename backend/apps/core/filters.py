"""Shared filter backends."""

from __future__ import annotations

from django.db.models import F
from rest_framework.filters import OrderingFilter


class NullsLastOrderingFilter(OrderingFilter):
    """``OrderingFilter`` that always sorts NULLs last.

    Part of the upstream archive predates the ``noticedate`` field: several
    thousand historical notices carry no publication date at all. PostgreSQL
    orders NULLs *first* under ``DESC``, which would push those undated records
    to the top of "newest first" — so every ordering term is rewritten as an
    explicit expression with ``nulls_last=True``.
    """

    def get_ordering(self, request, queryset, view):
        ordering = super().get_ordering(request, queryset, view)
        if not ordering:
            return ordering
        return [self._to_expression(term) for term in ordering]

    @staticmethod
    def _to_expression(term: str):
        if term.startswith("-"):
            return F(term[1:]).desc(nulls_last=True)
        return F(term).asc(nulls_last=True)
