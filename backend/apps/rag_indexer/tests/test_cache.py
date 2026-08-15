"""When an answer may be served twice, and when it may not.

The cache's value is obvious and its risk is not, so these tests are mostly
about the second: the conditions under which a stored answer is *withheld*.
A hit that should have been a miss is not a slow answer, it is a wrong one
delivered instantly and with citations attached.

Fakes rather than a live Qdrant. What is under test is this module's rules —
scope, expiry, what is refused — and none of them are the store's.
"""

from __future__ import annotations

import time

from django.conf import settings
from django.core.cache import cache as django_cache
from django.test import SimpleTestCase, override_settings

#: `RAG_ENABLED` is off by default (settings), and the cache is gated on it —
#: turning the semantic index off must turn its cache off too. So every test
#: here runs with it on, which is the deployment the cache exists for.
RAG_ON = {**settings.RAG, "ENABLED": True, "CACHE_ENABLED": True}

from apps.rag_indexer.services.cache import CACHE_VERSION, SemanticCache
from apps.rag_indexer.services.qdrant import QdrantUnavailable, SearchHit


def passage(notice_id: str = "OP-1", content: str = "Turnover of USD 22.4m.") -> SearchHit:
    return SearchHit(
        score=0.81,
        payload={
            "content": content,
            "notice_id": notice_id,
            "title": "Road works",
            "source_key": f"notice:{notice_id}",
            "position_id": "s0",
            "source_type": "text",
        },
    )


def record() -> SearchHit:
    """A row of our own tables — the source that is about *now*."""
    return SearchHit(
        score=1.0,
        retrieval="record",
        payload={
            "content": "22 tenders are open for bids.",
            "notice_id": "",
            "source_key": "record:open-tenders",
            "source_type": "record",
            "position_id": "",
        },
    )


class FakeStore:
    """A Qdrant that keeps points in a dict, or refuses to."""

    def __init__(self, *, fails: Exception | None = None):
        self.points: dict[str, tuple[list[float], dict]] = {}
        self.fails = fails
        self.swept: list[tuple[str, int]] = []
        self.created = 0

    def ensure_collection(self) -> bool:
        if self.fails:
            raise self.fails
        self.created += 1
        return True

    def upsert(self, points) -> int:
        if self.fails:
            raise self.fails
        for point_id, vector, payload in points:
            self.points[point_id] = (list(vector), dict(payload))
        return len(points)

    def build_filter(self, **conditions):
        return {key: value for key, value in conditions.items() if value}

    def search(self, vector, *, limit, query_filter=None, score_threshold=None):
        if self.fails:
            raise self.fails
        scope = (query_filter or {}).get("scope")
        for _point_id, (stored, payload) in self.points.items():
            if scope and payload.get("scope") != scope:
                continue
            score = _cosine(stored, list(vector))
            if score_threshold is not None and score < score_threshold:
                continue
            return [SearchHit(score=score, payload=payload)]
        return []

    def delete_older_than(self, key: str, cutoff: int) -> None:
        self.swept.append((key, cutoff))

    def drop_collection(self) -> None:
        self.points.clear()


def _cosine(left: list[float], right: list[float]) -> float:
    dot = sum(a * b for a, b in zip(left, right))
    norm = (sum(a * a for a in left) ** 0.5) * (sum(b * b for b in right) ** 0.5)
    return dot / norm if norm else 0.0


@override_settings(RAG=RAG_ON)
class CacheRules(SimpleTestCase):
    """What is stored, what is served, and what is refused."""

    def setUp(self) -> None:
        django_cache.clear()
        self.store = FakeStore()
        self.cache = SemanticCache(store=self.store)
        self.scope = SemanticCache.scope_key(language="uz")

    def test_the_same_question_is_answered_without_a_model_or_a_vector(self):
        """A literal repeat hits the exact tier, which needs no embedding."""
        self.cache.store(
            "Silva e-maili qanday?",
            scope=self.scope,
            vector=None,
            claims=[{"text": "Silva: silva@example.org", "sources": [0]}],
            sources=[passage()],
        )

        hit = self.cache.lookup("Silva e-maili qanday?", scope=self.scope)

        self.assertIsNotNone(hit)
        self.assertEqual(hit.tier, "exact")
        self.assertEqual(hit.claims[0]["sources"], [0])
        self.assertEqual(hit.sources[0].payload["notice_id"], "OP-1")

    def test_punctuation_and_case_do_not_make_it_a_different_question(self):
        self.cache.store(
            "Silva e-maili qanday?",
            scope=self.scope,
            vector=None,
            claims=[{"text": "silva@example.org", "sources": [0]}],
            sources=[passage()],
        )

        self.assertIsNotNone(
            self.cache.lookup("  silva e-maili qanday  ", scope=self.scope)
        )

    def test_a_paraphrase_hits_the_semantic_tier(self):
        """The brief's case: two shapes of one question, one stored answer."""
        self.cache.store(
            "Silva e-maili qanday?",
            scope=self.scope,
            vector=[1.0, 0.0, 0.0, 0.0],
            claims=[{"text": "silva@example.org", "sources": [0]}],
            sources=[passage()],
        )

        hit = self.cache.lookup(
            "Silvaning pochtasi nima?",
            scope=self.scope,
            # Near, but not the same words — which is exactly the case the
            # exact tier cannot serve and this one can.
            vector=[0.99, 0.14, 0.0, 0.0],
        )

        self.assertIsNotNone(hit)
        self.assertEqual(hit.tier, "semantic")
        self.assertGreaterEqual(hit.score, 0.92)
        self.assertEqual(hit.question, "Silva e-maili qanday?")

    def test_a_related_question_is_not_the_same_question(self):
        """Below the floor is a miss. Related is not equivalent."""
        self.cache.store(
            "Silva e-maili qanday?",
            scope=self.scope,
            vector=[1.0, 0.0, 0.0, 0.0],
            claims=[{"text": "silva@example.org", "sources": [0]}],
            sources=[passage()],
        )

        self.assertIsNone(
            self.cache.lookup(
                "Tender muddati qachon?",
                scope=self.scope,
                vector=[0.4, 0.9, 0.0, 0.0],
            )
        )

    def test_another_language_is_another_scope(self):
        """An Uzbek answer is not an answer to a Russian question."""
        self.cache.store(
            "Deadline?",
            scope=SemanticCache.scope_key(language="uz"),
            vector=[1.0, 0.0, 0.0, 0.0],
            claims=[{"text": "12-avgust", "sources": [0]}],
            sources=[passage()],
        )

        self.assertIsNone(
            self.cache.lookup(
                "Deadline?",
                scope=SemanticCache.scope_key(language="ru"),
                vector=[1.0, 0.0, 0.0, 0.0],
            )
        )

    def test_a_notice_scope_does_not_leak_into_another(self):
        stored = SemanticCache.scope_key(language="en", notice_id="OP-1")
        other = SemanticCache.scope_key(language="en", notice_id="OP-2")
        self.cache.store(
            "What is the deadline?",
            scope=stored,
            vector=[1.0, 0.0, 0.0, 0.0],
            claims=[{"text": "12 August", "sources": [0]}],
            sources=[passage()],
        )

        self.assertIsNone(
            self.cache.lookup(
                "What is the deadline?", scope=other, vector=[1.0, 0.0, 0.0, 0.0]
            )
        )

    def test_an_answer_about_now_is_never_stored(self):
        """A database record is true for a minute; a cache entry lives hours."""
        written = self.cache.store(
            "Qanday tenderlar ochiq?",
            scope=self.scope,
            vector=[1.0, 0.0, 0.0, 0.0],
            claims=[{"text": "22 ta tender ochiq.", "sources": [0]}],
            sources=[record()],
        )

        self.assertFalse(written)
        self.assertIsNone(self.cache.lookup("Qanday tenderlar ochiq?", scope=self.scope))

    def test_an_answer_with_no_claims_is_not_an_answer(self):
        self.assertFalse(
            self.cache.store(
                "Anything?",
                scope=self.scope,
                vector=[1.0, 0.0, 0.0, 0.0],
                claims=[],
                sources=[passage()],
            )
        )

    def test_an_expired_entry_is_a_miss_before_it_is_swept(self):
        """Age is checked on read, not only when the sweep gets round to it."""
        self.cache.store(
            "Deadline?",
            scope=self.scope,
            vector=[1.0, 0.0, 0.0, 0.0],
            claims=[{"text": "12 August", "sources": [0]}],
            sources=[passage()],
        )
        for _point_id, (_vector, payload) in self.store.points.items():
            payload["stored_at"] = int(time.time()) - self.cache.ttl - 60
        django_cache.clear()

        self.assertIsNone(
            self.cache.lookup("Deadline?", scope=self.scope, vector=[1.0, 0.0, 0.0, 0.0])
        )

    def test_an_entry_from_an_older_shape_is_a_miss(self):
        self.cache.store(
            "Deadline?",
            scope=self.scope,
            vector=[1.0, 0.0, 0.0, 0.0],
            claims=[{"text": "12 August", "sources": [0]}],
            sources=[passage()],
        )
        for _point_id, (_vector, payload) in self.store.points.items():
            payload["version"] = f"{CACHE_VERSION}-old"
        django_cache.clear()

        self.assertIsNone(
            self.cache.lookup("Deadline?", scope=self.scope, vector=[1.0, 0.0, 0.0, 0.0])
        )

    def test_a_dead_store_is_a_miss_and_not_an_error(self):
        cache = SemanticCache(store=FakeStore(fails=QdrantUnavailable("down")))
        django_cache.clear()

        self.assertIsNone(
            cache.lookup("Deadline?", scope=self.scope, vector=[1.0, 0.0, 0.0, 0.0])
        )
        # The write still lands on the exact tier: Redis is up even when
        # Qdrant is not, and a literal repeat is still worth answering free.
        self.assertTrue(
            cache.store(
                "Deadline?",
                scope=self.scope,
                vector=[1.0, 0.0, 0.0, 0.0],
                claims=[{"text": "12 August", "sources": [0]}],
                sources=[passage()],
            )
        )

    def test_a_stored_source_comes_back_as_the_hit_it_was(self):
        """Rehydrated, not left as a dict a client renders differently."""
        self.cache.store(
            "Deadline?",
            scope=self.scope,
            vector=None,
            claims=[{"text": "12 August", "sources": [0]}],
            sources=[passage(content="Bids close on 12 August 2026.")],
        )

        hit = self.cache.lookup("Deadline?", scope=self.scope)

        rendered = hit.sources[0].as_dict()
        self.assertEqual(rendered["content"], "Bids close on 12 August 2026.")
        self.assertEqual(rendered["notice_id"], "OP-1")
        self.assertEqual(rendered["payload"]["source_key"], "notice:OP-1")


class CacheSwitch(SimpleTestCase):
    """The cache does nothing at all when it is turned off."""

    def setUp(self) -> None:
        django_cache.clear()

    def test_disabled_means_no_read_and_no_write(self):
        with override_settings(RAG={**RAG_ON, "CACHE_ENABLED": False}):
            cache = SemanticCache(store=FakeStore())
            scope = SemanticCache.scope_key(language="en")

            self.assertFalse(
                cache.store(
                    "Deadline?",
                    scope=scope,
                    vector=[1.0, 0.0, 0.0, 0.0],
                    claims=[{"text": "12 August", "sources": [0]}],
                    sources=[passage()],
                )
            )
            self.assertIsNone(cache.lookup("Deadline?", scope=scope))
