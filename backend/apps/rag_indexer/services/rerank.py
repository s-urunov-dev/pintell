"""Reading the candidates again, more carefully, before spending the window.

Retrieval ranks a passage by the distance between two vectors that were
computed without ever seeing each other — the question was embedded in one
call, the passage in another, months apart. That is what makes it fast enough
to run over 74,000 chunks, and it is also its ceiling: nothing in the score
comes from the two texts being *compared*. A cross-encoder reads the pair
together and scores it, which is far too expensive for the corpus and exactly
affordable for twenty candidates.

**This module is a seam, and the default backend does nothing.** Three
backends are implemented — ``none``, ``cohere``, ``local`` — and ``none`` is
what ships. That is a decision rather than an unfinished job:

* ``local`` (``bge-reranker-v2-m3`` through ``sentence-transformers``) needs
  torch in the image. It is the option that keeps the rule the embeddings
  already keep — nothing leaves the deployment — and it costs about two
  gigabytes of container and a model load at first use.
* ``cohere`` needs an account, a key and a third party that this codebase has
  not asked the legal side about. What crosses the wire is the reader's
  question and passages of *published* World Bank notices, which is the same
  material `EmbeddingService` already sends to Gemini — but "same shape as an
  existing exception" is an argument, not an approval, and vendor profile text
  must never reach either.

Either is one environment variable away. Neither is switched on by a machine.

**What the reranker may and may not decide.** It reorders and it truncates.
It never edits a passage, never merges two, never scores a *claim*, and its
number reaches no verdict — the same line D28 draws around `importance`. A
passage the reranker drops is a passage the model is not shown; it is not a
passage that has been judged wrong.

**Truncation only happens when a reranker actually ran.** With ``none`` the
candidate list is returned unchanged, at the length D49 measured (sixteen
passages, because eight left a general question with three distinct notices to
answer from). Cutting to five on fused rank alone would take the brief's number
without the machinery that earns it.

**Never fatal.** A missing key, a dead endpoint, a model that will not load, a
response of the wrong shape: each is logged and the candidates are returned in
the order they arrived. A reranker that fails closed would turn a cost
optimisation into an outage.
"""

from __future__ import annotations

import logging
import threading
from typing import Any, Sequence

from django.conf import settings

from .qdrant import SearchHit

logger = logging.getLogger(__name__)

#: Characters of a passage sent to the reranker. A cross-encoder truncates its
#: input anyway; cutting here makes the truncation visible in one place and
#: keeps a pathological table from spending the request on its own.
DOCUMENT_MAX_CHARS = 2000

#: Backends this module knows about. An unrecognised value behaves as `none`
#: and says so once, rather than failing a deployment over a typo in an
#: environment variable.
BACKENDS = ("none", "cohere", "local")

COHERE_ENDPOINT = "https://api.cohere.com/v2/rerank"


class RerankUnavailable(RuntimeError):
    """The configured backend cannot score this batch.

    Caught by :meth:`RerankService.rerank` itself — it exists so the backends
    can fail in one declared way instead of each raising a client library's
    own exception family, whose names change between versions.
    """


class RerankService:
    """Score ``(question, passage)`` pairs together, and keep the best."""

    def __init__(self, backend: str | None = None):
        config = settings.RAG
        self.backend: str = (backend or config["RERANK_BACKEND"]).strip().lower()
        self.model: str = config["RERANK_MODEL"]
        self.timeout: int = config["RERANK_TIMEOUT"]
        self.top_n: int = config["RERANK_TOP_N"]
        self._model: Any = None
        self._lock = threading.Lock()
        if self.backend not in BACKENDS:
            logger.warning(
                "Unknown rerank backend %r — reranking is off. Expected one of %s.",
                self.backend,
                ", ".join(BACKENDS),
            )
            self.backend = "none"

    def enabled(self) -> bool:
        """Whether a second pass will actually run."""
        if self.backend == "none":
            return False
        if self.backend == "cohere":
            return bool(settings.RAG["RERANK_API_KEY"])
        return True

    def rerank(
        self, query: str, hits: Sequence[SearchHit], *, top_n: int | None = None
    ) -> list[SearchHit]:
        """The candidates, best first, cut to ``top_n`` — or unchanged.

        Returns the input list untouched when no backend is configured, when
        there is nothing to reorder, or when the backend failed. The caller
        cannot tell the last case from the first by the return value alone,
        which is deliberate: the answer is the same either way, and the failure
        is in the log where an operator reads it rather than in a response a
        reader would have to interpret.
        """
        candidates = list(hits)
        if not self.enabled() or len(candidates) < 2 or not (query or "").strip():
            return candidates

        documents = [_document_of(hit) for hit in candidates]
        try:
            scores = self._score(query, documents)
        except RerankUnavailable as exc:
            logger.info("Rerank unavailable (%s): %s", self.backend, exc)
            return candidates
        except Exception as exc:  # noqa: BLE001 - never fatal; see the module docstring
            logger.warning("Rerank failed (%s): %s", self.backend, exc)
            return candidates

        if len(scores) != len(candidates):
            # A backend that returned a different number of scores than it was
            # given cannot be zipped against the candidates: the mismatch would
            # attach every score after the gap to the wrong passage, and a
            # wrong score is still a number. The same contract `embed_documents`
            # holds, for the same reason.
            logger.warning(
                "Rerank returned %d scores for %d passages — keeping retrieval order.",
                len(scores),
                len(candidates),
            )
            return candidates

        ordered = sorted(
            zip(candidates, scores), key=lambda pair: pair[1], reverse=True
        )
        limit = top_n or self.top_n
        kept: list[SearchHit] = []
        for hit, score in ordered[:limit]:
            payload = dict(hit.payload)
            # Written onto the payload rather than into `score`, which still
            # means what the retrieval arm said it meant. Two numbers from two
            # different scales on one hit is exactly the confusion `search.py`
            # refuses — so they are two fields with two names.
            payload["rerank_score"] = round(float(score), 6)
            kept.append(
                SearchHit(score=hit.score, payload=payload, retrieval=hit.retrieval)
            )
        return kept

    # -- backends -----------------------------------------------------------
    def _score(self, query: str, documents: list[str]) -> list[float]:
        if self.backend == "cohere":
            return self._score_cohere(query, documents)
        if self.backend == "local":
            return self._score_local(query, documents)
        raise RerankUnavailable(f"No scorer for backend {self.backend!r}.")

    def _score_cohere(self, query: str, documents: list[str]) -> list[float]:
        """One HTTP call, scores returned in the order the documents were sent.

        The response numbers its results by index into the request, and this
        reassembles by that index rather than trusting arrival order — the API
        returns them ranked, so reading them positionally would silently invert
        the ordering it just computed.
        """
        import requests  # noqa: PLC0415 - already a dependency; imported per call site

        key = settings.RAG["RERANK_API_KEY"]
        if not key:
            raise RerankUnavailable("RAG_RERANK_API_KEY is not set.")

        try:
            response = requests.post(
                COHERE_ENDPOINT,
                headers={
                    "Authorization": f"Bearer {key}",
                    "Content-Type": "application/json",
                },
                json={
                    "model": self.model,
                    "query": query,
                    "documents": documents,
                    # Every candidate, because the caller decides how many to
                    # keep. Asking the vendor to truncate would move a product
                    # decision into a request parameter.
                    "top_n": len(documents),
                },
                timeout=self.timeout,
            )
            response.raise_for_status()
            body = response.json()
        except Exception as exc:  # noqa: BLE001
            raise RerankUnavailable(str(exc)) from exc

        scores = [0.0] * len(documents)
        for row in body.get("results", []):
            index = row.get("index")
            if isinstance(index, int) and 0 <= index < len(scores):
                scores[index] = float(row.get("relevance_score") or 0.0)
        return scores

    def _score_local(self, query: str, documents: list[str]) -> list[float]:
        """A cross-encoder in this process. Loaded once, on first use.

        Lazily because the load is seconds and hundreds of megabytes, and a
        deployment that never asks a question should never pay it. The import
        is inside the method for the same reason every optional dependency in
        this app is: a build without it degrades rather than failing to start.
        """
        model = self._local_model()
        pairs = [(query, document) for document in documents]
        return [float(score) for score in model.predict(pairs)]

    def _local_model(self) -> Any:
        if self._model is not None:
            return self._model
        with self._lock:
            if self._model is None:
                try:
                    from sentence_transformers import CrossEncoder  # noqa: PLC0415
                except ImportError as exc:  # pragma: no cover - packaging guard
                    raise RerankUnavailable(
                        "`sentence-transformers` is not installed in this build."
                    ) from exc
                try:
                    self._model = CrossEncoder(self.model, max_length=512)
                except Exception as exc:  # noqa: BLE001
                    raise RerankUnavailable(
                        f"Could not load {self.model}: {exc}"
                    ) from exc
                logger.info("Cross-encoder loaded (%s)", self.model)
        return self._model


def _document_of(hit: SearchHit) -> str:
    """What the reranker reads for one hit.

    The title travels with the passage because a chunk cut from the middle of a
    bidding document often does not name the thing it is about, and a
    cross-encoder scoring "the bidder shall confirm availability" against a
    question about roads has nothing to go on. It is a prefix for scoring only
    and never becomes part of the passage the reader is shown.
    """
    payload = hit.payload
    title = str(payload.get("title") or payload.get("notice_id") or "").strip()
    content = str(payload.get("content") or "").strip()
    text = f"{title}. {content}" if title else content
    return text[:DOCUMENT_MAX_CHARS]


_service: RerankService | None = None
_service_lock = threading.Lock()


def get_rerank_service() -> RerankService:
    """The process-wide service. Holds the local model when there is one."""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = RerankService()
    return _service


def reset_rerank_service() -> None:
    global _service
    with _service_lock:
        _service = None
