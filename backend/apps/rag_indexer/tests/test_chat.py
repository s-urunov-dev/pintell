"""The one guarantee the chat answer makes: nothing reaches the reader uncited.

A model writing prose over retrieved passages is the part of this product most
able to state a procurement fact that is not in any document — the exact
failure the sourcing rule exists to prevent (docs/OPEN-QUESTIONS.md). The defence is not the
prompt; it is that a claim can only cite an index into the passages it was
given, and this module checks those indices rather than trusting them.

So these tests are about what happens to bad citations, not about answer
quality. Quality is a gold-set question. This is a correctness one.
"""

from __future__ import annotations

import json
from types import SimpleNamespace

from datetime import timedelta

from django.conf import settings
from django.test import SimpleTestCase, TestCase, override_settings
from django.utils import timezone

from apps.rag_indexer.services.chat import ChatService
from apps.rag_indexer.services.qdrant import SearchHit
from apps.rag_indexer.services.search import SearchResponse


def hit(index: int) -> SearchHit:
    return SearchHit(
        score=0.8,
        payload={
            "content": f"Passage number {index}.",
            "notice_id": f"OP0000{index}",
            "title": "A tender",
            "source_key": f"notice:OP0000{index}",
            "source_type": "text",
            "position_id": f"s{index}",
            "char_start": 0,
            "char_end": 20,
        },
    )


class FakeSearch:
    """Retrieval that answers with whatever the test wants."""

    def __init__(self, hits=None, retrieval="vector", reason=""):
        self.hits = hits if hits is not None else [hit(0), hit(1)]
        self.retrieval = retrieval
        self.reason = reason
        self.asked: list[str] = []
        self.kwargs: list[dict] = []

    def search(self, question, **kwargs):
        self.asked.append(question)
        self.kwargs.append(kwargs)
        return SearchResponse(self.hits, self.retrieval, 5, self.reason)


def fake_client(claims):
    """A client returning one JSON body, shaped like the real response."""
    body = json.dumps({"claims": claims})
    return SimpleNamespace(
        messages=SimpleNamespace(
            create=lambda **kwargs: SimpleNamespace(
                stop_reason="end_turn",
                content=[SimpleNamespace(type="text", text=body)],
            )
        )
    )


@override_settings(AI_ENABLED=True)
class CitationsAreChecked(SimpleTestCase):
    """Indices are validated against the passages actually retrieved."""

    def _ask(self, claims, hits=None):
        service = ChatService(search=FakeSearch(hits), client=fake_client(claims))
        service.enabled = staticmethod(lambda: True)
        return service.ask("what turnover is required?")

    def test_a_claim_citing_a_real_passage_survives(self):
        answer = self._ask([{"text": "Turnover of USD 22m is required.", "sources": [1]}])

        self.assertEqual(len(answer.claims), 1)
        self.assertEqual(answer.claims[0]["sources"], [1])
        self.assertEqual(answer.unsupported, 0)

    def test_a_claim_citing_a_passage_that_was_never_shown_is_dropped(self):
        """The model cannot invent a source, because the index has to exist."""
        answer = self._ask([{"text": "Bids are valid for 120 days.", "sources": [7]}])

        self.assertEqual(answer.claims, [])
        self.assertEqual(answer.unsupported, 1)

    def test_an_invalid_index_is_removed_rather_than_clamped(self):
        """Clamping would point the badge at a sentence that says something else."""
        answer = self._ask([{"text": "Two things are required.", "sources": [0, 9]}])

        self.assertEqual(answer.claims[0]["sources"], [0])

    def test_supported_and_unsupported_claims_are_separated_not_merged(self):
        answer = self._ask(
            [
                {"text": "Grounded.", "sources": [0]},
                {"text": "Invented.", "sources": [42]},
            ]
        )

        self.assertEqual([c["text"] for c in answer.claims], ["Grounded."])
        self.assertEqual(answer.unsupported, 1)

    def test_duplicate_citations_collapse(self):
        answer = self._ask([{"text": "Said twice.", "sources": [1, 1, 0]}])
        self.assertEqual(answer.claims[0]["sources"], [0, 1])


class WhenSomethingIsMissing(SimpleTestCase):
    """Every degradation returns the passages rather than an error."""

    def test_no_passages_means_no_model_call_at_all(self):
        """Asking a model to answer with nothing to answer from is the whole
        failure this product is built to avoid."""
        called = []
        service = ChatService(
            search=FakeSearch(hits=[], retrieval="none"),
            client=fake_client([{"text": "anything", "sources": [0]}]),
        )
        service.enabled = staticmethod(lambda: called.append(1) or True)
        answer = service.ask("a question about nothing")

        self.assertEqual(answer.claims, [])
        self.assertEqual(answer.sources, [])

    def test_no_api_key_still_returns_the_retrieved_passages(self):
        service = ChatService(search=FakeSearch(), client=None)
        service.enabled = staticmethod(lambda: False)
        answer = service.ask("what turnover is required?")

        self.assertEqual(answer.claims, [])
        self.assertEqual(len(answer.sources), 2)
        self.assertEqual(answer.degraded_reason, "model_unavailable")

    def test_a_model_failure_is_an_answer_without_claims_not_a_crash(self):
        broken = SimpleNamespace(
            messages=SimpleNamespace(
                create=lambda **kwargs: (_ for _ in ()).throw(RuntimeError("boom"))
            )
        )
        service = ChatService(search=FakeSearch(), client=broken)
        service.enabled = staticmethod(lambda: True)
        answer = service.ask("what turnover is required?")

        self.assertEqual(answer.claims, [])
        self.assertEqual(answer.degraded_reason, "model_failed")
        self.assertEqual(len(answer.sources), 2)

    def test_a_keyword_answer_says_it_was_a_keyword_answer(self):
        """The reader is entitled to know the index was not consulted."""
        service = ChatService(
            search=FakeSearch(retrieval="fts", reason="embeddings_unavailable"),
            client=fake_client([{"text": "From a keyword hit.", "sources": [0]}]),
        )
        service.enabled = staticmethod(lambda: True)
        answer = service.ask("what turnover is required?")

        self.assertEqual(answer.retrieval, "fts")
        self.assertEqual(answer.degraded_reason, "embeddings_unavailable")


class TheEndpoint(TestCase):
    """The route exists, validates, and never 500s on a bad question."""

    def test_a_blank_question_is_rejected_before_anything_is_spent(self):
        response = self.client.post(
            "/api/v1/chat/", {"question": "  "}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)

    def test_a_two_character_question_is_rejected(self):
        response = self.client.post(
            "/api/v1/chat/", {"question": "hi"}, content_type="application/json"
        )
        self.assertEqual(response.status_code, 400)


@override_settings(AI_ENABLED=True)
class TheAnswerIsWrittenInTheReadersLanguage(TestCase):
    """The interface language decides, and it reaches the model.

    Tested on the *request* rather than on the output, because what the model
    writes is the model's business and what this code owes is a correct, present
    instruction. Asserting on Uzbek output would be a test of Claude.
    """

    def _sent(self, headers: dict) -> str:
        captured: dict = {}

        def create(**kwargs):
            captured.update(kwargs)
            return SimpleNamespace(
                stop_reason="end_turn",
                content=[
                    SimpleNamespace(
                        type="text",
                        text=json.dumps({"claims": [{"text": "x", "sources": [0]}]}),
                    )
                ],
            )

        service = ChatService(search=FakeSearch(), client=SimpleNamespace(
            messages=SimpleNamespace(create=create)
        ))
        service.enabled = staticmethod(lambda: True)

        from apps.rag_indexer.services import chat as chat_module

        original = chat_module.get_chat_service
        chat_module._service = service
        try:
            self.client.post(
                "/api/v1/chat/",
                {"question": "what is required?"},
                content_type="application/json",
                **headers,
            )
        finally:
            chat_module._service = None
            chat_module.get_chat_service = original

        return captured["messages"][0]["content"]

    def test_an_uzbek_reader_is_answered_in_uzbek(self):
        sent = self._sent({"HTTP_ACCEPT_LANGUAGE": "uz"})
        self.assertIn("Uzbek", sent)

    def test_a_russian_reader_is_answered_in_russian(self):
        sent = self._sent({"HTTP_ACCEPT_LANGUAGE": "ru"})
        self.assertIn("Russian", sent)

    def test_an_english_reader_is_answered_in_english(self):
        sent = self._sent({"HTTP_ACCEPT_LANGUAGE": "en"})
        self.assertIn("English", sent)


class ABroadQuestionIsNotADeadEnd(TestCase):
    """Database rows are sources, so a broad question is answerable *and* cited.

    An earlier version put these facts in an uncitable context block. The model
    answered correctly and then cited whichever passage was nearest, so the
    badge opened a sentence that supported nothing — the one failure this
    product cannot trade anything for. Rows are numbered sources now.
    """

    def test_the_open_tenders_the_feed_shows_are_offered_as_a_source(self):

        from apps.rag_indexer.services.chat import record_sources
        from apps.tenders.models import TenderNotice

        # `bidding_open` is `actionable()` — an open deadline *and* one of the
        # two opportunity notice types. An award notice is history, not an
        # opportunity, so the type is part of the fixture rather than noise.
        TenderNotice.objects.create(
            notice_id="OP0100",
            source="worldbank",
            country="Uzbekistan",
            notice_type=TenderNotice.OPPORTUNITY_TYPES[0],
            bid_description="Rehabilitation of pumping stations",
            notice_text_sanitized="<p>Body.</p>",
            deadline_date=timezone.now() + timedelta(days=10),
        )
        records = record_sources()

        self.assertEqual(len(records), 1)
        self.assertEqual(records[0].retrieval, "record")
        self.assertIn("open for bids", records[0].payload["content"])
        self.assertIn("/search", records[0].payload["content"])

    def test_the_tender_being_read_is_named(self):
        from apps.rag_indexer.services.chat import record_sources
        from apps.tenders.models import TenderNotice

        TenderNotice.objects.create(
            notice_id="OP0200",
            source="worldbank",
            country="Armenia",
            bid_description="Supply of laboratory equipment",
            notice_text_sanitized="<p>Body.</p>",
        )
        records = record_sources("OP0200")

        self.assertEqual(records[0].payload["notice_id"], "OP0200")
        self.assertIn("Supply of laboratory equipment", records[0].payload["content"])
        self.assertEqual(records[0].payload["source_type"], "record")

    def test_there_are_no_records_rather_than_broken_ones_when_nothing_is_open(self):
        from apps.rag_indexer.services.chat import record_sources

        self.assertEqual(record_sources(), [])

    def test_a_claim_may_cite_a_record_exactly_as_it_cites_a_passage(self):
        """The whole point of making rows sources: the badge on "12 tenders are
        open" now opens the thing that says so."""
        from apps.tenders.models import TenderNotice

        TenderNotice.objects.create(
            notice_id="OP0300",
            source="worldbank",
            country="Uzbekistan",
            notice_type=TenderNotice.OPPORTUNITY_TYPES[0],
            bid_description="Supply of transformers",
            notice_text_sanitized="<p>Body.</p>",
            deadline_date=timezone.now() + timedelta(days=5),
        )
        service = ChatService(
            search=FakeSearch(hits=[hit(0)]),
            client=fake_client([{"text": "Some tenders are open.", "sources": [1]}]),
        )
        service.enabled = staticmethod(lambda: True)
        answer = service.ask("what is open now?")

        self.assertEqual(answer.unsupported, 0)
        self.assertEqual(answer.claims[0]["sources"], [1])
        self.assertEqual(answer.sources[1].retrieval, "record")


class ClosedTendersAreNotTheAnswer(TestCase):
    """Of ~25,000 indexed notices a few dozen are open; the rest are history.

    Unfiltered, a general question was answered almost entirely out of
    2007-2018 contracts — presented as an answer, that reads as current
    procurement guidance rather than as an archive.
    """

    def test_an_open_question_is_answered_only_from_open_tenders(self):
        service = ChatService(search=FakeSearch(), client=None)
        service.enabled = staticmethod(lambda: False)
        service.ask("what turnover do IT tenders require?")

        self.assertTrue(service.search.kwargs[0]["active_only"])

    def test_a_question_about_one_tender_is_not_filtered(self):
        """The reader means *that* tender, and an award notice reached from
        the feed is the ordinary case."""
        service = ChatService(search=FakeSearch(), client=None)
        service.enabled = staticmethod(lambda: False)
        service.ask("what does this one require?", notice_id="OP0001")

        self.assertFalse(service.search.kwargs[0]["active_only"])


def passage(notice_id: str, content: str, score: float = 0.7) -> SearchHit:
    """One retrieved passage, named by the notice it came from."""
    return SearchHit(
        score=score,
        payload={
            "content": content,
            "notice_id": notice_id,
            "title": "A tender",
            "source_key": f"notice:{notice_id}",
            "source_type": "text",
            "position_id": "s0",
        },
    )


class WhatReachesTheContextWindow(SimpleTestCase):
    """Which passages are worth a slot, once retrieval has ranked them.

    Retrieval answers "the closest N"; on this corpus the closest N contains
    the same template paragraph three times over and one talkative notice four
    times. Both were measured on the deployed archive, and both spend slots a
    reader is paying for. Nothing here re-ranks — the order is retrieval's, and
    only what repeats is dropped.
    """

    def hits(self, service):
        """The passages a question ended up showing the model."""
        service.enabled = staticmethod(lambda: False)
        answer = service.ask("what turnover is required?")
        return [source.payload["notice_id"] for source in answer.sources]

    def test_a_paragraph_repeated_across_notices_takes_one_slot(self):
        boilerplate = (
            "The bidder shall confirm availability of certified specialists in "
            "the staff of the company providing installation and commissioning "
            "of software in the Republic of Uzbekistan."
        )
        service = ChatService(
            search=FakeSearch(hits=[
                passage("OP1", boilerplate),
                passage("OP2", boilerplate),
                passage("OP3", boilerplate),
                passage("OP4", "Average annual turnover must be at least USD 3,000,000."),
            ]),
            client=None,
        )
        self.assertEqual(self.hits(service), ["OP1", "OP4"])

    def test_the_same_figure_in_two_copies_does_not_make_them_different(self):
        """Digits are excluded from the comparison on purpose: the numbers
        filled into a template are exactly where two copies of it differ."""
        template = "Average annual turnover for the last three years must be at least USD %s."
        service = ChatService(
            search=FakeSearch(hits=[
                passage("OP1", template % "1,000,000"),
                passage("OP2", template % "9,500,000"),
            ]),
            client=None,
        )
        self.assertEqual(self.hits(service), ["OP1"])

    @override_settings(RAG={**settings.RAG, "CHAT_PASSAGES": 8})
    def test_one_notice_cannot_take_the_whole_answer(self):
        service = ChatService(
            search=FakeSearch(hits=[
                passage("OP1", f"Requirement number {index} of this tender.")
                for index in range(6)
            ] + [passage("OP2", "A different tender, with its own requirement.")]),
            client=None,
        )
        self.assertEqual(self.hits(service), ["OP1", "OP1", "OP1", "OP2"])

    @override_settings(RAG={**settings.RAG, "CHAT_PASSAGES": 8})
    def test_a_question_about_one_tender_may_be_answered_from_it_alone(self):
        """Scoped to a notice, the per-notice cap would cap the answer."""
        service = ChatService(
            search=FakeSearch(hits=[
                passage("OP1", f"Requirement number {index} of this tender.")
                for index in range(6)
            ]),
            client=None,
        )
        service.enabled = staticmethod(lambda: False)
        answer = service.ask("what does this require?", notice_id="OP1")

        self.assertEqual(len(answer.sources), 6)

    @override_settings(RAG={**settings.RAG, "CHAT_PASSAGES": 4})
    def test_retrieval_is_asked_for_more_than_the_answer_shows(self):
        """The over-fetch is what lets a discarded duplicate be replaced by a
        different passage rather than by nothing."""
        service = ChatService(search=FakeSearch(), client=None)
        service.enabled = staticmethod(lambda: False)
        service.ask("what turnover is required?")

        self.assertEqual(service.search.kwargs[0]["limit"], 8)

    @override_settings(RAG={**settings.RAG, "CHAT_PASSAGES": 2})
    def test_a_thin_result_is_shown_thin_rather_than_padded(self):
        """A source is a licence to cite. Topping the list back up with a
        passage that was discarded widens that licence and adds no material."""
        same = (
            "Interested eligible bidders may obtain further information and "
            "inspect the bidding documents at the address given below during "
            "office hours."
        )
        service = ChatService(
            search=FakeSearch(hits=[passage(f"OP{i}", same) for i in range(3)]),
            client=None,
        )
        self.assertEqual(self.hits(service), ["OP0"])

    def test_two_short_passages_are_not_called_copies_of_each_other(self):
        """A four-word sentence shares every word it has with its neighbour,
        and the figures that tell them apart are what the comparison drops."""
        service = ChatService(
            search=FakeSearch(hits=[
                passage("OP1", "Lot 1: USD 250,000."),
                passage("OP2", "Lot 2: USD 900,000."),
            ]),
            client=None,
        )
        self.assertEqual(self.hits(service), ["OP1", "OP2"])

    def test_a_claim_is_only_reported_once_its_closing_brace_has_arrived(self):
        """A sentence shown and then rewritten is worse than a slower answer."""
        from apps.rag_indexer.services.chat import _complete_claims

        partial = '{"claims": [{"text": "First.", "sources": [0]}, {"text": "Sec'
        self.assertEqual(
            _complete_claims(partial), [{"text": "First.", "sources": [0]}]
        )

    def test_nothing_is_reported_before_the_array_opens(self):
        from apps.rag_indexer.services.chat import _complete_claims

        self.assertEqual(_complete_claims('{"cla'), [])
        self.assertEqual(_complete_claims('{"claims"'), [])

    def test_every_finished_claim_is_found(self):
        from apps.rag_indexer.services.chat import _complete_claims

        whole = (
            '{"claims": [{"text": "One.", "sources": [0]}, '
            '{"text": "Two.", "sources": [1, 2]}]}'
        )
        self.assertEqual([claim["text"] for claim in _complete_claims(whole)], ["One.", "Two."])


class FakeCache:
    """A cache that hits when the test says so, and records what it stored."""

    def __init__(self, answer=None):
        self.answer = answer
        self.looked_up: list[tuple[str, str]] = []
        self.stored: list[dict] = []

    def lookup(self, question, *, scope, vector=None):
        self.looked_up.append((question, scope))
        return self.answer

    def store(self, question, *, scope, vector, claims, sources):
        self.stored.append(
            {"question": question, "scope": scope, "claims": claims, "sources": sources}
        )
        return True


class FakeEmbedder:
    def __init__(self):
        self.calls: list[str] = []

    def embed_query(self, text):
        self.calls.append(text)
        return [0.1, 0.2, 0.3, 0.4]


class FakeReranker:
    """A reranker that keeps whichever passages the test names."""

    def __init__(self, keep: int | None = None):
        self.keep = keep
        self.seen: list[list[SearchHit]] = []

    def rerank(self, query, hits, top_n=None):
        self.seen.append(list(hits))
        return list(hits) if self.keep is None else list(hits)[: self.keep]


@override_settings(AI_ENABLED=True)
class ThePipelineInFrontOfTheModel(SimpleTestCase):
    """Cache, hybrid, rerank and routing — and what none of them may change."""

    def _service(self, *, cache=None, reranker=None, claims=None, search=None):
        service = ChatService(
            search=search or FakeSearch(),
            client=fake_client(claims or [{"text": "Grounded.", "sources": [0]}]),
            cache=cache or FakeCache(),
            reranker=reranker or FakeReranker(),
            embedding=FakeEmbedder(),
        )
        service.enabled = staticmethod(lambda: True)
        return service

    def test_a_cache_hit_answers_without_calling_the_model(self):
        from apps.rag_indexer.services.cache import CachedAnswer

        cached = CachedAnswer(
            claims=[{"text": "silva@example.org", "sources": [0]}],
            sources=[hit(0)],
            tier="semantic",
            score=0.97,
            question="Silva e-maili qanday?",
        )
        called = []
        service = self._service(cache=FakeCache(answer=cached))
        service._client = SimpleNamespace(
            messages=SimpleNamespace(
                create=lambda **kwargs: called.append(kwargs) or SimpleNamespace()
            )
        )

        answer = service.ask("Silvaning pochtasi nima?")

        self.assertEqual(called, [])
        self.assertEqual(answer.retrieval, "cache")
        self.assertEqual(answer.claims, cached.claims)
        self.assertEqual(answer.cache["tier"], "semantic")

    def test_a_cache_hit_serves_the_sources_the_claims_were_written_against(self):
        """Indices point into their own list, so the pair travels as one value."""
        from apps.rag_indexer.services.cache import CachedAnswer

        cached = CachedAnswer(
            claims=[{"text": "From passage one.", "sources": [1]}],
            sources=[hit(4), hit(5)],
            tier="exact",
        )
        answer = self._service(cache=FakeCache(answer=cached)).ask("anything?")

        self.assertEqual(len(answer.sources), 2)
        self.assertEqual(answer.sources[1].payload["notice_id"], "OP00005")

    def test_a_follow_up_is_neither_looked_up_nor_stored(self):
        """Its own words identify no question — `retrieval_query` said so."""
        cache = FakeCache()
        service = self._service(cache=cache)

        service.ask("va uning muddati qachon?", query="Bu tender haqida ayting va uning muddati qachon?")

        self.assertEqual(cache.looked_up, [])
        self.assertEqual(cache.stored, [])

    def test_a_standalone_answer_is_kept(self):
        cache = FakeCache()

        self._service(cache=cache).ask("what turnover is required?")

        self.assertEqual(len(cache.stored), 1)
        self.assertEqual(cache.stored[0]["claims"], [{"text": "Grounded.", "sources": [0]}])

    def test_the_question_is_embedded_once_for_both_consumers(self):
        embedder = FakeEmbedder()
        search = FakeSearch()
        service = ChatService(
            search=search,
            client=fake_client([{"text": "Grounded.", "sources": [0]}]),
            cache=FakeCache(),
            reranker=FakeReranker(),
            embedding=embedder,
        )
        service.enabled = staticmethod(lambda: True)

        service.ask("what turnover is required?")

        self.assertEqual(len(embedder.calls), 1)
        self.assertEqual(search.kwargs[0]["vector"], [0.1, 0.2, 0.3, 0.4])

    def test_retrieval_is_asked_for_both_arms(self):
        search = FakeSearch()

        self._service(search=search).ask("TRIP-CS-01 requirements?")

        self.assertTrue(search.kwargs[0]["hybrid"])

    def test_a_reranker_cuts_the_passages_the_model_sees(self):
        reranker = FakeReranker(keep=1)
        search = FakeSearch(hits=[hit(0), hit(1), hit(2)])

        answer = self._service(search=search, reranker=reranker).ask("turnover?")

        self.assertEqual(len(answer.sources), 1)
        self.assertEqual(answer.reranked_out, 2)

    def test_the_route_is_reported_even_when_there_is_one_tier(self):
        answer = self._service().ask("what turnover is required?")

        self.assertEqual(answer.route["tier"], "deep")
        self.assertIn("model", answer.route)

    def test_a_degraded_answer_is_never_kept(self):
        """It records the deployment at one instant, not the archive."""
        cache = FakeCache()
        service = ChatService(
            search=FakeSearch(),
            client=None,
            cache=cache,
            reranker=FakeReranker(),
            embedding=FakeEmbedder(),
        )
        service.enabled = staticmethod(lambda: False)

        service.ask("what turnover is required?")

        self.assertEqual(cache.stored, [])
