"""The search request, validated before anything is spent on it.

A serializer rather than reading ``request.data`` in the view, for one reason
that is specific to this endpoint: a search costs an embedding call, and an
embedding call is metered. Every field that can be rejected locally — a blank
query, a 4 kB paste, a limit of ten thousand, a category slug that is not one
of ours — is rejected before the provider is touched. Validation here is a cost
control as much as a correctness one.

The vocabulary fields are validated **against the project's own choices**
rather than against a copy: ``TenderCategory`` and ``ConsultingSubcategory``
are imported, so adding a direction in ``apps/tenders/categories.py`` makes it
filterable here with no edit in this app. A hardcoded list would be right on
the day it was written and wrong at the next classifier change — the same
failure ``keywords.py`` and the CSV import page already avoid.
"""

from __future__ import annotations

from django.conf import settings
from rest_framework import serializers

from apps.tenders.categories import TenderCategory
from apps.tenders.subcategories import ConsultingSubcategory

from .chunks import PDF, TEXT
from .models import ChatConversation, ChatMessage


class VectorSearchSerializer(serializers.Serializer):
    """``POST /api/v1/search/vector/``."""

    query = serializers.CharField(
        # Bounded because it is embedded: a pasted document in the query box
        # would be truncated by the provider anyway, and silently. Rejecting it
        # tells the caller what happened.
        max_length=2000,
        trim_whitespace=True,
        allow_blank=False,
    )
    #: Scope to one tender. One id, not two: ``TenderNotice``'s primary key
    #: *is* the upstream notice id, so the string the public routes use and the
    #: key the console holds are the same value.
    notice_id = serializers.CharField(max_length=64, required=False, allow_blank=True)

    category = serializers.ChoiceField(
        choices=TenderCategory.choices, required=False, allow_blank=True
    )
    subcategory = serializers.ChoiceField(
        choices=ConsultingSubcategory.choices, required=False, allow_blank=True
    )
    source_type = serializers.ChoiceField(
        choices=[(PDF, "PDF"), (TEXT, "Text")], required=False, allow_blank=True
    )
    limit = serializers.IntegerField(required=False, min_value=1)

    def validate_limit(self, value: int) -> int:
        """Clamped rather than rejected above the ceiling.

        A caller asking for 200 results wants "as many as you have", and a 400
        would be a worse answer than 50. The ceiling exists because each result
        carries its chunk's text, so the response size is linear in it.
        """
        return min(value, settings.RAG["SEARCH_MAX_LIMIT"])


class ChatSerializer(serializers.Serializer):
    """``POST /api/v1/chat/``.

    Same validation discipline as the search request and for a sharper reason:
    a chat question costs an embedding call *and* a model call, so everything
    rejectable locally is rejected before either is spent. The question is
    bounded harder than a search query — a pasted document is not a question,
    and letting one through would put it in the model's context at the
    reader's expense.
    """

    question = serializers.CharField(
        max_length=1000, trim_whitespace=True, allow_blank=False
    )
    #: Scope the answer to one tender. The common case in the UI: the widget is
    #: opened from a notice and the reader means "this one".
    notice_id = serializers.CharField(max_length=64, required=False, allow_blank=True)
    category = serializers.ChoiceField(
        choices=TenderCategory.choices, required=False, allow_blank=True
    )
    #: The thread this question continues. Optional, and an id that does not
    #: resolve starts a new thread rather than failing — see
    #: `conversations.get_or_start`. The client never has to have one first.
    conversation_id = serializers.UUIDField(required=False, allow_null=True)

    def validate_question(self, value: str) -> str:
        # Two characters is not a question, and it retrieves the whole archive
        # equally badly. Rejected here rather than answered with noise.
        if len(value.strip()) < 3:
            raise serializers.ValidationError("Ask a longer question.")
        return value.strip()


class ConversationSerializer(serializers.ModelSerializer):
    """A row in the thread list. Cheap on purpose — the sidebar polls it."""

    message_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ChatConversation
        fields = ["id", "title", "notice_id", "message_count", "created_at", "updated_at"]
        read_only_fields = fields


class ConversationMessageSerializer(serializers.ModelSerializer):
    """One stored turn, in the same shape the live answer arrives in.

    The client renders history and a fresh answer with the same component, so
    the two shapes have to match: `claims` and `sources` are the live response's
    fields under their live names. A history that needed its own renderer is a
    history that drifts from the answer it is supposed to be a record of.
    """

    class Meta:
        model = ChatMessage
        fields = [
            "id", "role", "text", "claims", "sources", "retrieval",
            "degraded_reason", "unsupported", "took_ms", "prompt_version",
            "created_at",
        ]
        read_only_fields = fields


class ConversationRenameSerializer(serializers.Serializer):
    """The one thing a client may change about a thread."""

    title = serializers.CharField(
        max_length=ChatConversation.TITLE_LENGTH, trim_whitespace=True
    )
