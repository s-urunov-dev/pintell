"""A conversation that is kept, and what keeping it must not cost.

Saving the thread is what makes a follow-up possible, and it is also the point
where this product could quietly lose the property it is built on: a claim that
rests on something the model said three turns ago is a claim with no source
behind it. So these tests hold both halves at once — the thread continues, and
every answer in it is still grounded in the passages retrieved for *that*
question.

The rest is ownership. The chat is public, so a session key is all the identity
there is; an id from another browser must read as a thread that does not exist.
"""

from __future__ import annotations

import json
from types import SimpleNamespace
from unittest.mock import patch

from django.contrib.auth import get_user_model
from django.core.cache import cache
from django.test import TestCase, TransactionTestCase

from apps.rag_indexer import conversations
from apps.rag_indexer.models import ChatConversation, ChatMessage
from apps.rag_indexer.services.chat import ChatService
from apps.rag_indexer.services.qdrant import SearchHit
from apps.rag_indexer.services.search import SearchResponse


class FakeSearch:
    def __init__(self):
        self.queries: list[str] = []

    def search(self, question, **kwargs):
        self.queries.append(question)
        hit = SearchHit(
            score=0.8,
            payload={
                "content": "Bids must be submitted by 12 September 2026 at 15:00.",
                "notice_id": "OP0001",
                "title": "A tender",
                "source_key": "notice:OP0001",
                "source_type": "text",
                "position_id": "s0",
            },
        )
        return SearchResponse([hit], "vector", 5, "")


def fake_client(claims, capture=None):
    def create(**kwargs):
        if capture is not None:
            capture.append(kwargs)
        return SimpleNamespace(
            stop_reason="end_turn",
            content=[SimpleNamespace(type="text", text=json.dumps({"claims": claims}))],
        )

    return SimpleNamespace(messages=SimpleNamespace(create=create))


def service(claims, capture=None):
    chat = ChatService(search=FakeSearch(), client=fake_client(claims, capture))
    chat.enabled = staticmethod(lambda: True)
    return chat


class ChatCaseMixin:
    """Answers the endpoint with a fake model, and puts the real one back.

    Patched rather than assigned over: the module attribute is shared, and a
    test that leaves a fake behind is a test that breaks whichever one runs
    next.
    """

    def setUp(self):
        # The chat's throttle counts in the cache, and the cache outlives a
        # test. Left alone, the twentieth question in this file is answered
        # with a 429 that has nothing to do with what is being tested.
        cache.clear()

    def use_model(self, claims, capture=None):
        chat = service(claims, capture)
        patcher = patch("apps.rag_indexer.views.get_chat_service", lambda: chat)
        patcher.start()
        self.addCleanup(patcher.stop)
        return chat

    def post(self, question, conversation_id=None, path="/api/v1/chat/"):
        body = {"question": question}
        if conversation_id:
            body["conversation_id"] = str(conversation_id)
        return self.client.post(path, body, content_type="application/json")


class ChatCase(ChatCaseMixin, TestCase):
    """The ordinary case: one request, one connection, rolled back after."""


class StreamingChatCase(ChatCaseMixin, TransactionTestCase):
    """Streaming needs the slower base class, and the reason is the feature.

    The streaming view answers from a worker thread, and a thread opens its own
    database connection — so its writes are real commits that the enclosing
    test transaction cannot roll back. Under `TestCase` they survive into the
    next test and the counts drift. `TransactionTestCase` commits for real and
    truncates between tests, which is what testing a threaded view honestly
    costs.
    """


class AThreadIsKept(ChatCase):
    """The turn survives the request that produced it."""

    def ask(self, question, conversation_id=None, claims=None):
        self.use_model(
            claims or [{"text": "Bids close on 12 September 2026.", "sources": [0]}]
        )
        return self.post(question, conversation_id)

    def test_a_first_question_starts_a_thread_and_names_it(self):
        response = self.ask("What is the deadline for this tender?")

        self.assertEqual(response.status_code, 200)
        conversation = ChatConversation.objects.get()
        self.assertEqual(conversation.title, "What is the deadline for this tender?")
        self.assertEqual(str(conversation.pk), response.json()["conversation_id"])

    def test_both_sides_of_the_turn_are_stored(self):
        self.ask("What is the deadline for this tender?")
        roles = list(ChatMessage.objects.values_list("role", flat=True))

        self.assertEqual(roles, ["user", "assistant"])

    def test_an_answer_keeps_the_sources_it_was_built_from(self):
        """A stored claim whose passages are gone is the unverifiable statement
        this product refuses. The badge must open the same sentence tomorrow."""
        self.ask("What is the deadline for this tender?")
        stored = ChatMessage.objects.get(role="assistant")

        self.assertEqual(len(stored.sources), 1)
        self.assertEqual(stored.sources[0]["payload"]["notice_id"], "OP0001")
        self.assertEqual(stored.claims[0]["sources"], [0])

    def test_a_second_question_continues_the_same_thread(self):
        first = self.ask("What is the deadline for this tender?").json()
        self.ask("And what about the bid security?", first["conversation_id"])

        self.assertEqual(ChatConversation.objects.count(), 1)
        self.assertEqual(ChatMessage.objects.count(), 4)

    def test_an_id_that_does_not_resolve_starts_a_thread_rather_than_failing(self):
        """A stale tab is not a reason to lose somebody's question."""
        response = self.ask(
            "What is the deadline?", "00000000-0000-4000-8000-000000000000"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(ChatConversation.objects.count(), 1)


class HistoryIsContextAndNeverASource(ChatCase):
    """The thread tells the model what the question means. Nothing more."""

    def test_earlier_turns_are_sent_to_the_model(self):
        capture: list = []
        self.use_model(
            [{"text": "It closes on 12 September 2026.", "sources": [0]}], capture
        )

        first = self.post("What does OP0001 require?").json()
        self.post("And when does it close?", first["conversation_id"])

        roles = [message["role"] for message in capture[-1]["messages"]]
        self.assertEqual(roles, ["user", "assistant", "user"])

    def test_a_replayed_answer_carries_no_citation_numbers(self):
        """An index from turn one names a passage in turn one's list. Beside
        turn two's list it would name a different passage."""
        capture: list = []
        self.use_model(
            [{"text": "Turnover of USD 3,000,000 is required.", "sources": [0]}], capture
        )

        first = self.post("What turnover does OP0001 require?").json()
        self.post("And the deadline?", first["conversation_id"])

        replayed = capture[-1]["messages"][1]["content"]
        self.assertNotIn("[0]", replayed)
        self.assertIn("USD 3,000,000", replayed)

    def test_a_claim_still_cannot_cite_a_passage_it_was_not_shown(self):
        """The guarantee is unchanged by the conversation around it."""
        self.use_model([{"text": "Invented from an earlier turn.", "sources": [4]}])
        response = self.post("What did you say earlier?").json()

        self.assertEqual(response["claims"], [])
        self.assertEqual(response["unsupported"], 1)


class WhatIsSearchedFor(TestCase):
    """A fragment has no subject to match on until the thread gives it one."""

    def test_a_short_follow_up_is_searched_with_the_question_before_it(self):
        turns = [
            {"role": "user", "text": "What turnover do IT tenders require?"},
            {"role": "assistant", "text": "It varies by notice."},
        ]
        query = conversations.retrieval_query("And the deadline?", turns)

        self.assertEqual(query, "What turnover do IT tenders require? And the deadline?")

    def test_a_question_that_carries_its_own_subject_is_left_alone(self):
        """A new subject in an old thread must not be dragged back to the
        previous one."""
        turns = [{"role": "user", "text": "What turnover do IT tenders require?"}]
        question = "Which road construction tenders are open in Uzbekistan right now?"

        self.assertEqual(conversations.retrieval_query(question, turns), question)

    def test_the_first_question_of_a_thread_is_searched_as_asked(self):
        self.assertEqual(conversations.retrieval_query("Deadline?", []), "Deadline?")


class AThreadBelongsToWhoeverStartedIt(ChatCase):
    """The session is the whole of the identity, and it is checked every time."""

    def start_thread(self):
        self.use_model([{"text": "Bids close on 12 September 2026.", "sources": [0]}])
        return self.post("What is the deadline for this tender?").json()["conversation_id"]

    def test_a_reader_sees_their_own_threads(self):
        self.start_thread()
        response = self.client.get("/api/v1/chat/conversations/")

        self.assertEqual(response.status_code, 200)
        self.assertEqual(len(response.json()["results"]), 1)

    def test_another_browser_cannot_read_the_thread(self):
        conversation_id = self.start_thread()

        other = self.client_class()
        self.assertEqual(
            other.get(f"/api/v1/chat/conversations/{conversation_id}/").status_code, 404
        )
        self.assertEqual(len(other.get("/api/v1/chat/conversations/").json()["results"]), 0)

    def test_another_browser_cannot_delete_the_thread(self):
        conversation_id = self.start_thread()

        other = self.client_class()
        self.assertEqual(
            other.delete(f"/api/v1/chat/conversations/{conversation_id}/").status_code, 404
        )
        self.assertEqual(ChatConversation.objects.count(), 1)

    def test_reopening_a_thread_returns_its_turns_in_order(self):
        conversation_id = self.start_thread()
        response = self.client.get(f"/api/v1/chat/conversations/{conversation_id}/")

        messages = response.json()["messages"]
        self.assertEqual([message["role"] for message in messages], ["user", "assistant"])
        self.assertEqual(len(messages[1]["sources"]), 1)

    def test_a_thread_can_be_renamed_and_deleted_by_its_owner(self):
        conversation_id = self.start_thread()

        renamed = self.client.patch(
            f"/api/v1/chat/conversations/{conversation_id}/",
            {"title": "Deadlines"},
            content_type="application/json",
        )
        self.assertEqual(renamed.json()["title"], "Deadlines")

        self.client.delete(f"/api/v1/chat/conversations/{conversation_id}/")
        self.assertEqual(ChatConversation.objects.count(), 0)
        self.assertEqual(ChatMessage.objects.count(), 0)


class SigningInDoesNotEmptyTheSidebar(TestCase):
    """Django rotates the session key on login. The threads must follow."""

    def test_threads_started_before_signing_in_are_adopted(self):
        user = get_user_model().objects.create_user(
            username="vendor@example.com", email="vendor@example.com", password="pw-12345678"
        )
        conversation = ChatConversation.objects.create(session_key="old-key")

        request = SimpleNamespace(
            session=SimpleNamespace(session_key="new-key", save=lambda: None),
            user=user,
        )
        adopted = conversations.adopt_session(request, previous_key="old-key")

        conversation.refresh_from_db()
        self.assertEqual(adopted, 1)
        self.assertEqual(conversation.user_id, user.pk)


class TheStreamReportsWhatIsActuallyHappening(StreamingChatCase):
    """The stages a reader watches are the pipeline's, not a timer's."""

    def read(self, response) -> list[str]:
        body = b"".join(response.streaming_content).decode()
        return [line[len("event: ") :] for line in body.splitlines() if line.startswith("event: ")]

    def test_the_stream_ends_with_the_answer_it_was_narrating(self):
        self.use_model([{"text": "Bids close on 12 September 2026.", "sources": [0]}])
        response = self.post(
            "What is the deadline for this tender?", path="/api/v1/chat/stream/"
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(response["Content-Type"], "text/event-stream")
        events = self.read(response)
        self.assertEqual(events[-1], "answer")
        self.assertIn("stage", events)

    def test_a_streamed_turn_is_recorded_like_any_other(self):
        """A reader who closes the tab still comes back to a complete thread."""
        self.use_model([{"text": "Bids close on 12 September 2026.", "sources": [0]}])
        response = self.post("What is the deadline?", path="/api/v1/chat/stream/")
        b"".join(response.streaming_content)

        self.assertEqual(ChatMessage.objects.count(), 2)
        self.assertEqual(ChatConversation.objects.count(), 1)

    def test_a_streamed_thread_is_reachable_afterwards(self):
        """The session cookie has to be set before the body streams.

        Middleware runs when the view returns and the body is consumed after,
        so a session created inside the stream is created too late to be sent.
        The symptom is silent: the answer arrives, and the thread it was filed
        under belongs to a key the browser never received.
        """
        self.use_model([{"text": "Bids close on 12 September 2026.", "sources": [0]}])
        response = self.post("What is the deadline?", path="/api/v1/chat/stream/")
        b"".join(response.streaming_content)

        listed = self.client.get("/api/v1/chat/conversations/").json()["results"]
        self.assertEqual(len(listed), 1)


class ALongThreadIsReadFromItsEnd(ChatCase):
    """A conversation is opened at the bottom, so that is what is sent.

    Each assistant turn carries the passages it was written from, so a thread
    of two hundred turns is megabytes of stored sources. Sending all of it to
    render the last exchange is the kind of page that works in testing and
    times out on the one conversation somebody actually used.
    """

    def thread_of(self, turns: int):
        conversation = ChatConversation.objects.create(session_key="k")
        for index in range(turns):
            ChatMessage.objects.create(
                conversation=conversation, role="user", text=f"Question {index}"
            )
            ChatMessage.objects.create(
                conversation=conversation, role="assistant", text=f"Answer {index}"
            )
        return conversation

    def open(self, conversation, **params):
        # Ownership is the session, so the thread has to be claimed by this
        # client before it can be read.
        conversation.session_key = self.client.session.session_key or ""
        conversation.save(update_fields=["session_key"])
        query = "&".join(f"{key}={value}" for key, value in params.items())
        url = f"/api/v1/chat/conversations/{conversation.pk}/"
        return self.client.get(f"{url}?{query}" if query else url).json()

    def test_only_the_last_page_is_sent(self):
        conversation = self.thread_of(30)
        body = self.open(conversation)

        self.assertEqual(len(body["messages"]), 20)
        self.assertTrue(body["has_more"])
        self.assertEqual(body["messages"][-1]["text"], "Answer 29")

    def test_the_page_is_still_in_reading_order(self):
        body = self.open(self.thread_of(3))
        self.assertEqual(
            [message["text"] for message in body["messages"][:2]],
            ["Question 0", "Answer 0"],
        )

    def test_older_turns_are_fetched_by_cursor_not_offset(self):
        """An offset shifts when a turn arrives mid-scroll, and the page that
        shifts is the one being read. The cursor is a message id, so it does
        not."""
        conversation = self.thread_of(30)  # 60 messages
        first = self.open(conversation)
        older = self.open(conversation, before=first["oldest_id"])

        # The page before "Question 20" ends where it begins.
        self.assertEqual(first["messages"][0]["text"], "Question 20")
        self.assertEqual(older["messages"][-1]["text"], "Answer 19")
        self.assertEqual(len(older["messages"]), 20)
        self.assertTrue(older["has_more"])

    def test_a_short_thread_says_there_is_nothing_more(self):
        body = self.open(self.thread_of(2))
        self.assertFalse(body["has_more"])
        self.assertEqual(len(body["messages"]), 4)
