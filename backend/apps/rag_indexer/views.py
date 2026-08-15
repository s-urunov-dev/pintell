"""The endpoints of the semantic index: ask it, read its sources, check on it.

Both are deliberately thin. Everything that could be a decision — which
retrieval path answered, what a hit looks like, whether the store is reachable
— was decided in ``services/`` and is merely rendered here, because the search
response is consumed by two front ends and a viewer, and a shape defined in a
view is a shape that gets adjusted per caller.

**Search and source reads are public and throttled; status is staff-only.** The passages in the
index are published World Bank material and the public tender API already
serves the same text, so gating search would protect nothing. What needs
bounding is the *cost*: every search is an embedding call, so this endpoint
carries its own throttle scope rather than sharing the mirror's generous
anonymous rate. Status is a different question — point counts, a connection
string's health and an archive-progress figure describe the deployment, not the
data — so it sits behind the same staff check as the rest of the console.
"""

from __future__ import annotations

import json
import logging
import queue
import threading

from django.db import connection
from django.db.models import Count
from django.http import Http404, StreamingHttpResponse
from django.shortcuts import get_object_or_404
from rest_framework import status
from rest_framework.authentication import SessionAuthentication
from rest_framework.exceptions import ValidationError
from rest_framework.renderers import BaseRenderer, JSONRenderer
from rest_framework.response import Response
from rest_framework.views import APIView

from apps.core.i18n import resolve_language
from apps.tenders.models import HarvestedDocument, TenderNotice

# Reused, not restated: the console's staff test is one class and this endpoint
# is part of the console's surface even though it lives under another prefix. A
# second permission class saying the same thing is a second one to remember
# when the rule changes.
from apps.adminpanel.permissions import IsStaffUser

from .chunks import PDF
from .models import PIPELINE_VERSION, IndexedSource
from . import conversations
from .serializers import (
    ChatSerializer,
    ConversationMessageSerializer,
    ConversationRenameSerializer,
    ConversationSerializer,
    VectorSearchSerializer,
)
from .services import (
    ExtractionService,
    IndexingService,
    get_chat_service,
    get_qdrant_service,
    get_search_service,
)

logger = logging.getLogger(__name__)

#: Seconds of silence before the stream sends a comment line. A model call is
#: seconds of nothing to send, and an idle connection is what a proxy closes.
HEARTBEAT_SECONDS = 15

#: Turns returned when a thread is opened. Ten exchanges is more than a reader
#: re-reads at once, and each assistant turn carries its own passages — the
#: whole thread would be megabytes of stored sources to render the last one.
MESSAGE_PAGE = 20


class VectorSearchView(APIView):
    """``POST /api/v1/search/vector/`` — passages, with where they sit.

    POST rather than GET, though it reads nothing: the query is free text that
    routinely contains the punctuation a procurement question needs, and a body
    keeps it out of the URL, out of the access log and out of the browser
    history. Not cacheable as a result, which is fine — the expensive half is
    the embedding call and Qdrant answers in single-digit milliseconds.
    """

    authentication_classes: list = []
    permission_classes: list = []
    #: Its own scope. See the module docstring: the ``anon`` rate is sized for
    #: reads of a mirror, and this endpoint spends money per call.
    throttle_scope = "rag_search"

    def post(self, request):
        serializer = VectorSearchSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        response = get_search_service().search(
            data["query"],
            limit=data.get("limit"),
            notice_id=data.get("notice_id", ""),
            category=data.get("category", ""),
            subcategory=data.get("subcategory", ""),
            source_type=data.get("source_type", ""),
            # Both arms, fused by rank (D58). The search box is where a reader
            # types a tender reference, and a reference is the one thing the
            # dense arm structurally cannot find. `retrieval` says `hybrid`
            # when both produced results, so a client can still tell what
            # answered — the field never became less informative.
            hybrid=True,
        )
        return Response(response.as_dict(), status=status.HTTP_200_OK)


class ChatView(APIView):
    """``POST /api/v1/chat/`` — a question answered from retrieved passages.

    Public and throttled on the same scope as search, because it is search plus
    a model call: the same retrieval, the same index, the same fallback — and
    then one more metered request on top. If anything, it wants a *tighter*
    budget than the search box, which is why the two share a scope rather than
    the chat getting its own more generous one.

    The response is claims and sources, never a paragraph. A single block of
    prose would force the client to parse citations out of text, and the whole
    point of the schema is that it does not have to: each claim already knows
    which passages support it, and each passage already knows where it sits in
    which document.
    """

    # Session authentication, with no permission class: the endpoint stays
    # public, but a signed-in vendor is *recognised*, which is what lets their
    # threads follow them off this browser.
    authentication_classes = [SessionAuthentication]
    permission_classes: list = []
    throttle_scope = "rag_search"

    def post(self, request):
        serializer = ChatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        answer, conversation, stored = answer_in_conversation(request, data)
        payload = answer.as_dict()
        payload["conversation_id"] = str(conversation.pk)
        payload["message_id"] = stored.pk if stored is not None else None
        payload["title"] = conversation.title
        return Response(payload, status=status.HTTP_200_OK)


def answer_in_conversation(request, data, *, on_stage=None):
    """Answer one question inside its thread, and keep the turn.

    Shared by the plain endpoint and the streaming one so that the two cannot
    diverge on what a turn is: the same history goes to the model, the same
    answer is stored, and the same thread is touched. A streaming endpoint that
    quietly stored something else would be a second product.
    """
    conversation = conversations.get_or_start(
        request,
        conversation_id=str(data.get("conversation_id") or ""),
        notice_id=data.get("notice_id", ""),
    )
    turns = conversations.history(conversation)
    question = data["question"]

    answer = get_chat_service().ask(
        question,
        # The thread's own scope, so reopening a tender's conversation reopens
        # the tender with it. The request may still name one for a first
        # question asked from a notice page.
        notice_id=data.get("notice_id") or conversation.notice_id,
        category=data.get("category", ""),
        # The interface language, taken from the header every client in this
        # project already sends. It is the tiebreak, not the decision — the
        # prompt answers in the language of the question (D47).
        language=resolve_language(request),
        history=turns,
        query=conversations.retrieval_query(question, turns),
        on_stage=on_stage,
    )

    try:
        stored = conversations.record_turn(
            conversation, question=question, answer=answer
        )
    except Exception as exc:  # noqa: BLE001 - an answer is never lost to its record
        logger.warning("Could not record the chat turn: %s", exc)
        stored = None
    return answer, conversation, stored


class EventStreamRenderer(BaseRenderer):
    """Lets a client ask for `text/event-stream` and be taken seriously.

    Without this the streaming endpoint answers **406** to the one `Accept`
    header that describes what it actually returns: DRF negotiates a renderer
    before the view runs, finds only JSON, and refuses. The symptom is quiet
    and expensive — the browser fell back to the plain endpoint on every
    question, so the stages were never seen and the reader watched a generic
    spinner while the server was reporting exactly what it was doing.

    The success path never reaches a renderer (the view returns a
    `StreamingHttpResponse` of its own), so this exists to make negotiation
    succeed and to keep a *validation error* readable when it does: the body is
    still JSON, because a 400 the client cannot parse is no better than a 406.
    """

    media_type = "text/event-stream"
    format = "txt"
    charset = "utf-8"

    def render(self, data, accepted_media_type=None, renderer_context=None):
        return json.dumps(data, default=str).encode(self.charset)


class ChatStreamView(APIView):
    """``POST /api/v1/chat/stream/`` — the same answer, reported as it is built.

    The client shows a reader what is happening while a question is answered,
    and the events it shows are **the pipeline's own stages**, not a timer
    pretending to be one: `retrieving` is emitted before the embedding call,
    `reading` carries the number of passages actually selected, `writing` is
    emitted when the model call starts. An animation driven by real events
    stays honest when a stage is slow, and a fabricated one turns into a lie
    exactly when the reader would most like the truth.

    **Why a thread.** `ask` is one blocking call by design — it is the same
    method the plain endpoint uses, and forking the pipeline to make it a
    generator would mean two pipelines to keep in agreement. So it runs in a
    worker thread and posts its stages to a queue this view drains. The
    connection that thread opens is closed in its own `finally`; leaving it to
    the pool is how a streaming endpoint turns into an outage.

    **A reader who closes the tab still gets their answer.** The turn is
    recorded by the worker regardless of whether anyone is still reading the
    stream, so the thread they come back to is complete.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes: list = []
    throttle_scope = "rag_search"
    # JSON stays in the list so a client that asks for it — or asks for
    # anything — still gets a parseable error.
    renderer_classes = [EventStreamRenderer, JSONRenderer]

    def post(self, request):
        serializer = ChatSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        data = serializer.validated_data

        # **The session has to exist before the response object does.** Django
        # runs the middleware — including the one that sets the session cookie
        # — when the view returns, and only *then* consumes the body. A session
        # created while the body streams is created after the headers have been
        # written, so the browser never receives the cookie, and every thread
        # started this way is filed under a key its owner does not have. Found
        # by an end-to-end run: the stream answered, the sidebar stayed empty.
        conversations.session_key(request)

        response = StreamingHttpResponse(
            self._events(request, data), content_type="text/event-stream"
        )
        # Buffering a stream defeats it. nginx honours the second header; the
        # first keeps every intermediary from holding events back to fill a
        # block, which is what makes a "thinking" animation arrive all at once
        # at the end.
        response["Cache-Control"] = "no-cache, no-transform"
        response["X-Accel-Buffering"] = "no"
        return response

    def _events(self, request, data):
        events: queue.Queue = queue.Queue()
        outcome: dict = {}

        def work():
            try:
                answer, conversation, stored = answer_in_conversation(
                    request, data, on_stage=events.put
                )
                payload = answer.as_dict()
                payload["conversation_id"] = str(conversation.pk)
                payload["message_id"] = stored.pk if stored is not None else None
                payload["title"] = conversation.title
                outcome["answer"] = payload
            except Exception as exc:  # noqa: BLE001 - reported, never raised into a stream
                logger.warning("Streamed chat failed: %s", exc)
                outcome["error"] = str(exc)
            finally:
                # This thread opened its own database connection. Nothing else
                # will close it.
                connection.close()
                events.put(None)

        worker = threading.Thread(target=work, name="chat-stream", daemon=True)
        worker.start()

        while True:
            try:
                item = events.get(timeout=HEARTBEAT_SECONDS)
            except queue.Empty:
                # An SSE comment. Keeps proxies from closing a connection that
                # is waiting on a model, and is ignored by every client.
                yield ": keep-alive\n\n"
                continue
            if item is None:
                break
            # A finished claim is its own event. The client appends it as it
            # arrives, so a dozen sentences land one at a time rather than
            # after forty seconds of a spinner — and each one has already
            # passed the citation check the final answer passes.
            if "claim" in item:
                yield _sse("claim", {"index": item.get("index", 0), **item["claim"]})
            elif item.get("stage") == "sources":
                yield _sse("sources", {"sources": item["sources"]})
            elif item.get("stage") == "draft":
                # The sentence in progress. Explicitly *not* a claim: it has no
                # citations yet, and the client renders it as unfinished until
                # the checked version replaces it.
                yield _sse("draft", {"index": item.get("index", 0), "text": item["text"]})
            else:
                yield _sse("stage", item)

        worker.join(timeout=1)
        if "answer" in outcome:
            yield _sse("answer", outcome["answer"])
        else:
            yield _sse("error", {"reason": outcome.get("error", "unknown")})


def _sse(event: str, payload: dict) -> str:
    """One Server-Sent Event. Named, so a client switches on the name."""
    return f"event: {event}\ndata: {json.dumps(payload, default=str)}\n\n"


def _blocks_of(raw: str, flat: str) -> list[dict]:
    """Paragraph spans for a text source, or none when there is nothing to say.

    A document with no markup gets no blocks rather than invented ones: the
    parsers this corpus runs on do not preserve paragraphs reliably (`pypdf`
    frequently emits one word per line), and a guessed break is a line the
    source does not have.
    """
    from apps.compliance.viewer import _split_blocks  # noqa: PLC0415

    if not raw or not flat:
        return []
    try:
        return [
            {"tag": tag, "start": start, "end": end}
            for tag, start, end in _split_blocks(raw, flat)
        ]
    except Exception as exc:  # noqa: BLE001 - typography, never the passage
        logger.info("Could not split %s into blocks: %s", extraction.source.title, exc)
        return []


class ConversationListView(APIView):
    """``GET /api/v1/chat/conversations/`` — this reader's threads, newest first.

    No pagination and no search. The list is capped where it is written
    (`conversations.MAX_CONVERSATIONS`), so it is always a sidebar rather than
    a dataset, and a paginated sidebar is a sidebar nobody scrolls twice.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes: list = []
    throttle_scope = "rag_search"

    def get(self, request):
        rows = (
            conversations.owned_by(request)
            .annotate(message_count=Count("messages"))
            .order_by("-updated_at")
        )
        return Response(
            {"results": ConversationSerializer(rows, many=True).data},
            status=status.HTTP_200_OK,
        )


class ConversationDetailView(APIView):
    """``/api/v1/chat/conversations/<id>/`` — read, rename, or delete one thread.

    Every verb resolves the thread through `conversations.owned_by`, so an id
    belonging to somebody else is a 404 and not a read. That is the whole
    access control on this table, and it is deliberately the only one: the
    content is a reader's own questions about published notices.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes: list = []
    throttle_scope = "rag_search"

    def get_object(self, request, conversation_id):
        conversation = conversations.owned_by(request).filter(pk=conversation_id).first()
        if conversation is None:
            raise Http404
        return conversation

    def get(self, request, conversation_id):
        """The thread's most recent turns, and a cursor for the ones before.

        Paginated from the *end*, which is the opposite of how a list endpoint
        usually works and is right here: a conversation is read at its bottom.
        A thread of two hundred turns would otherwise send every stored source
        payload — megabytes of passages — to render the last exchange.

        `before` is a message id, not an offset. Offsets shift when a turn is
        added while the reader is scrolling back, and the page that shifts is
        the one they are looking at.
        """
        conversation = self.get_object(request, conversation_id)
        rows = conversation.messages.order_by("-created_at", "-id")

        before = (request.query_params.get("before") or "").strip()
        if before.isdigit():
            rows = rows.filter(id__lt=int(before))

        try:
            limit = min(max(int(request.query_params.get("limit", MESSAGE_PAGE)), 1), 100)
        except ValueError:
            limit = MESSAGE_PAGE

        # One extra row read, never sent: it is how the client is told there is
        # more without a second query counting what it would not display.
        window = list(rows[: limit + 1])
        has_more = len(window) > limit
        window = window[:limit]
        window.reverse()

        return Response(
            {
                "conversation": ConversationSerializer(conversation).data,
                "messages": ConversationMessageSerializer(window, many=True).data,
                "has_more": has_more,
                "oldest_id": window[0].pk if window else None,
            },
            status=status.HTTP_200_OK,
        )

    def patch(self, request, conversation_id):
        conversation = self.get_object(request, conversation_id)
        serializer = ConversationRenameSerializer(data=request.data)
        serializer.is_valid(raise_exception=True)
        conversation.title = serializer.validated_data["title"]
        conversation.save(update_fields=["title", "updated_at"])
        return Response(ConversationSerializer(conversation).data)

    def delete(self, request, conversation_id):
        self.get_object(request, conversation_id).delete()
        return Response(status=status.HTTP_204_NO_CONTENT)



class SourceTextView(APIView):
    """``GET /api/v1/search/source/?source_key=…`` — the text a hit indexes.

    Exists because ``char_start``/``char_end`` are offsets into the *canonical*
    form of a source, and canonicalisation happens in Python
    (``apps.compliance.text.canonical`` — entity unescaping, lookalike folding,
    whitespace collapse, block tags turned into full stops). A viewer that
    fetched the raw notice body and highlighted those offsets would be a few
    characters out at the first ``&nbsp;`` and further out with every one after
    it, drifting down the page exactly as the document got longer.

    So the offsets and the string they index are served by the same code that
    produced them, and the viewer renders what it is given. The alternative —
    reimplementing ``canonical`` in TypeScript — is the same class of mistake
    as reimplementing ``expressions.describe`` in the console, and would fail
    silently rather than loudly.

    PDF sources are not served here and do not need to be: a PDF citation is
    rendered from the file itself with the ``bbox`` on the hit, so there is no
    second copy of the text to keep in step.
    """

    authentication_classes: list = []
    permission_classes: list = []
    throttle_scope = "rag_search"

    def get(self, request):
        source_key = (request.query_params.get("source_key") or "").strip()
        kind, _, identifier = source_key.partition(":")
        if not identifier or kind not in {"notice", "document"}:
            raise ValidationError({"source_key": "Expected `notice:<id>` or `document:<id>`."})

        service = ExtractionService()
        if kind == "notice":
            notice = get_object_or_404(TenderNotice, pk=identifier)
            extraction = service.from_notice(notice)
            # The body as stored: HTML, and the only place the notice's own
            # paragraph boundaries still exist. `canonical` turns them into full
            # stops, which is right for quoting and wrong for reading.
            raw_markup = notice.notice_text_sanitized or ""
        else:
            document = get_object_or_404(HarvestedDocument, pk=identifier)
            notice = document.notices.first()
            if notice is None:
                raise Http404("This document is not linked to any notice in scope.")
            extraction = service.from_document(document, notice)
            # A mirrored document is extracted text, not markup: there are no
            # boundaries to find, so the pane renders it as one block.
            raw_markup = ""
            if extraction.source.source_type == PDF:
                # Rendered from the file, not from a string. Answered rather
                # than 404'd so the client can tell "wrong mode" from "gone".
                return Response(
                    {
                        "source_key": source_key,
                        "source_type": PDF,
                        "document_id": document.pk,
                        "title": extraction.source.title,
                        "text": "",
                    }
                )

        return Response(
            {
                "source_key": source_key,
                "source_type": extraction.source.source_type,
                "document_id": extraction.source.document_id,
                # Where the source's own paragraphs sit inside the canonical
                # text. The pane used to render one unbroken wall of characters
                # — every paragraph break in the borrower's notice had become a
                # full stop by the time `canonical` was done with it, and a
                # reader could not tell where one requirement ended and the next
                # began. These are **located**, not rebuilt: the compliance
                # viewer solved the same problem (D32) and its splitter is
                # reused rather than copied, so the offsets a highlight is drawn
                # at and the offsets a paragraph starts at are measured against
                # one ruler and cannot disagree.
                "blocks": _blocks_of(raw_markup, extraction.canonical_text),
                "title": extraction.source.title,
                "text": extraction.canonical_text,
            }
        )


class IndexStatusView(APIView):
    """``GET /api/v1/search/status/`` — the collection, and how full it is.

    Two halves that must not be conflated, and the endpoint keeps them in
    separate objects for that reason. ``collection`` is what Qdrant says about
    itself; ``archive`` is what Postgres says about how much of the mirror has
    been handed to it. They disagree during a run — the upserts are
    asynchronous — and they disagree permanently if somebody deletes the
    collection without clearing the bookkeeping table, which is precisely the
    state an operator needs to be able to see rather than have averaged away.
    """

    authentication_classes = [SessionAuthentication]
    permission_classes = [IsStaffUser]

    def get(self, request):
        store = get_qdrant_service()
        # `stats` reports a failed connection instead of raising, so a Qdrant
        # that is down renders as a disconnected badge rather than a 500 on
        # the console's dashboard.
        collection = store.stats()

        service = IndexingService(store=store)
        try:
            archive = service.pending_count(focus_only=False)
        except Exception as exc:  # noqa: BLE001 - the console must still render
            logger.warning("Could not count the archive: %s", exc)
            archive = {}

        latest = IndexedSource.objects.order_by("-indexed_at").first()
        return Response(
            {
                "enabled": service.embedding.enabled(),
                "collection_name": store.collection,
                "embed_model": service.embedding.model,
                "pipeline_version": PIPELINE_VERSION,
                "collection": collection.as_dict(),
                "archive": archive,
                "last_indexed_at": latest.indexed_at if latest else None,
                "chunks_recorded": _chunks_recorded(),
            }
        )


def _chunks_recorded() -> int:
    """Chunks this deployment believes it wrote.

    Beside Qdrant's own ``points`` in the console, and the pair is the point:
    equal means the import landed, ours-higher means upserts are still in
    flight or the collection was dropped, theirs-higher means points from an
    older pipeline version are still in there and nobody has swept them.
    """
    from django.db.models import Sum

    total = IndexedSource.objects.aggregate(total=Sum("chunk_count"))["total"]
    return int(total or 0)

