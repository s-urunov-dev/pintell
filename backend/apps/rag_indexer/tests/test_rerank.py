"""What the reranker does, and — mostly — what it does when it cannot.

The default backend is ``none``, so the behaviour that actually ships is
"return the candidates unchanged". These tests pin that, pin that a configured
backend reorders and truncates, and pin the two failure shapes that would
otherwise corrupt an answer silently: a backend that raises, and one that
returns a different number of scores than it was given.
"""

from __future__ import annotations

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from apps.rag_indexer.services.qdrant import SearchHit
from apps.rag_indexer.services.rerank import RerankService, RerankUnavailable


def hit(key: str, content: str = "text") -> SearchHit:
    return SearchHit(
        score=0.5,
        payload={"source_key": key, "position_id": "s0", "content": content, "title": ""},
    )


class FakeBackend(RerankService):
    """A reranker whose scores — or failure — the test chooses."""

    def __init__(self, scores=None, fails: Exception | None = None, **kwargs):
        with override_settings(
            RAG={**settings.RAG, "RERANK_BACKEND": "cohere", "RERANK_API_KEY": "k"}
        ):
            super().__init__(**kwargs)
        self.scores = scores or []
        self.fails = fails
        self.seen: list[str] = []

    def enabled(self) -> bool:
        return True

    def _score(self, query, documents):
        self.seen = list(documents)
        if self.fails:
            raise self.fails
        return self.scores


class Reranking(SimpleTestCase):
    def test_the_default_backend_returns_the_candidates_untouched(self):
        """Nothing is truncated when nothing reranked — D49's sixteen stand."""
        with override_settings(RAG={**settings.RAG, "RERANK_BACKEND": "none"}):
            service = RerankService()
            candidates = [hit(str(index)) for index in range(16)]

            self.assertEqual(service.rerank("turnover?", candidates), candidates)

    def test_a_configured_backend_reorders_and_cuts(self):
        service = FakeBackend(scores=[0.1, 0.9, 0.4])

        kept = service.rerank("turnover?", [hit("a"), hit("b"), hit("c")], top_n=2)

        self.assertEqual([h.payload["source_key"] for h in kept], ["b", "c"])
        self.assertEqual(kept[0].payload["rerank_score"], 0.9)

    def test_the_retrieval_score_is_not_overwritten(self):
        """Two numbers from two scales are two fields, never one."""
        service = FakeBackend(scores=[0.9, 0.1])

        kept = service.rerank("turnover?", [hit("a"), hit("b")], top_n=1)

        self.assertEqual(kept[0].score, 0.5)
        self.assertEqual(kept[0].payload["rerank_score"], 0.9)

    def test_a_failing_backend_costs_the_ordering_and_not_the_answer(self):
        service = FakeBackend(fails=RerankUnavailable("no key"))
        candidates = [hit("a"), hit("b")]

        self.assertEqual(service.rerank("turnover?", candidates), candidates)

    def test_a_short_score_list_is_refused_rather_than_zipped(self):
        """Scores that do not line up would attach every one to the wrong passage."""
        service = FakeBackend(scores=[0.9])
        candidates = [hit("a"), hit("b"), hit("c")]

        self.assertEqual(service.rerank("turnover?", candidates), candidates)

    def test_the_title_travels_with_the_passage_for_scoring_only(self):
        service = FakeBackend(scores=[0.9, 0.1])
        titled = SearchHit(
            score=0.5,
            payload={
                "source_key": "a",
                "position_id": "s0",
                "content": "The bidder shall confirm availability.",
                "title": "Road works in Samarkand",
            },
        )

        kept = service.rerank("roads?", [titled, hit("b")], top_n=1)

        self.assertIn("Road works in Samarkand", service.seen[0])
        # …and never into what the reader is shown.
        self.assertEqual(
            kept[0].payload["content"], "The bidder shall confirm availability."
        )

    def test_an_unknown_backend_name_is_off_rather_than_broken(self):
        with override_settings(RAG={**settings.RAG, "RERANK_BACKEND": "bge-magic"}):
            service = RerankService()

            self.assertEqual(service.backend, "none")
            self.assertFalse(service.enabled())

    def test_cohere_without_a_key_is_not_enabled(self):
        with override_settings(
            RAG={**settings.RAG, "RERANK_BACKEND": "cohere", "RERANK_API_KEY": ""}
        ):
            self.assertFalse(RerankService().enabled())
