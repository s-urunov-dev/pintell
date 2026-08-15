"""Answering a question that has already been answered, without asking again.

Two readers ask "Silva'ning pochtasi nima?" and "Silva e-maili qanday?" an hour
apart. Those are the same question in two shapes: retrieval returns the same
passages, the model writes the same sentences, and the second reader waits the
same forty seconds for them. This module returns the first answer to the second
reader — and the whole design is about the narrow conditions under which that
is honest rather than merely fast.

**A cache entry is never a source.** It lives in its own Qdrant collection, its
vectors are *questions* rather than passages, and nothing in it can be returned
by ``SearchService``. The alternative — one collection, a payload flag — is one
missing filter away from a chat answer citing a previous chat answer, which in a
product whose entire claim is that every sentence traces to a published document
is the one failure not worth trading anything for. Dropping this collection
costs the hit rate and nothing else.

**A hit serves the stored claims with the stored sources, together.** A claim's
citations are indices into *its own* source list (D44), so serving yesterday's
claims beside today's passages would renumber every badge. The pair travels as
one value or not at all.

**What is deliberately not cached.**

* **An answer that cites a database record.** ``record_sources`` reads open
  tenders *now*: "22 ta tender ochiq" is true for as long as it is true. Those
  answers are the ones a reader is most likely to repeat and the ones a cache
  would most embarrassingly get wrong.
* **A follow-up.** "va uning muddati qachon?" means the tender the reader was
  just discussing; its own text matches every deadline question in the archive.
  ``conversations.retrieval_query`` already detects that case, and this module
  is told about it rather than guessing again.
* **A degraded answer.** No model, no passages, a truncated body — these are
  states of the deployment at one instant, and storing one would serve the
  outage for six hours after it ended.

**Scope is part of the key, not a filter applied afterwards.** The same words
asked from a notice page and from the search page are two questions, and an
answer written in Uzbek is not an answer to a Russian question. Language,
``notice_id`` and ``category`` are matched exactly inside the store, so a
near-miss on the vector cannot cross a scope boundary.

**The threshold is conservative, and that was measured rather than assumed.**
Probed against the live archive with ``gemini-embedding-001`` at 768
dimensions, storing "Silva e-maili qanday?": "Silva e-mail manzili qanday?"
scores 0.9721, "Silvaning elektron pochtasi qanday?" 0.8691, "Silvaning
pochtasi nima?" 0.7727, an unrelated question 0.5267. At 0.92 the first hits
and the other two do not — so this serves near-identical rewordings and not
looser Uzbek synonymy. That is the safe direction (a miss costs one model call;
a false hit costs a wrong answer delivered instantly with citations attached),
it is the brief's own number, and it is a setting rather than a constant
because moving it belongs to the gold set. See D57 for the table.

**Two tiers, and only one of them is fast.** The exact tier is a hash of the
normalised question in Django's cache (Redis in this deployment): no embedding
call, no network beyond Redis, and it is the only path that answers in the
tens of milliseconds the brief asks for. The semantic tier still pays one
embedding call — a few hundred milliseconds — and saves the model call, which
is the expensive half. Anyone quoting "<50ms" for a paraphrase is quoting the
wrong tier.
"""

from __future__ import annotations

import hashlib
import json
import logging
import re
import threading
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Sequence

from django.conf import settings
from django.core.cache import cache as django_cache

from .qdrant import QdrantService, QdrantUnavailable, SearchHit

logger = logging.getLogger(__name__)

#: Bumped when the stored shape changes. An old entry then simply misses
#: rather than being deserialised into a shape the reader no longer renders.
CACHE_VERSION = "v1"

#: Namespace for the point id, so the same question in the same scope
#: overwrites its entry instead of accumulating near-duplicates.
CACHE_NAMESPACE = uuid.UUID("6f1d4d2e-9a44-5c7b-8e35-9a4f2c1b7d10")

#: Writes between sweeps of expired entries. A sweep is one filtered delete, so
#: this is not about its cost — it is about not issuing one on every question
#: when the answer to "is anything expired" is almost always no.
SWEEP_EVERY = 50

_WHITESPACE = re.compile(r"\s+")
#: Punctuation folded away before hashing, so "Silva e-maili qanday?" and
#: "silva e-maili qanday" are one key. Deliberately not applied to the
#: *semantic* tier's text, which is embedded as the reader wrote it.
_PUNCTUATION = re.compile(r"[^\w\s]", re.UNICODE)


@dataclass
class CachedAnswer:
    """A stored answer, and what it took to decide it was the right one."""

    claims: list[dict[str, Any]] = field(default_factory=list)
    #: Rehydrated as ``SearchHit`` rather than left as the dicts they were
    #: stored as, so a cached answer is the same object a fresh one is. The
    #: alternative — a second shape that renders "almost" the same — is how a
    #: citation badge ends up opening nothing on exactly the cheap path nobody
    #: clicks through while testing.
    sources: list[SearchHit] = field(default_factory=list)
    #: ``exact`` or ``semantic``. Reported on the response so a hit rate can be
    #: measured per tier rather than as one number that hides which worked.
    tier: str = "exact"
    #: Cosine similarity of the question that was asked to the question that
    #: was stored. 1.0 on the exact tier, and it means what it says there too.
    score: float = 1.0
    #: The stored question, so a reader can be told what was matched. A cache
    #: that answers a paraphrase silently is a cache nobody can audit.
    question: str = ""
    age_seconds: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "tier": self.tier,
            "score": round(self.score, 6),
            "question": self.question,
            "age_seconds": self.age_seconds,
        }


class SemanticCache:
    """Question → answer, keyed by meaning within one scope."""

    def __init__(self, store: QdrantService | None = None):
        config = settings.RAG
        self.collection: str = config["CACHE_COLLECTION"]
        self.threshold: float = config["CACHE_THRESHOLD"]
        self.ttl: int = config["CACHE_TTL_SECONDS"]
        self.max_entries: int = config["CACHE_MAX_ENTRIES"]
        # Its own store instance, pointed at its own collection, with only the
        # payload keys this module filters on indexed. Reusing the archive's
        # service would mean one object whose `collection` attribute decides
        # whether a search reads passages or answers.
        self._store = store or QdrantService(
            collection=self.collection,
            payload_indexes=(
                ("scope", "keyword"),
                ("stored_at", "integer"),
            ),
        )
        self._ready = False
        self._writes = 0
        self._lock = threading.Lock()

    # -- availability -------------------------------------------------------
    @staticmethod
    def enabled() -> bool:
        """Whether the cache may answer at all.

        Its own switch. The cache is an optimisation over a pipeline that
        works without it, so a deployment that distrusts it turns it off
        without touching retrieval, the model, or the index.
        """
        config = settings.RAG
        return bool(config["ENABLED"] and config["CACHE_ENABLED"])

    # -- keys ---------------------------------------------------------------
    @staticmethod
    def scope_key(*, language: str, notice_id: str = "", category: str = "") -> str:
        """The bucket a question belongs to. Matched exactly; never fuzzily.

        One string rather than three payload fields because it is only ever
        compared for equality — and a single keyword index is one filter
        condition instead of three, on a collection that is read on the
        critical path of every question.
        """
        return f"{language or 'en'}|{notice_id or '-'}|{category or '-'}"

    @classmethod
    def _normalised(cls, question: str) -> str:
        """The question as the exact tier keys it: folded, unpunctuated, tight."""
        text = _PUNCTUATION.sub(" ", (question or "").casefold())
        return _WHITESPACE.sub(" ", text).strip()

    def _exact_key(self, question: str, scope: str) -> str:
        digest = hashlib.sha256(
            f"{scope}\x00{self._normalised(question)}".encode("utf-8")
        ).hexdigest()
        return f"rag:chatcache:{CACHE_VERSION}:{digest}"

    def _point_id(self, question: str, scope: str) -> str:
        return str(
            uuid.uuid5(CACHE_NAMESPACE, f"{scope}\x00{self._normalised(question)}")
        )

    # -- reading ------------------------------------------------------------
    def lookup(
        self, question: str, *, scope: str, vector: Sequence[float] | None = None
    ) -> CachedAnswer | None:
        """The stored answer to this question, if there is an honest one.

        Never raises. Every failure here — Redis down, Qdrant down, a payload
        this version cannot read — is a miss, and a miss costs a model call
        rather than an answer.

        ``vector`` is passed in rather than computed: the caller embeds the
        question once and uses the same vector for this lookup and, on a miss,
        for retrieval. Embedding it twice would double the one cost the cache
        cannot avoid.
        """
        if not self.enabled() or not (question or "").strip():
            return None

        hit = self._exact_lookup(question, scope)
        if hit is not None:
            return hit
        if vector is None:
            return None
        return self._semantic_lookup(question, scope, vector)

    def _exact_lookup(self, question: str, scope: str) -> CachedAnswer | None:
        try:
            stored = django_cache.get(self._exact_key(question, scope))
        except Exception as exc:  # noqa: BLE001 - a cache is never fatal
            logger.info("Exact cache read failed: %s", exc)
            return None
        if not isinstance(stored, dict):
            return None
        return self._answer_from(stored, tier="exact", score=1.0)

    def _semantic_lookup(
        self, question: str, scope: str, vector: Sequence[float]
    ) -> CachedAnswer | None:
        try:
            hits = self._store.search(
                vector,
                limit=1,
                query_filter=self._store.build_filter(scope=scope),
                # Applied by Qdrant rather than here, so a near-miss never
                # costs the payload read. The floor is the whole decision:
                # below it the two questions are related, not equivalent.
                score_threshold=self.threshold,
            )
        except QdrantUnavailable as exc:
            logger.info("Semantic cache unavailable: %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001
            logger.info("Semantic cache read failed: %s", exc)
            return None

        if not hits:
            return None
        return self._answer_from(hits[0].payload, tier="semantic", score=hits[0].score)

    def _answer_from(
        self, payload: dict[str, Any], *, tier: str, score: float
    ) -> CachedAnswer | None:
        """One stored payload, or ``None`` if it is expired or unreadable.

        The age check runs here rather than only in the sweep: an expired entry
        that has not been swept yet is still in the collection and would still
        be the nearest neighbour, and "we meant to delete it" is not an answer
        a reader can check.
        """
        if payload.get("version") != CACHE_VERSION:
            return None
        stored_at = int(payload.get("stored_at") or 0)
        age = int(time.time()) - stored_at
        if stored_at <= 0 or age > self.ttl:
            return None

        claims = _loads(payload.get("claims"))
        sources = [_hit_from(row) for row in _loads(payload.get("sources"))]
        if not claims or not sources:
            # A hit that would render as an empty answer is worse than a miss:
            # the reader gets nothing *and* the pipeline was skipped.
            return None
        return CachedAnswer(
            claims=claims,
            sources=sources,
            tier=tier,
            score=float(score),
            question=str(payload.get("question") or ""),
            age_seconds=max(age, 0),
        )

    # -- writing ------------------------------------------------------------
    def store(
        self,
        question: str,
        *,
        scope: str,
        vector: Sequence[float] | None,
        claims: list[dict[str, Any]],
        sources: Sequence[SearchHit],
    ) -> bool:
        """Keep this answer for the next reader who means the same thing.

        Returns whether anything was written. Never raises: a cache that
        cannot be written is a cache that misses next time, and the reader
        whose question paid for this answer already has it.

        The exact tier is written even when the vector is missing — a
        deployment with no embedding key still answers a literally repeated
        question for free.
        """
        if not self.enabled() or not claims or not sources:
            return False
        if not _cacheable(sources):
            return False

        payload = {
            "version": CACHE_VERSION,
            "scope": scope,
            "question": (question or "").strip()[:600],
            "claims": json.dumps(claims, ensure_ascii=False),
            "sources": json.dumps(
                [_row_of(hit) for hit in sources], ensure_ascii=False
            ),
            "stored_at": int(time.time()),
        }

        written = False
        try:
            django_cache.set(self._exact_key(question, scope), payload, self.ttl)
            written = True
        except Exception as exc:  # noqa: BLE001
            logger.info("Exact cache write failed: %s", exc)

        if vector is None:
            return written

        try:
            self._ensure_collection()
            self._store.upsert([(self._point_id(question, scope), list(vector), payload)])
            written = True
        except QdrantUnavailable as exc:
            logger.info("Semantic cache write skipped: %s", exc)
        except Exception as exc:  # noqa: BLE001
            logger.info("Semantic cache write failed: %s", exc)

        self._maybe_sweep()
        return written

    def _ensure_collection(self) -> None:
        """Create the cache collection on first write. Idempotent and cheap.

        Lazily rather than at start-up, and never on the read path: a
        deployment that never answers a question should not create a
        collection, and a *lookup* that created one would turn a dead Qdrant
        into a write on the critical path of every miss.
        """
        if self._ready:
            return
        with self._lock:
            if not self._ready:
                self._store.ensure_collection()
                self._ready = True

    def _maybe_sweep(self) -> None:
        """Drop expired entries every so often. Never fatal, never blocking."""
        with self._lock:
            self._writes += 1
            due = self._writes % SWEEP_EVERY == 0
        if not due:
            return
        try:
            self._store.delete_older_than("stored_at", int(time.time()) - self.ttl)
        except Exception as exc:  # noqa: BLE001
            logger.info("Cache sweep failed: %s", exc)

    def clear(self) -> None:
        """Forget everything. For an operator who changed the prompt."""
        try:
            self._store.drop_collection()
        except Exception as exc:  # noqa: BLE001
            logger.info("Could not drop the cache collection: %s", exc)
        self._ready = False


def _row_of(hit: SearchHit) -> dict[str, Any]:
    """One source, stored whole.

    The payload rather than ``as_dict()``: that method is the *API's* shape and
    it lifts three keys out of the payload for the front end's convenience.
    Storing it would mean rebuilding a payload from a projection of itself, and
    a key added to the projection later would silently start round-tripping
    differently from one that was not.
    """
    return {
        "score": hit.score,
        "retrieval": hit.retrieval,
        "payload": dict(hit.payload),
    }


def _hit_from(row: dict[str, Any]) -> SearchHit:
    """A stored row, back as the hit it was."""
    payload = row.get("payload")
    return SearchHit(
        score=float(row.get("score") or 0.0),
        payload=dict(payload) if isinstance(payload, dict) else {},
        # Stamped so a reader is told the passage came out of a cached answer
        # rather than a search run for their question. It is the same passage
        # either way; what differs is when it was chosen.
        retrieval=str(row.get("retrieval") or "cache"),
    )


def _cacheable(sources: Sequence[SearchHit]) -> bool:
    """Whether this answer is about the archive rather than about right now.

    A ``record`` source is a row of our own tables read at the moment the
    question was asked — how many tenders are open, which closes soonest. It is
    the correct answer for a minute and a plausible-looking wrong one after
    that, so an answer resting on one is never stored. See the module
    docstring: this is the failure a TTL alone does not prevent, because the
    entry does not become wrong gradually.
    """
    return not any(
        hit.retrieval == "record" or hit.payload.get("source_type") == "record"
        for hit in sources
    )


def _loads(raw: Any) -> list[dict[str, Any]]:
    """A stored JSON list, or an empty one. A bad payload is a miss."""
    if isinstance(raw, list):
        return [item for item in raw if isinstance(item, dict)]
    if not isinstance(raw, str) or not raw:
        return []
    try:
        value = json.loads(raw)
    except json.JSONDecodeError:
        return []
    if not isinstance(value, list):
        return []
    return [item for item in value if isinstance(item, dict)]


_service: SemanticCache | None = None
_service_lock = threading.Lock()


def get_semantic_cache() -> SemanticCache:
    """The process-wide cache. Stateless apart from its store client."""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = SemanticCache()
    return _service


def reset_semantic_cache() -> None:
    global _service
    with _service_lock:
        _service = None
