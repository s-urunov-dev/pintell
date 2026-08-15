"""Routes for the semantic index, mounted at ``/api/v1/``.

Its own prefix and its own namespace, sharing nothing with ``/api/`` (the
mirror), ``/api/compliance/`` (verdicts) or ``/api/admin/`` (the console). That
is not tidiness: this app is a **cache in front of published data**, and the
prefix is where that shows. Everything under ``/api/`` is a read of the mirror
and answers the same way whether or not anything has been embedded; everything
here answers out of an index that may be empty, stale, or rebuilt overnight —
and a client that can see the difference in the URL is a client that can decide
what to do when it degrades.

The version number is real, for the same reason. A payload here is a position
in a document that a viewer draws on top of a rendered page; changing the
geometry it carries breaks a deployed front end. A ``/api/v2/`` alongside is
how that change ships, and there is no such promise on ``/api/`` because that
one serves rows, not coordinates.
"""

from django.urls import path

from .views import (
    ChatStreamView,
    ChatView,
    ConversationDetailView,
    ConversationListView,
    IndexStatusView,
    SourceTextView,
    VectorSearchView,
)

# Produces, under /api/v1/:
#   search/vector/   POST, public, throttled  -> passages with their positions
#   search/source/   GET, public, throttled   -> the text those positions index
#   search/status/   GET, staff               -> the collection and the archive
#   chat/            POST, public, throttled  -> claims, each tied to a passage
#   chat/stream/     POST, public, throttled  -> the same answer as SSE stages
#   chat/conversations/       GET             -> this reader's saved threads
#   chat/conversations/<id>/  GET/PATCH/DELETE -> one thread and its turns
#
# The conversation routes sit under `chat/` rather than beside it because a
# thread is not a second resource the index serves — it is the shape the chat
# is read back in, and a client that can reach one can reach the other.
urlpatterns = [
    path("search/vector/", VectorSearchView.as_view(), name="vector-search"),
    path("search/source/", SourceTextView.as_view(), name="source-text"),
    path("search/status/", IndexStatusView.as_view(), name="index-status"),
    path("chat/", ChatView.as_view(), name="chat"),
    path("chat/stream/", ChatStreamView.as_view(), name="chat-stream"),
    path(
        "chat/conversations/",
        ConversationListView.as_view(),
        name="chat-conversations",
    ),
    path(
        "chat/conversations/<uuid:conversation_id>/",
        ConversationDetailView.as_view(),
        name="chat-conversation",
    ),
]
