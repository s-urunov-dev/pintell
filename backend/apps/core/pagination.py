"""Shared pagination style for the public API."""

from __future__ import annotations

import hashlib
import logging
from collections import OrderedDict

from django.core.cache import cache
from django.core.paginator import Paginator
from django.utils.functional import cached_property
from rest_framework.pagination import PageNumberPagination
from rest_framework.response import Response

logger = logging.getLogger(__name__)


class CachedCountPaginator(Paginator):
    """``Paginator`` that remembers ``COUNT(*)`` for a short while.

    Every paginated request costs two scans of the same filtered set: one to
    count the rows and one to fetch the page. On a table with hundreds of
    thousands of notices the count is the more expensive half, and it is
    identical for every page of the same query — so it is cached, keyed by the
    exact SQL of the filtered queryset.

    The TTL is deliberately short: a browsing session sees a stable total while
    a background sync is inserting rows, and the number is never more than a
    minute behind.
    """

    count_cache_ttl = 60
    count_cache_prefix = "pagination:count:v1:"

    @cached_property
    def count(self) -> int:
        queryset = self.object_list
        if isinstance(queryset, (list, tuple)) or not hasattr(queryset, "count"):
            # A plain sequence — nothing to cache, nothing to query. The
            # isinstance check is what does the work: `hasattr(x, "count")` is
            # true for a list too, but that is `list.count(value)`, which takes
            # an argument and raises TypeError when called like a queryset's.
            # `/api/team-leads/` paginates a list and hit exactly that.
            return len(queryset)

        cache_key = self._count_cache_key(queryset)
        if cache_key is None:
            return queryset.count()

        cached = cache.get(cache_key)
        if cached is not None:
            return cached

        value = queryset.count()
        cache.set(cache_key, value, self.count_cache_ttl)
        return value

    def _count_cache_key(self, queryset) -> str | None:
        """A key derived from the query itself, or ``None`` if it is not safe.

        The SQL string includes every filter value, so two different filter
        combinations can never share a cached total.
        """
        try:
            sql = str(queryset.query)
        except Exception:  # noqa: BLE001 - an uncacheable query is not an error
            logger.debug("Could not stringify queryset for count caching", exc_info=True)
            return None
        digest = hashlib.md5(sql.encode("utf-8"), usedforsecurity=False).hexdigest()
        return f"{self.count_cache_prefix}{digest}"


class StandardResultsSetPagination(PageNumberPagination):
    """Page-number pagination with a client-controlled, bounded page size.

    The extra ``page``/``page_size``/``total_pages`` keys spare the frontend
    from having to parse the ``next``/``previous`` URLs.
    """

    django_paginator_class = CachedCountPaginator
    page_size = 20
    page_size_query_param = "page_size"
    max_page_size = 100

    def get_paginated_response(self, data):
        return Response(
            OrderedDict(
                [
                    ("count", self.page.paginator.count),
                    ("total_pages", self.page.paginator.num_pages),
                    ("page", self.page.number),
                    ("page_size", self.get_page_size(self.request)),
                    ("next", self.get_next_link()),
                    ("previous", self.get_previous_link()),
                    ("results", data),
                ]
            )
        )
