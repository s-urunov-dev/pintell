"""Who a conversation belongs to, what the model is told of it, and how a turn is kept.

Three questions that would otherwise be answered slightly differently in the
plain endpoint, the streaming endpoint and the list endpoint. They are answered
once here.

**Ownership is never taken from the client.** No endpoint accepts a
conversation id and trusts it: every lookup is filtered by the requesting
session (or the signed-in account), so an id from somebody else's thread is a
404 rather than a read. The chat is public, so the session is all the identity
there is — and that is enough, because the alternative is asking a judge to
register before they may ask a question.

**What history does and does not do.** Earlier turns are sent to the model so
that "and when does it close?" means something. They are *not* a source: the
answer schema still only accepts an index into the passages retrieved for the
current question, so a claim can never rest on something the model said before.
That is the same guarantee D44 shipped, stated for a longer conversation — a
prior answer is context for reading the question, never grounds for a new
statement.

**Prior answers are replayed without their citation numbers.** The numbers in
turn three point at turn three's source list; showing them again beside turn
five's list would invite the model to reuse an index that now means a different
passage. Nothing is gained by keeping them — the text is what the question
follows from.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db.models import Q, QuerySet

from .models import ChatConversation, ChatMessage

logger = logging.getLogger(__name__)

#: Turns of history sent to the model. Six is three exchanges — enough for a
#: pronoun to resolve and for the thread's subject to stay in view, and short
#: enough that the passages, not the conversation, remain the bulk of the
#: prompt. A longer window would also push the cache breakpoint around on every
#: turn for no gain: the passages differ per question anyway.
HISTORY_TURNS = 6

#: Characters of a replayed answer. A previous answer can be a dozen claims;
#: what the next question needs from it is the subject, not the whole reply.
HISTORY_ANSWER_CHARS = 600

#: Conversations one owner may hold. Beyond this the oldest are pruned on the
#: next write. A cap rather than unbounded growth because this table is written
#: by an anonymous public endpoint, and "keep everything forever" is a decision
#: nobody makes deliberately at the moment a crawler finds the route.
MAX_CONVERSATIONS = 100


def session_key(request) -> str:
    """The requesting browser's session, created if this is its first request.

    Django only persists a session once something is written to it, so an
    anonymous reader has no key until asked for one. Creating it here is what
    makes their first question the start of a thread rather than an orphan.
    """
    if not request.session.session_key:
        request.session.save()
    return request.session.session_key or ""


def owned_by(request) -> QuerySet[ChatConversation]:
    """Every conversation the requester may see.

    A signed-in vendor sees the account's threads **and** the ones started in
    this browser before they signed in — the second half is what makes signing
    in mid-conversation feel like signing in rather than starting over.
    """
    key = session_key(request)
    user = getattr(request, "user", None)
    # An empty key matches nothing rather than every anonymous conversation
    # ever written. A session that could not be created is a browser that
    # cannot hold a thread, not a licence to read other people's.
    mine = Q(session_key=key) if key else Q(pk__in=[])
    if user is not None and user.is_authenticated:
        return ChatConversation.objects.filter(Q(user=user) | mine).distinct()
    return ChatConversation.objects.filter(mine)


def adopt_session(request, *, previous_key: str = "") -> int:
    """Hand this browser's conversations to the account that just signed in.

    ``previous_key`` is the session key **from before** the login, and it is
    not an optimisation: Django rotates the session key on login to defeat
    fixation, so by the time this runs the browser's threads are filed under a
    key that no longer exists. Reading only the current key would adopt nothing
    and empty the sidebar at the moment the vendor signed in.

    Idempotent, and never fatal: a vendor whose threads did not move has lost a
    sidebar, and failing their login over it would be the worse trade.
    """
    user = getattr(request, "user", None)
    if user is None or not user.is_authenticated:
        return 0
    keys = {key for key in (previous_key, session_key(request)) if key}
    if not keys:
        return 0
    try:
        return ChatConversation.objects.filter(
            session_key__in=keys, user__isnull=True
        ).update(user=user)
    except Exception as exc:  # noqa: BLE001 - a sidebar, never a login
        logger.info("Could not adopt chat conversations: %s", exc)
        return 0


def get_or_start(
    request, *, conversation_id: str = "", notice_id: str = ""
) -> ChatConversation:
    """The thread this question belongs to — the named one, or a new one.

    An id that does not resolve **starts a new conversation** rather than
    raising. The alternative is losing a question to a stale tab or a cleared
    cookie, and the reader's question is worth more than the id they sent.
    """
    if conversation_id:
        existing = owned_by(request).filter(pk=conversation_id).first()
        if existing is not None:
            return existing

    user = getattr(request, "user", None)
    conversation = ChatConversation.objects.create(
        session_key=session_key(request),
        user=user if (user is not None and user.is_authenticated) else None,
        notice_id=notice_id,
    )
    _prune(request)
    return conversation


def _prune(request) -> None:
    """Drop the oldest threads once an owner is past the cap. Never fatal."""
    try:
        owned = owned_by(request).order_by("-updated_at")
        surplus = list(owned.values_list("pk", flat=True)[MAX_CONVERSATIONS:])
        if surplus:
            ChatConversation.objects.filter(pk__in=surplus).delete()
    except Exception as exc:  # noqa: BLE001
        logger.info("Could not prune conversations: %s", exc)


def history(conversation: ChatConversation) -> list[dict[str, str]]:
    """The last few turns, in the shape the model takes them.

    Read newest-first and reversed rather than sliced from the front: a long
    thread should cost one bounded query, not a full read of its own history.
    """
    rows = list(
        conversation.messages.order_by("-created_at", "-id")[:HISTORY_TURNS]
    )
    rows.reverse()

    turns: list[dict[str, str]] = []
    for message in rows:
        text = (message.text or "").strip()
        if not text:
            continue
        if message.role == ChatMessage.Role.ASSISTANT:
            text = text[:HISTORY_ANSWER_CHARS]
        turns.append({"role": message.role, "text": text})
    return turns


def retrieval_query(question: str, turns: list[dict[str, str]]) -> str:
    """What is embedded for this question, given what came before it.

    A follow-up is often unsearchable alone: "va uning muddati qachon?" has no
    subject to match on, and embedding it returns whatever paragraphs happen to
    discuss deadlines. Joining it to the previous question gives it one.

    **This is a heuristic, and the one heuristic in the chat path.** The
    alternative — a cheap model call rewriting the follow-up into a standalone
    question — is better and was not chosen yet, because it puts a second
    metered call and a second failure mode in front of every turn. The length
    test is the cheap approximation of it: a question short enough to be a
    fragment is treated as one, and a fully formed question is left alone so a
    new subject in an old thread is not dragged back to the previous one.
    """
    words = [word for word in question.split() if word]
    if len(words) > FOLLOW_UP_WORDS:
        return question
    previous = next(
        (turn["text"] for turn in reversed(turns) if turn["role"] == ChatMessage.Role.USER),
        "",
    )
    if not previous:
        return question
    return f"{previous} {question}"[:400]


#: Words up to which a question is treated as a fragment of the previous one.
#: Six covers "va uning muddati qachon?" and "yana shunga o'xshash tenderlar
#: bormi?" without catching a sentence that carries its own subject.
FOLLOW_UP_WORDS = 6


def title_from(question: str) -> str:
    """A sidebar row's worth of the first question, cut on a word boundary."""
    text = " ".join((question or "").split())
    if len(text) <= ChatConversation.TITLE_LENGTH:
        return text
    return text[: ChatConversation.TITLE_LENGTH - 1].rsplit(" ", 1)[0] + "…"


def record_turn(
    conversation: ChatConversation, *, question: str, answer: Any
) -> ChatMessage:
    """Persist the question and the answer, and name the thread if it is new.

    The answer is stored with its own sources rather than a reference to them —
    see `ChatMessage`. Returns the assistant message so a caller can hand back
    its id.
    """
    ChatMessage.objects.create(
        conversation=conversation, role=ChatMessage.Role.USER, text=question
    )
    # How the answer was produced, flattened out of the two dicts the pipeline
    # reports it in. `or {}` rather than a branch: an answer written before
    # routing existed, or one that never reached the model, simply has none —
    # and blank is the correct record of that, not a placeholder.
    route = answer.route or {}
    cache = answer.cache or {}
    stored = ChatMessage.objects.create(
        conversation=conversation,
        role=ChatMessage.Role.ASSISTANT,
        text=" ".join(claim["text"] for claim in answer.claims),
        claims=answer.claims,
        sources=[hit.as_dict() for hit in answer.sources],
        retrieval=answer.retrieval,
        degraded_reason=answer.degraded_reason,
        unsupported=answer.unsupported,
        took_ms=answer.took_ms,
        prompt_version=answer.as_dict()["prompt_version"],
        model=str(route.get("model") or "")[:64],
        route_tier=str(route.get("tier") or "")[:16],
        cache_tier=str(cache.get("tier") or "")[:16],
    )
    if not conversation.title:
        conversation.title = title_from(question)
        conversation.save(update_fields=["title", "updated_at"])
    else:
        conversation.touch()
    return stored
