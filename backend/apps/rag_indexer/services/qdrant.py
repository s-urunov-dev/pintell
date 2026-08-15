"""The vector store, and the only place that knows it is Qdrant.

Everything above this module speaks in chunks, payloads and filter keys; the
collection name, the distance metric and the client's exception types stop
here. That seam is worth the indirection for one concrete reason: the store is
a *cache*. Every vector in it can be rebuilt from the mirror by re-running the
archive command, so losing the container costs time and no data — and a
component that can be thrown away should not have its API spread through four
other modules.

**A missing or broken Qdrant is a degraded search, never a failed request.**
Every method raises :class:`QdrantUnavailable` and every caller catches it:
``SearchService`` falls back to Postgres full-text search, the console shows a
disconnected badge, the archive command reports and stops. Nothing in the
compliance path reads this module at all, so a dead container cannot affect a
verdict.

**Payload indexes are created with the collection, not after it fills.** A
filter on an unindexed payload field in Qdrant is a full scan of the segment,
which is fast on ten thousand points and is not on eight million — and the
version of this that "adds the indexes later" is the version where a filtered
search on the deployed server is thirty times slower than on the laptop it was
tested on. ``ensure_collection`` is idempotent and cheap, so it runs at the
start of every archive run rather than in a migration.
"""

from __future__ import annotations

import logging
import threading
from dataclasses import dataclass
from typing import Any, Sequence

from django.conf import settings

logger = logging.getLogger(__name__)

#: Notice ids per ``set_payload`` request. A filter matching thirteen thousand
#: values in one request is a request no server should be asked to parse.
STAMP_BATCH = 400


class QdrantUnavailable(RuntimeError):
    """The vector store cannot serve this call.

    Covers a missing client library, a container that is down, and a rejected
    request alike, because every caller does the same thing with all three:
    degrade. Where the distinction matters — the console's status badge — the
    message carries it.
    """


@dataclass(frozen=True)
class SearchHit:
    """One retrieved passage, as the API returns it.

    A thin dataclass rather than the client's own result type, so the response
    serializer does not import ``qdrant_client`` and the full-text fallback can
    produce the same shape. That symmetry is the whole reason the fallback is
    usable: the front end draws a citation from a hit and never asks which
    retrieval path produced it — it reads ``retrieval`` if it wants to say.
    """

    score: float
    payload: dict[str, Any]
    retrieval: str = "vector"

    def as_dict(self) -> dict[str, Any]:
        payload = dict(self.payload)
        return {
            "score": round(self.score, 6),
            "retrieval": self.retrieval,
            "content": payload.pop("content", ""),
            "notice_id": payload.get("notice_id", ""),
            "title": payload.get("title", ""),
            "source_type": payload.get("source_type", "text"),
            "payload": payload,
        }


@dataclass(frozen=True)
class CollectionStats:
    """What the console shows about the index. Never raises to build."""

    connected: bool
    exists: bool
    points: int = 0
    indexed_vectors: int = 0
    segments: int = 0
    status: str = ""
    vector_size: int = 0
    distance: str = ""
    error: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "connected": self.connected,
            "exists": self.exists,
            "points": self.points,
            "indexed_vectors": self.indexed_vectors,
            "segments": self.segments,
            "status": self.status,
            "vector_size": self.vector_size,
            "distance": self.distance,
            "error": self.error,
        }


class QdrantService:
    """Collection lifecycle, upserts and filtered search over one collection."""

    def __init__(
        self,
        collection: str | None = None,
        *,
        payload_indexes: tuple[tuple[str, str], ...] | None = None,
    ):
        config = settings.RAG
        self.collection = collection or config["COLLECTION"]
        self.vector_size: int = config["VECTOR_SIZE"]
        self.url: str = config["QDRANT_URL"]
        self.timeout: int = config["QDRANT_TIMEOUT"]
        self.upsert_batch: int = config["UPSERT_BATCH"]
        #: Which payload keys this collection filters on. Overridable because
        #: the semantic cache (D57) is a second collection through the same
        #: client with an entirely different payload — indexing the archive's
        #: seven keys on it would cost memory for filters nobody issues.
        self.payload_indexes = (
            self.PAYLOAD_INDEXES if payload_indexes is None else payload_indexes
        )
        self._client: Any = None
        self._lock = threading.Lock()

    # -- client -------------------------------------------------------------
    def _models(self):
        try:
            from qdrant_client import models  # noqa: PLC0415 - optional dependency
        except ImportError as exc:  # pragma: no cover - packaging guard
            raise QdrantUnavailable(
                "The `qdrant-client` package is not installed."
            ) from exc
        return models

    def client(self) -> Any:
        if self._client is not None:
            return self._client
        with self._lock:
            if self._client is None:
                try:
                    from qdrant_client import QdrantClient  # noqa: PLC0415
                except ImportError as exc:  # pragma: no cover - packaging guard
                    raise QdrantUnavailable(
                        "The `qdrant-client` package is not installed."
                    ) from exc
                # Constructing the client does not open a connection, so this
                # cannot fail for a container that is merely down — that shows
                # up on the first call, which is where it belongs.
                self._client = QdrantClient(
                    url=self.url,
                    api_key=settings.RAG["QDRANT_API_KEY"] or None,
                    timeout=self.timeout,
                    prefer_grpc=False,
                )
                logger.info("Qdrant client initialised (%s)", self.url)
        return self._client

    def reset(self) -> None:
        with self._lock:
            self._client = None

    # -- collection ---------------------------------------------------------
    def ensure_collection(self) -> bool:
        """Create the collection and its payload indexes if they are missing.

        Returns ``True`` when it created something. Idempotent, so it is safe
        at the head of every run — and it is called there rather than from a
        migration on purpose: a Django migration that talks to a third service
        turns ``migrate`` into something that fails when that service is down,
        and this stack runs ``migrate`` on every container start.

        An existing collection with the wrong width is a hard error, not a
        silent re-create. Dropping it would throw away hours of embedding
        because somebody changed a setting; the operator is told to pick a new
        ``RAG_COLLECTION`` or delete this one deliberately.
        """
        models = self._models()
        client = self.client()
        try:
            if client.collection_exists(self.collection):
                self._check_width(client)
                self._ensure_payload_indexes()
                return False

            client.create_collection(
                collection_name=self.collection,
                vectors_config=models.VectorParams(
                    size=self.vector_size,
                    distance=models.Distance.COSINE,
                    # Stated rather than defaulted. The vectors are what a
                    # search actually traverses, and on the deployed server
                    # the first query after an idle spell cost 2.1 s against
                    # 32 ms warm (D63) — the kernel had reclaimed the pages
                    # backing them. `False` here means "this collection's
                    # vectors belong in RAM", which for 76,015 × 768 floats is
                    # about 230 MB and is affordable; the payload below is the
                    # large half and stays where it is.
                    #
                    # It binds new collections only. An existing one is
                    # changed through Qdrant's collection-update API, and the
                    # warm-up task is what covers the deployment that has not
                    # had that done — see apps/rag_indexer/tasks.py.
                    on_disk=False,
                ),
                # The payload carries every chunk's text, so it is the large
                # half of the collection and the half that is only read for
                # results actually returned. On disk it costs one seek per hit
                # and keeps RAM for the vectors, which are what the search
                # actually traverses.
                on_disk_payload=True,
            )
            logger.info(
                "Created Qdrant collection %s (size=%d, distance=cosine)",
                self.collection, self.vector_size,
            )
            self._ensure_payload_indexes()
            return True
        except QdrantUnavailable:
            raise
        except Exception as exc:  # noqa: BLE001 - one failure type upwards
            raise QdrantUnavailable(f"Could not prepare the collection: {exc}") from exc

    def _check_width(self, client: Any) -> None:
        info = client.get_collection(self.collection)
        params = getattr(getattr(info, "config", None), "params", None)
        vectors = getattr(params, "vectors", None)
        size = getattr(vectors, "size", None)
        if size and int(size) != self.vector_size:
            raise QdrantUnavailable(
                f"Collection {self.collection} stores {size}-dimension vectors "
                f"and this deployment produces {self.vector_size}. Point "
                f"RAG_COLLECTION at a new name, or delete the collection."
            )

    #: Payload fields worth an index, with the schema Qdrant filters them by.
    #: Only fields something actually filters on: an index costs memory and
    #: write time, and indexing ``content`` here would build a second, worse
    #: full-text engine beside the Postgres one the fallback already uses.
    PAYLOAD_INDEXES: tuple[tuple[str, str], ...] = (
        ("notice_id", "keyword"),
        ("category", "keyword"),
        ("subcategory", "keyword"),
        ("document_id", "keyword"),
        ("source_type", "keyword"),
        ("source_key", "keyword"),
        ("is_award", "bool"),
    )

    def _ensure_payload_indexes(self) -> None:
        models = self._models()
        client = self.client()
        schemas = {
            "integer": models.PayloadSchemaType.INTEGER,
            "keyword": models.PayloadSchemaType.KEYWORD,
            "bool": models.PayloadSchemaType.BOOL,
        }
        for field, kind in self.payload_indexes:
            try:
                client.create_payload_index(
                    collection_name=self.collection,
                    field_name=field,
                    field_schema=schemas[kind],
                    wait=False,
                )
            except Exception as exc:  # noqa: BLE001 - already-exists is normal
                # Qdrant answers an existing index with an error rather than a
                # no-op, and the message differs by version. Logged at debug
                # because on every run after the first this is the expected
                # path for all seven fields.
                logger.debug("Payload index %s: %s", field, exc)

    # -- writing ------------------------------------------------------------
    def upsert(self, points: Sequence[tuple[str, list[float], dict[str, Any]]]) -> int:
        """Write ``(id, vector, payload)`` triples. Returns how many were sent.

        ``wait=False``: the archive run is the only writer and it does not read
        back what it wrote, so blocking on each batch's indexing would spend
        the whole run waiting for a segment optimiser. The console's point
        count lags a few seconds behind a running import as a result, which is
        the correct trade and is why the console shows Qdrant's own
        ``indexed_vectors`` beside it.
        """
        if not points:
            return 0
        models = self._models()
        client = self.client()
        structs = [
            models.PointStruct(id=point_id, vector=vector, payload=payload)
            for point_id, vector, payload in points
        ]
        sent = 0
        try:
            for start in range(0, len(structs), self.upsert_batch):
                batch = structs[start : start + self.upsert_batch]
                client.upsert(collection_name=self.collection, points=batch, wait=False)
                sent += len(batch)
        except Exception as exc:  # noqa: BLE001
            raise QdrantUnavailable(f"Upsert failed after {sent} points: {exc}") from exc
        return sent

    def stamp_payload(self, notice_ids: Sequence[str], payload: dict[str, Any]) -> int:
        """Set payload keys on every point of the given notices. No re-embedding.

        This exists because a payload field can be *added* to an index that
        already cost hours to build. `is_award` was not in the schema when the
        archive was embedded, and re-running the import to introduce it would
        have meant paying for 74,000 embeddings to write one boolean. Qdrant
        sets payload by filter, so the same change is a handful of requests
        over vectors that never move.

        Batched because a filter matching thirteen thousand ids in one request
        is a request no server should be asked to parse.
        """
        if not notice_ids:
            return 0
        models = self._models()
        client = self.client()
        ids = list(notice_ids)
        stamped = 0
        try:
            for start in range(0, len(ids), STAMP_BATCH):
                batch = ids[start : start + STAMP_BATCH]
                client.set_payload(
                    collection_name=self.collection,
                    payload=payload,
                    points=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="notice_id", match=models.MatchAny(any=batch)
                            )
                        ]
                    ),
                    wait=False,
                )
                stamped += len(batch)
        except Exception as exc:  # noqa: BLE001
            raise QdrantUnavailable(f"Could not stamp payload: {exc}") from exc
        return stamped

    def delete_source(self, source_key: str) -> None:
        """Remove every point of one source.

        Needed because re-indexing a *changed* source is not a pure upsert: if
        the new parse yields fewer chunks, the ids of the ones that disappeared
        are still in the collection and still match searches, pointing at
        offsets that no longer exist. Deleting first costs one filtered delete
        per changed source and removes that whole class of ghost result.
        """
        models = self._models()
        client = self.client()
        try:
            client.delete(
                collection_name=self.collection,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key="source_key",
                                match=models.MatchValue(value=source_key),
                            )
                        ]
                    )
                ),
                wait=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise QdrantUnavailable(f"Could not delete {source_key}: {exc}") from exc

    def delete_older_than(self, key: str, cutoff: int) -> None:
        """Remove every point whose integer payload ``key`` is below ``cutoff``.

        Written for the semantic cache's expiry sweep (D57) and kept general,
        because the shape — "delete by a range over an indexed payload field" —
        is the store's, not the cache's. The field must carry an integer
        payload index or Qdrant scans the segment to answer it.
        """
        models = self._models()
        client = self.client()
        try:
            client.delete(
                collection_name=self.collection,
                points_selector=models.FilterSelector(
                    filter=models.Filter(
                        must=[
                            models.FieldCondition(
                                key=key, range=models.Range(lt=cutoff)
                            )
                        ]
                    )
                ),
                wait=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise QdrantUnavailable(f"Could not sweep {self.collection}: {exc}") from exc

    def drop_collection(self) -> None:
        """Delete the whole collection. Only ever called on a rebuildable one.

        There is no guard here and there is one at every call site instead:
        this is the archive's client too, and a method that decided for itself
        which collections were safe to drop would be deciding it from a
        settings string.
        """
        client = self.client()
        try:
            if client.collection_exists(self.collection):
                client.delete_collection(collection_name=self.collection)
        except Exception as exc:  # noqa: BLE001
            raise QdrantUnavailable(
                f"Could not drop {self.collection}: {exc}"
            ) from exc

    # -- reading ------------------------------------------------------------
    def points_for_source(
        self, source_key: str, *, limit: int = 24
    ) -> list[dict[str, Any]]:
        """A source's stored points — vector and payload — in index order.

        Read back rather than recomputed. Embedding the notice again to get a
        query vector would cost a metered call on every page view and would
        produce a *different* vector: ``RETRIEVAL_QUERY`` places a question,
        not a passage, so a tender would not even be closest to itself. These
        are the exact vectors the archive was indexed with.

        Bounded, because the caller is choosing between candidates rather than
        reading the document: a bidding document has hundreds of chunks and the
        passage that says what is being bought is not the two hundredth.
        """
        models = self._models()
        client = self.client()
        try:
            points, _next = client.scroll(
                collection_name=self.collection,
                scroll_filter=models.Filter(
                    must=[
                        models.FieldCondition(
                            key="source_key", match=models.MatchValue(value=source_key)
                        )
                    ]
                ),
                limit=limit,
                with_payload=True,
                with_vectors=True,
            )
        except Exception as exc:  # noqa: BLE001
            raise QdrantUnavailable(f"Could not read {source_key}: {exc}") from exc
        return [
            {"vector": list(point.vector or []), "payload": dict(point.payload or {})}
            for point in points
            if point.vector
        ]

    def build_filter(self, *, exclude: dict[str, Any] | None = None, **conditions: Any) -> Any:
        """A hard filter over the indexed payload keys, or ``None``.

        ``None`` rather than an empty ``Filter`` for the unfiltered case: the
        client treats them the same, but ``None`` is what a reader of the
        search call sees as "the whole collection", and an empty ``must`` list
        has read as a bug to three people already.

        Blank values are dropped rather than matched. A category filter of
        ``""`` would otherwise match exactly the unclassified notices — the
        99.9% of the mirror that has no direction yet — which is the opposite
        of what a caller sending an empty form field meant.
        """
        models = self._models()

        def conditions_of(source: dict[str, Any]) -> list[Any]:
            built = []
            for key, value in source.items():
                if value is None or value == "" or value == []:
                    continue
                if isinstance(value, (list, tuple, set)):
                    built.append(
                        models.FieldCondition(
                            key=key, match=models.MatchAny(any=list(value))
                        )
                    )
                else:
                    built.append(
                        models.FieldCondition(
                            key=key, match=models.MatchValue(value=value)
                        )
                    )
            return built

        must = conditions_of(conditions)
        # `exclude` is what makes "notices like this one" possible at all: the
        # nearest neighbours of a tender's own vectors are its own chunks, and
        # without a must_not the whole result page is the tender the reader is
        # already looking at.
        must_not = conditions_of(exclude or {})
        if not must and not must_not:
            return None
        return models.Filter(must=must or None, must_not=must_not or None)

    def search(
        self,
        vector: Sequence[float],
        *,
        limit: int,
        query_filter: Any = None,
        score_threshold: float | None = None,
    ) -> list[SearchHit]:
        """Top-``limit`` nearest passages by cosine similarity.

        ``score_threshold`` is applied by Qdrant rather than by the caller, so
        a filtered-out low match never costs the payload read. It exists
        because cosine always returns *something*: with no floor, a question
        about turnover thresholds asked against a corpus that never mentions
        them comes back with five confident-looking irrelevant passages, and
        the citation badge makes them look sourced.
        """
        client = self.client()
        try:
            response = client.query_points(
                collection_name=self.collection,
                query=list(vector),
                limit=limit,
                query_filter=query_filter,
                score_threshold=score_threshold,
                with_payload=True,
                with_vectors=False,
            )
        except Exception as exc:  # noqa: BLE001
            raise QdrantUnavailable(f"Search failed: {exc}") from exc

        return [
            SearchHit(score=float(point.score), payload=dict(point.payload or {}))
            for point in getattr(response, "points", [])
        ]

    # -- reporting ----------------------------------------------------------
    def stats(self) -> CollectionStats:
        """A snapshot for the console. Reports failure; never raises it.

        The console polls this every few seconds, and an endpoint that 500s
        when a side service is down tells the operator less than a badge that
        says the side service is down.
        """
        try:
            client = self.client()
            if not client.collection_exists(self.collection):
                return CollectionStats(connected=True, exists=False)
            info = client.get_collection(self.collection)
        except Exception as exc:  # noqa: BLE001 - see the docstring
            return CollectionStats(connected=False, exists=False, error=str(exc))

        vectors = getattr(getattr(getattr(info, "config", None), "params", None), "vectors", None)
        return CollectionStats(
            connected=True,
            exists=True,
            points=int(getattr(info, "points_count", 0) or 0),
            indexed_vectors=int(getattr(info, "indexed_vectors_count", 0) or 0),
            segments=int(getattr(info, "segments_count", 0) or 0),
            status=str(getattr(info, "status", "") or ""),
            vector_size=int(getattr(vectors, "size", 0) or 0),
            distance=str(getattr(vectors, "distance", "") or ""),
        )


_service: QdrantService | None = None
_service_lock = threading.Lock()


def get_qdrant_service() -> QdrantService:
    """The process-wide service, built lazily."""
    global _service
    if _service is None:
        with _service_lock:
            if _service is None:
                _service = QdrantService()
    return _service


def reset_qdrant_service() -> None:
    global _service
    with _service_lock:
        _service = None
