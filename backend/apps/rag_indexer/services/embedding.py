"""One place that turns text into vectors, and the retry policy around it.

Everything about this module is shaped by the size of the job. The archive is
tens of millions of characters, one pass is hours of network calls, and the
failure modes that matter are not exceptions but *quiet* ones: a batch that
came back short, a query embedded with the wrong task type, a run that half
succeeded. So the contract is deliberately strict — a batch returns exactly as
many vectors as it was given texts, of exactly the configured width, or it
raises.

**Documents and queries are embedded differently, and the difference is not
optional.** Gemini's embedding models take a ``task_type``: passages stored in
the index use ``RETRIEVAL_DOCUMENT`` and the question asked of them uses
``RETRIEVAL_QUERY``. The two produce vectors in the same space but placed for
asymmetric retrieval, and using one type for both costs recall silently — the
search still returns five results, they are just worse, and no error is ever
raised. That is why the two entry points here are separate methods rather than
one method with a flag defaulted to something.

**What is sent, and what is not.** The passages embedded here are World Bank
notice bodies and the borrower documents those notices link to: published
material, already mirrored, already served by the public API. Vendor profile
data is a different question with a legal answer attached (D43: profile
data does not leave the deployment), and it is not sent here — the query side
takes a search string typed into a search box, and ``SearchService`` is the
only caller. Anyone wiring a vendor's own profile text into a query is crossing
a line this docstring is the record of; the provider seam below is what makes
swapping in a local model at that point a configuration change rather than a
rewrite.

The provider is isolated behind :class:`EmbeddingService` for that reason and
one more: the vector width is a *collection* property in Qdrant, fixed at
creation. Changing model without changing width keeps the collection valid;
changing width means a new collection. Both numbers therefore come from the
same settings block and the mismatch is checked at start-up rather than
discovered as bad search results.
"""

from __future__ import annotations

import logging
import random
import threading
import time
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Iterable, Sequence

from django.conf import settings

logger = logging.getLogger(__name__)

#: Passages going into the index.
TASK_DOCUMENT = "RETRIEVAL_DOCUMENT"
#: The question asked of them.
TASK_QUERY = "RETRIEVAL_QUERY"


class EmbeddingUnavailable(RuntimeError):
    """No usable embedding provider, or it refused for good.

    Raised rather than returning empty vectors, so a deployment with no key is
    distinguishable from a source with no text. Every caller catches it: the
    archive command reports and stops, the search endpoint falls back to
    Postgres full-text search. Nothing propagates it to a user.
    """


class EmbeddingService:
    """Batched embeddings from Gemini, with backoff and a strict shape check.

    One instance per process is enough and one is what the module-level
    :func:`get_embedding_service` hands out — the SDK client is thread-safe and
    building it per call would re-read the key and re-open the connection pool
    on every batch of an hours-long run.
    """

    def __init__(self):
        config = settings.RAG
        self.model: str = config["EMBED_MODEL"]
        self.dimensions: int = config["VECTOR_SIZE"]
        #: Texts per request. Gemini caps a batch, and the cap is lower than
        #: the number of chunks a single bidding document produces — so this is
        #: a real bound on throughput, not a formality.
        self.batch_size: int = config["EMBED_BATCH"]
        #: Characters per text. Longer input is truncated by the provider
        #: anyway; cutting here makes the truncation visible in one place and
        #: keeps a pathological table cell from spending the whole batch's
        #: token budget.
        self.max_chars: int = config["EMBED_MAX_CHARS"]
        self.max_retries: int = config["EMBED_MAX_RETRIES"]
        self.backoff_base: float = config["EMBED_BACKOFF_BASE"]
        #: **Texts** per minute this service will not exceed. ``0`` is unpaced.
        #:
        #: Texts, not HTTP calls, and the difference is the whole reason this
        #: exists. The provider's quota metric is named
        #: ``EmbedContentRequestsPerMinute...`` and its batch endpoint calls
        #: each text in the batch a "request" — ``at most 100 requests can be
        #: in one batch``. So a hundred texts sent in *one* HTTP call spend the
        #: whole free-tier minute. Measured directly against the live endpoint:
        #: one call carrying 100 texts returns 200, and a second call carrying
        #: 10 texts one second later returns 429.
        #:
        #: A pacer that counted HTTP calls would therefore be exactly wrong in
        #: the direction that looks right — it was, and it let a flush of 1,125
        #: texts go out as twelve "well within 90 per minute" calls that
        #: exhausted a hundred-text minute eleven times over.
        self.texts_per_minute: int = config["EMBED_TEXTS_PER_MINUTE"]
        self._seconds_per_text = (
            60.0 / self.texts_per_minute if self.texts_per_minute > 0 else 0.0
        )
        self._next_allowed = 0.0
        self._pace_lock = threading.Lock()
        #: Requests in flight at once. ``1`` is sequential.
        #:
        #: Latency, not quota, was the binding constraint the moment the
        #: ceiling was lifted: one call carrying a hundred texts takes tens of
        #: seconds, and a sequential stream of them indexes about 75 chunks a
        #: minute whatever the plan allows — a day for this archive. The
        #: requests are independent and the provider is remote, so overlapping
        #: them is the whole fix.
        #:
        #: It stays at 1 by default because on a *paced* deployment it buys
        #: nothing: the pacer holds its lock across the sleep, so threads queue
        #: behind the same per-minute budget, which is the correct behaviour
        #: and not a useful one. Raise it together with lifting the pace.
        self.concurrency: int = max(config["EMBED_CONCURRENCY"], 1)
        self._client: Any = None
        self._lock = threading.Lock()

    # -- availability -------------------------------------------------------
    @staticmethod
    def enabled() -> bool:
        """Whether embedding can run at all in this deployment.

        Its own switch rather than a reuse of ``AI_ENABLED``: that flag gates
        Claude extraction, which is the product, and this is an index that can
        be rebuilt at any time. Turning the semantic index off must not turn a
        verdict off, and enabling extraction must not silently start spending
        an embedding quota — the same separation ``CLASSIFY_ENABLED`` exists
        for in ``settings.ANTHROPIC``.
        """
        config = settings.RAG
        return bool(config["ENABLED"] and config["EMBED_API_KEY"])

    def _get_client(self) -> Any:
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                if not self.enabled():
                    raise EmbeddingUnavailable(
                        "Embeddings are disabled — set RAG_ENABLED=true and "
                        "GEMINI_API_KEY (or RAG_EMBED_API_KEY)."
                    )
                try:
                    from google import genai
                    from google.genai import types
                except ImportError as exc:  # pragma: no cover - packaging guard
                    raise EmbeddingUnavailable(
                        "The `google-genai` package is not installed."
                    ) from exc

                self._client = genai.Client(
                    api_key=settings.RAG["EMBED_API_KEY"],
                    # The SDK counts milliseconds; every timeout setting in
                    # this project is in seconds.
                    http_options=types.HttpOptions(
                        timeout=settings.RAG["EMBED_TIMEOUT"] * 1000
                    ),
                )
                logger.info(
                    "Embedding client initialised (model=%s, dim=%s)",
                    self.model,
                    self.dimensions,
                )
        return self._client

    def reset(self) -> None:
        """Drop the cached client — used by tests and after a settings change."""
        with self._lock:
            self._client = None

    # -- public API ---------------------------------------------------------
    def embed_documents(self, texts: Sequence[str]) -> list[list[float]]:
        """Vectors for passages going into the index, in the order given.

        Order is part of the contract: the caller zips the result against the
        chunks it sent, and a provider that reordered or dropped one would
        attach every payload after it to the wrong vector. Nothing in the
        response identifies which text a vector belongs to, so the length check
        below is the only guard there is — which is why it raises rather than
        logging.
        """
        return self._embed_all(texts, TASK_DOCUMENT)

    def embed_query(self, text: str) -> list[float]:
        """The vector for one search string. See ``TASK_QUERY`` above."""
        vectors = self._embed_all([text], TASK_QUERY)
        return vectors[0]

    def batched(self, items: Sequence[Any], size: int | None = None) -> Iterable[list[Any]]:
        """Slice a sequence into request-sized batches.

        Exposed because the archive command batches *chunks* — it needs the
        payloads that travel beside the texts — and duplicating the arithmetic
        there is how the two drift apart when the batch cap changes.
        """
        step = size or self.batch_size
        for start in range(0, len(items), step):
            yield list(items[start : start + step])

    # -- internals ----------------------------------------------------------
    def _embed_all(self, texts: Sequence[str], task_type: str) -> list[list[float]]:
        """Every text, in the order given, however many requests that takes.

        Order survives concurrency because the batches are reassembled by
        index rather than by completion. That is not a detail: nothing in a
        response identifies which text a vector belongs to, so a result list
        built in finishing order would attach every payload to the wrong
        passage — and it would look completely normal, because a wrong vector
        is still a vector of the right width.
        """
        if not texts:
            return []

        prepared = [self._prepare(text) for text in texts]
        batches = list(self.batched(prepared))

        if self.concurrency > 1 and len(batches) > 1:
            with ThreadPoolExecutor(
                max_workers=min(self.concurrency, len(batches)),
                thread_name_prefix="embed",
            ) as pool:
                # `map` rather than `submit` + `as_completed`: it yields in
                # submission order, which is exactly the guarantee this needs,
                # and it re-raises the first failure like the sequential path.
                results = list(pool.map(lambda b: self._embed_batch(b, task_type), batches))
        else:
            results = [self._embed_batch(batch, task_type) for batch in batches]

        vectors: list[list[float]] = []
        for result in results:
            vectors.extend(result)

        if len(vectors) != len(texts):  # pragma: no cover - provider contract
            raise EmbeddingUnavailable(
                f"Asked for {len(texts)} embeddings and received {len(vectors)}."
            )
        return vectors

    def _wait_for_slot(self, texts: int) -> None:
        """Block until this process may send ``texts`` more texts.

        A monotonic next-allowed instant rather than a sliding window of
        timestamps: the window is the more accurate model and it is not worth
        its memory here, because the run is a single sequential producer and an
        evenly spaced stream is exactly what a per-minute quota wants. The lock
        is held across the sleep on purpose — two threads that computed their
        slot and then slept independently would both wake into the same one.

        The cost is charged for the whole batch *before* it goes out rather
        than after, so a call carrying a hundred texts waits for a hundred
        texts' worth of quota instead of discovering afterwards that it spent
        a minute it did not have.
        """
        if self._seconds_per_text <= 0:
            return
        with self._pace_lock:
            now = time.monotonic()
            if now < self._next_allowed:
                time.sleep(self._next_allowed - now)
            self._next_allowed = (
                max(now, self._next_allowed) + texts * self._seconds_per_text
            )

    def _prepare(self, text: str) -> str:
        """One text, bounded and never empty.

        An empty string is replaced rather than skipped: skipping would shorten
        the batch and break the positional contract above, and the provider
        rejects the empty string outright. A single space embeds to something
        meaningless, which is the correct outcome for a chunk that should not
        have reached here — and ``ExtractionService`` already drops those.
        """
        cleaned = (text or "").strip()
        if not cleaned:
            return " "
        return cleaned[: self.max_chars]

    def _embed_batch(self, batch: list[str], task_type: str) -> list[list[float]]:
        """One request, retried on the failures that are worth retrying.

        Retries are bounded, exponential and jittered. Jitter matters more than
        it looks: an archive run is a single process hammering one quota, and a
        bare doubling sends every retry of a rate-limited minute at the same
        instant — the second wave gets rate-limited for the same reason as the
        first, and the run walks its way to the retry ceiling without ever
        being unlucky.

        The exception type is not inspected. The SDK raises a family of them,
        their names have changed across versions, and the cost of retrying a
        genuinely fatal error is one extra call before the same failure — while
        the cost of *not* retrying a transient one is an abandoned run over an
        archive that takes hours. The message is logged each time so a bad key
        is visible immediately rather than after the ceiling.
        """
        from google.genai import types

        client = self._get_client()
        last_error: Exception | None = None

        for attempt in range(self.max_retries + 1):
            self._wait_for_slot(len(batch))
            try:
                response = client.models.embed_content(
                    model=self.model,
                    contents=batch,
                    config=types.EmbedContentConfig(
                        task_type=task_type,
                        # The collection's width and this are the same setting,
                        # read once. See the module docstring: a mismatch here
                        # is not an error at the provider, it is a rejected
                        # upsert at best and a silently useless index at worst.
                        output_dimensionality=self.dimensions,
                    ),
                )
            except Exception as exc:  # noqa: BLE001 - see the docstring
                last_error = exc
                if attempt >= self.max_retries:
                    break
                delay = self.backoff_base * (2**attempt) + random.uniform(0, 0.5)
                logger.warning(
                    "Embedding batch of %d failed (attempt %d/%d): %s — retrying in %.1fs",
                    len(batch), attempt + 1, self.max_retries + 1, exc, delay,
                )
                time.sleep(delay)
                continue

            vectors = [list(item.values or []) for item in (response.embeddings or [])]
            if len(vectors) != len(batch):
                raise EmbeddingUnavailable(
                    f"Batch of {len(batch)} returned {len(vectors)} embeddings."
                )
            for vector in vectors:
                if len(vector) != self.dimensions:
                    raise EmbeddingUnavailable(
                        f"Model {self.model} returned {len(vector)} dimensions, "
                        f"and the collection is {self.dimensions} wide."
                    )
            return vectors

        raise EmbeddingUnavailable(
            f"Embedding failed after {self.max_retries + 1} attempts: {last_error}"
        ) from last_error


_service: EmbeddingService | None = None
_service_lock = threading.Lock()


def get_embedding_service() -> EmbeddingService:
    """The process-wide service. See :class:`EmbeddingService`."""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = EmbeddingService()
    return _service


def reset_embedding_service() -> None:
    """Drop the cached service (tests, and after a settings override)."""
    global _service
    with _service_lock:
        _service = None
