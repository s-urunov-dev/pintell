"""Services for the semantic index.

Split by *what they talk to* rather than by what they are about — which is why
each one can be replaced without touching the others:

* :mod:`extraction` — text and PDFs. Talks to the mirror on disk.
* :mod:`embedding` — the embedding provider. The only module that knows which.
* :mod:`qdrant` — the vector store. The only module that knows which.
* :mod:`indexing` — the archive walk, which is where the first three meet.
* :mod:`search` — the read path, and the full-text fallback under it.
* :mod:`fusion` — merging two ranked lists that do not share a scale.
* :mod:`rerank` — reading the candidates again, together with the question.
* :mod:`cache` — the answer to a question already answered. Its own collection.
* :mod:`routing` — which model answers, and what it is actually shown.
* :mod:`structured` — cutting an uploaded document on its own headings.
* :mod:`similarity` — which passage stands for a notice, and what is near it.
* :mod:`chat` — a question answered in prose, every claim tied to a passage.

Re-exported here so callers import from ``apps.rag_indexer.services`` and not
from a module path that is really an implementation detail of this split.
"""

from .cache import (
    CachedAnswer,
    SemanticCache,
    get_semantic_cache,
    reset_semantic_cache,
)
from .chat import ChatAnswer, ChatService, get_chat_service, reset_chat_service
from .embedding import (
    EmbeddingService,
    EmbeddingUnavailable,
    get_embedding_service,
    reset_embedding_service,
)
from .extraction import Extraction, ExtractionService
from .indexing import Candidate, IndexingService, RunStats
from .qdrant import (
    CollectionStats,
    QdrantService,
    QdrantUnavailable,
    SearchHit,
    get_qdrant_service,
    reset_qdrant_service,
)
from .rerank import (
    RerankService,
    RerankUnavailable,
    get_rerank_service,
    reset_rerank_service,
)
from .routing import Route, compress, route
from .search import SearchResponse, SearchService, get_search_service, reset_search_service
from .similarity import (
    Representative,
    SimilarityService,
    get_similarity_service,
    reset_similarity_service,
)

__all__ = [
    "CachedAnswer",
    "Candidate",
    "ChatAnswer",
    "ChatService",
    "CollectionStats",
    "EmbeddingService",
    "EmbeddingUnavailable",
    "Extraction",
    "ExtractionService",
    "IndexingService",
    "QdrantService",
    "QdrantUnavailable",
    "RerankService",
    "RerankUnavailable",
    "Route",
    "RunStats",
    "SearchHit",
    "SearchResponse",
    "Representative",
    "SearchService",
    "SemanticCache",
    "SimilarityService",
    "compress",
    "get_chat_service",
    "get_embedding_service",
    "get_qdrant_service",
    "get_rerank_service",
    "get_search_service",
    "get_semantic_cache",
    "get_similarity_service",
    "reset_chat_service",
    "reset_embedding_service",
    "reset_qdrant_service",
    "reset_rerank_service",
    "reset_search_service",
    "reset_semantic_cache",
    "reset_similarity_service",
    "route",
]
