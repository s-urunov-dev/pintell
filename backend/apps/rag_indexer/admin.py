"""The index in the Django admin: what was embedded, and is the store alive.

The React console has the operator-facing version of this. What the Django
admin adds is the row-level one — *which* source failed, with the provider's
own message on it — and it is the screen someone reaching for a shell would
otherwise write a query for.

Read-only throughout. Nothing here is a source of truth: every row is derived
from the mirror by ``archive_to_qdrant`` and editing one would desynchronise
the bookkeeping from the collection it describes, which is exactly the state
the ``chunks_recorded`` / ``points`` pair on the status page exists to detect.
Deletion is allowed and does something useful — clearing a row is how an
operator forces a source back into the queue.
"""

from __future__ import annotations

from django.contrib import admin
from django.template.response import TemplateResponse
from django.urls import path
from django.utils.html import format_html

from .models import PIPELINE_VERSION, IndexedSource
from .services import IndexingService, get_qdrant_service


@admin.register(IndexedSource)
class IndexedSourceAdmin(admin.ModelAdmin):
    list_display = (
        "source_key", "kind", "status", "chunk_count", "char_count",
        "embed_model", "pipeline_version", "indexed_at",
    )
    list_filter = ("kind", "status", "embed_model", "pipeline_version")
    search_fields = ("source_key", "document_id", "notice__notice_id")
    readonly_fields = tuple(
        field.name for field in IndexedSource._meta.fields
    )
    ordering = ("-indexed_at",)
    #: The bookkeeping table is one row per source and the archive has tens of
    #: thousands. Django's default count query on a table that size costs a
    #: sequential scan on every page of the changelist; the approximate pager
    #: is the standard answer and the exact total is of no use here anyway.
    show_full_result_count = False

    change_list_template = "admin/rag_indexer/indexedsource/change_list.html"

    def has_add_permission(self, request) -> bool:
        return False

    def has_change_permission(self, request, obj=None) -> bool:
        return False

    # -- the status page ----------------------------------------------------
    def get_urls(self):
        return [
            path(
                "status/",
                # `admin_view` wraps it, so it is staff-only and carries the
                # admin's own CSRF and permission handling rather than a
                # second implementation of both.
                self.admin_site.admin_view(self.index_status),
                name="rag_indexer_status",
            ),
            *super().get_urls(),
        ]

    def index_status(self, request):
        """Qdrant's health and the archive's coverage, on one page.

        Every number is read live and none is cached. The page is opened by a
        person wondering whether something is wrong, and a cached answer to
        that question is worse than no answer.
        """
        service = IndexingService()
        store = get_qdrant_service()
        collection = store.stats()

        try:
            archive = service.pending_count(focus_only=False)
        except Exception as exc:  # noqa: BLE001 - the page must still render
            archive = {}
            self.message_user(request, f"Could not count the archive: {exc}", level=30)

        done = archive.get("notices_indexed", 0) + archive.get("documents_indexed", 0)
        total = archive.get("sources_total", 0)

        context = {
            **self.admin_site.each_context(request),
            "title": "Semantic index status",
            "opts": self.model._meta,
            "collection": collection,
            "collection_name": store.collection,
            "embed_model": service.embedding.model,
            "embeddings_enabled": service.embedding.enabled(),
            "pipeline_version": PIPELINE_VERSION,
            "archive": archive,
            "coverage": (done / total * 100) if total else 0,
            "failures": IndexedSource.objects.filter(
                status=IndexedSource.Status.FAILED
            ).order_by("-indexed_at")[:20],
        }
        return TemplateResponse(request, "admin/rag_indexer/status.html", context)

    @admin.display(description="Error")
    def short_error(self, obj: IndexedSource):
        if not obj.last_error:
            return "—"
        return format_html("<code>{}</code>", obj.last_error[:160])
