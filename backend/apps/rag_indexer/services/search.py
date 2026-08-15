"""Answering a question against the archive, and saying how it was answered.

The vector path is the product; the full-text path is what keeps the endpoint
truthful when the vector path cannot run. Both return the same shape, so a
client can render either — but each hit says which produced it, and that field
is not decoration.

**The two scores are not comparable and the API must never pretend they are.**
A cosine similarity between 0 and 1 and a ``ts_rank`` are different quantities
on different scales; averaging them, thresholding them together, or sorting one
list containing both would produce an ordering that means nothing. So a
response is *either* vector or full-text, never a merge, and ``retrieval`` says
which. Anything downstream that wants to weight them has to decide that
explicitly, with the field in hand.

**The fallback is a fallback, not a second opinion.** It runs when the vector
path cannot answer at all — no embedding key, Qdrant down, an embedding call
that failed — and when the vector path returns nothing. It does *not* run to
"top up" a short result list: three good passages plus two keyword matches
reads to a user as five results of one kind, and the two kinds are not of one
quality.

**Hybrid is the third mode, and it does not reopen either argument** (D58). A
caller may ask for ``hybrid=True``, in which case both arms run and their
results are fused by :mod:`.fusion` — over *ranks*, never scores. Rank is the
one quantity both paths genuinely produce on one scale, so the objection above
does not apply to it; a hit's cosine value reaches nothing. And this is not the
fallback topping up a short list: hybrid is asked for, both arms run every
time, and the response says ``hybrid`` rather than borrowing either label.

It is opt-in per call rather than a global default because the two callers want
different things. The chat asks for it — a reader who types a tender reference
means *that* tender, and the dense arm cannot find an identifier. The plain
search box asks for it too. Anything else keeps the older contract untouched.

**"Still open" is a post-filter, and that is the correct design rather than a
shortcut.** The obvious alternative is a ``deadline`` in the payload, filtered
inside Qdrant — and it would be wrong: a deadline baked into a vector store
goes stale the moment it passes, so the index would have to be rewritten daily
to keep saying something true. Whether a tender is open is a fact about *now*
against a row in Postgres, so it is asked of Postgres, per request. The cost is
over-fetching from the store and discarding what has closed.

**The fallback still returns positions.** It would be easier to hand back whole
notices, and it would quietly break the viewer: a citation badge with no
``char_start`` cannot highlight anything, so every fallback result would open a
document scrolled to the top with nothing marked. Instead the matched notices
are cut by the *same* ``ExtractionService.text_chunks`` the indexer uses, so
the offsets a fallback hit carries are byte-for-byte the offsets its vector
equivalent would have carried.
"""

from __future__ import annotations

import logging
import re
import time
from dataclasses import dataclass
from typing import Any

from django.conf import settings
from django.contrib.postgres.search import SearchQuery, SearchRank, SearchVector
from django.db import DatabaseError
from django.db.models import F, Q

from apps.tenders.models import TenderNotice

from ..chunks import SourceRef
from . import fusion
from .embedding import EmbeddingService, EmbeddingUnavailable, get_embedding_service
from .extraction import ExtractionService
from .qdrant import QdrantService, QdrantUnavailable, SearchHit, get_qdrant_service

logger = logging.getLogger(__name__)

#: The text search configuration Postgres parses the query with. English
#: because the corpus is: World Bank notices are published in English even for
#: Uzbek and Russian borrowers, with the local-language version linked as a
#: document. A per-notice language column exists (`notice_language`) and would
#: be the honest input to this if the fallback ever became a primary path.
FTS_CONFIG = "english"

#: Notices the fallback reads bodies from. The ranking query itself is indexed
#: and cheap; cutting a body into sentences is not, and this bounds it. Set
#: above the result limit so the best passage is still likely to be found in a
#: notice that ranked third rather than first.
FTS_NOTICE_SCAN = 25

_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)

#: How much wider to search when closed tenders will be discarded afterwards.
#: Of ~25,000 indexed notices only a few dozen are open, so the ratio is brutal
#: — but the passages of an *open* tender rank highly for a question a vendor
#: is actually asking, and a wider net past this point costs payload reads for
#: results nobody sees. Where it is not enough the answer is short rather than
#: wrong, which is the right way for this to fail.
ACTIVE_OVERFETCH = 12

#: A tender reference as the borrower prints it — ``TRIP-CS-01``, ``UZ-MOF-42``.
#: Two or more hyphen-joined runs of letters and digits, at least one of which
#: has a digit, which is what separates a reference from a hyphenated English
#: phrase like ``pre-qualification``.
#:
#: This exists because neither arm finds one on its own. The dense arm has no
#: neighbourhood for an identifier, and Postgres' ``english`` configuration
#: splits the code on its hyphens and then stems the pieces, so a search for
#: ``TRIP-CS-01`` matches every notice containing the word "trip". A literal
#: match on the columns that actually carry a reference is the only thing that
#: answers the question the reader asked.
_REFERENCE_RE = re.compile(r"\b(?=[\w-]*\d)[A-Za-z0-9]+(?:-[A-Za-z0-9]+)+\b")

#: Notices a reference probe may return. A code is meant to identify one
#: tender; a probe that matched dozens has matched something else, and the
#: fused list should not be flooded by it.
REFERENCE_LIMIT = 5


def _open_first(hits: list[SearchHit]) -> list[SearchHit]:
    """Open tenders first, everything stamped with whether it is still live.

    One query for the whole batch rather than one per hit, and the *ids* are
    fetched rather than the rows: nothing here needs a notice, only the answer
    to "is this one still live". Relevance order is preserved within each
    group, so the ordering says "live and most relevant, then history and most
    relevant" rather than re-ranking anything.

    Stamping rather than dropping is the point. A closed contract is the wrong
    answer to "what can I bid on" and the *right* material for "what do these
    tenders typically require" — and the only thing that separates those two
    uses is whether the reader is told which is which.
    """
    notice_ids = {hit.payload.get("notice_id", "") for hit in hits}
    notice_ids.discard("")
    if not notice_ids:
        return list(hits)

    open_ids = set(
        TenderNotice.objects.filter(notice_id__in=notice_ids)
        .bidding_open()
        .values_list("notice_id", flat=True)
    )

    live: list[SearchHit] = []
    archived: list[SearchHit] = []
    for hit in hits:
        is_open = hit.payload.get("notice_id") in open_ids
        # Written onto the payload so it survives into the API response and the
        # model's source list without a second lookup anywhere downstream.
        hit.payload["tender_open"] = is_open
        (live if is_open else archived).append(hit)
    return live + archived


@dataclass
class SearchResponse:
    """The answer, plus what it cost and which path produced it."""

    hits: list[SearchHit]
    retrieval: str
    took_ms: int
    #: Why the vector path did not answer, when it did not. Empty on the happy
    #: path. Surfaced in the response because an operator watching an empty
    #: result list needs to know whether the index is missing or the corpus is.
    degraded_reason: str = ""

    def as_dict(self) -> dict[str, Any]:
        return {
            "retrieval": self.retrieval,
            "took_ms": self.took_ms,
            "degraded_reason": self.degraded_reason,
            "count": len(self.hits),
            "results": [hit.as_dict() for hit in self.hits],
        }


class SearchService:
    """Vector search over the archive, with a Postgres full-text fallback."""

    def __init__(
        self,
        *,
        embedding: EmbeddingService | None = None,
        store: QdrantService | None = None,
        extraction: ExtractionService | None = None,
    ):
        self.embedding = embedding or get_embedding_service()
        self.store = store or get_qdrant_service()
        self.extraction = extraction or ExtractionService()

    def search(
        self,
        query: str,
        *,
        limit: int | None = None,
        notice_id: str = "",
        category: str = "",
        subcategory: str = "",
        source_type: str = "",
        active_only: bool = False,
        hybrid: bool = False,
        vector: list[float] | None = None,
    ) -> SearchResponse:
        """Top passages for ``query``, hard-filtered by whatever was supplied.

        ``active_only`` **orders and labels; it does not discard.** That is a
        correction of the first version, which dropped closed tenders outright
        and made a different answer wrong: "what turnover do IT tenders
        require?" is a question about a *pattern*, and the pattern lives in the
        archive — filtering it away left one open notice that happened to say
        nothing, and a truthful reply that helped nobody.

        So open tenders come first and every hit is stamped ``tender_open``.
        The caller can then say the thing that actually needed saying, which
        was never "ignore history" but "do not present a contract that closed
        in 2011 as something to bid on". The chat's prompt does exactly that:
        archived passages may establish a range, never an opportunity.

        Filters are applied *inside* Qdrant rather than to its results. The
        difference is not performance: a post-filter asks for the top five of
        the whole archive and then discards the ones from other tenders, so a
        search scoped to one notice returns however many of the global top five
        happened to come from it — usually none. The pre-filter is what makes
        "search within this tender" mean what it says.

        ``vector`` lets a caller hand in a query embedding it has already paid
        for — the semantic cache (D57) embeds the question to look itself up,
        and embedding it again here would double the one cost a cache miss
        cannot avoid. It must be the *query* embedding: a passage vector placed
        here searches with the wrong task type and returns quietly worse
        results, which is exactly the failure ``EmbeddingService`` keeps two
        methods to prevent.
        """
        started = time.monotonic()
        limit = limit or settings.RAG["SEARCH_LIMIT"]
        query = (query or "").strip()
        if not query:
            return SearchResponse([], "none", 0, "empty_query")

        # The setting is a ceiling, not a default: a deployment that turns
        # hybrid off turns it off for callers that ask for it too.
        hybrid = hybrid and bool(settings.RAG["HYBRID"])
        reason = ""
        dense: list[SearchHit] = []
        try:
            dense = self._vector_search(
                query,
                # Over-fetch when the closed tenders still have to be thrown
                # away afterwards; the store cannot know today's date.
                limit=limit * ACTIVE_OVERFETCH if active_only else limit,
                notice_id=notice_id,
                category=category,
                subcategory=subcategory,
                source_type=source_type,
                vector=vector,
            )
            if active_only:
                dense = _open_first(dense)[:limit]
            if dense and not hybrid:
                return SearchResponse(dense, "vector", _elapsed(started))
            if not dense:
                reason = "no_vector_match"
        except (EmbeddingUnavailable, QdrantUnavailable) as exc:
            # Logged at info, not error: on a deployment that never ran the
            # archive import this is the normal state of the endpoint, and an
            # error-level line per search would bury the real ones.
            logger.info("Vector search unavailable, falling back: %s", exc)
            reason = _reason_of(exc)

        lexical = self._lexical_search(
            query,
            # The lexical arm's own budget rather than the answer's. Fusion
            # needs lists long enough for agreement between the arms to mean
            # something, and a top-five list agrees with almost nothing.
            limit=settings.RAG["HYBRID_CANDIDATES"] if hybrid else limit,
            notice_id=notice_id,
            category=category,
            subcategory=subcategory,
            active_only=active_only,
            references=hybrid,
        )

        if dense and lexical:
            fused = fusion.reciprocal_rank_fusion(
                [("dense", dense), ("lexical", lexical)],
                k=settings.RAG["RRF_K"],
                limit=limit,
            )
            return SearchResponse(fused, "hybrid", _elapsed(started))
        if dense:
            # Hybrid was asked for and the lexical arm found nothing. Labelled
            # for what produced the results rather than for what was requested:
            # a reader told "hybrid" would believe two paths had agreed.
            return SearchResponse(dense, "vector", _elapsed(started))
        return SearchResponse(
            lexical[:limit], "fts" if lexical else "none", _elapsed(started), reason
        )

    # -- vector -------------------------------------------------------------
    def _vector_search(
        self,
        query: str,
        *,
        limit: int,
        notice_id: str,
        category: str,
        subcategory: str,
        source_type: str,
        vector: list[float] | None = None,
    ) -> list[SearchHit]:
        vector = vector if vector is not None else self.embedding.embed_query(query)
        query_filter = self.store.build_filter(
            notice_id=notice_id,
            category=category,
            subcategory=subcategory,
            source_type=source_type,
        )
        return self.store.search(
            vector,
            limit=limit,
            query_filter=query_filter,
            score_threshold=settings.RAG["SCORE_THRESHOLD"],
        )

    # -- lexical ------------------------------------------------------------
    def _lexical_search(
        self,
        query: str,
        *,
        limit: int,
        notice_id: str,
        category: str,
        subcategory: str,
        active_only: bool = False,
        references: bool = False,
    ) -> list[SearchHit]:
        """The keyword half: ranked full text, with exact references in front.

        Two probes rather than one, because they fail differently. Postgres'
        ``english`` configuration stems and splits, which is what makes it good
        at "turnover requirement" and useless at ``TRIP-CS-01``; a literal
        column match is the reverse. Running the cheap, bounded reference probe
        first and putting its hits at the head of the list means the notice a
        reader *named* outranks the notices that merely discuss the same words
        — before fusion ever sees either.

        Deduplicated, because both probes can return the same passage and a
        ranked list with one passage in two positions counts it twice in the
        fusion below.
        """
        found: list[SearchHit] = []
        if references:
            found.extend(
                self._reference_hits(
                    query,
                    notice_id=notice_id,
                    category=category,
                    subcategory=subcategory,
                    active_only=active_only,
                )
            )
        found.extend(
            self._fts_search(
                query,
                limit=limit,
                notice_id=notice_id,
                category=category,
                subcategory=subcategory,
                active_only=active_only,
            )
        )
        return fusion.dedupe(found)[:limit]

    def _reference_hits(
        self,
        query: str,
        *,
        notice_id: str,
        category: str,
        subcategory: str,
        active_only: bool = False,
    ) -> list[SearchHit]:
        """Notices whose own identifiers contain a code the reader typed.

        Runs only when the query holds something code-shaped, so the ordinary
        question pays nothing for it. The columns searched are the short ones
        that actually carry a reference — the notice id, the project id, the
        description line — and never ``notice_text_sanitized``: an unindexed
        ``ILIKE`` over every body in the mirror is a table scan of megabytes to
        answer a question about a label.

        Scored at 1.0 and rank-ordered by the caller. The number is not a
        similarity and nothing downstream compares it to one: fusion reads the
        *position*, and this probe returns an exact match or nothing.
        """
        codes = _REFERENCE_RE.findall(query)
        if not codes:
            return []

        queryset = TenderNotice.objects.all()
        if notice_id:
            queryset = queryset.filter(notice_id=notice_id)
        if category:
            queryset = queryset.filter(category=category)
        if subcategory:
            queryset = queryset.filter(subcategory=subcategory)
        if active_only:
            queryset = queryset.bidding_open()

        matches = Q()
        for code in codes[:3]:
            matches |= (
                Q(notice_id__icontains=code)
                | Q(project_id__icontains=code)
                | Q(bid_description__icontains=code)
            )

        try:
            notices = list(
                queryset.filter(matches).only(
                    "notice_id", "category", "subcategory",
                    "bid_description", "project_name", "notice_text_sanitized",
                )[:REFERENCE_LIMIT]
            )
        except DatabaseError as exc:
            logger.info("Reference probe failed: %s", exc)
            return []

        hits: list[SearchHit] = []
        for notice in notices:
            extraction = self.extraction.from_notice(notice)
            if not extraction.chunks:
                continue
            # The notice's opening passage, which is where a template puts what
            # is being bought. A reader who typed a reference wants the tender,
            # not the paragraph of it that happens to share their words.
            hits.append(
                SearchHit(
                    score=1.0,
                    payload=extraction.chunks[0].payload(extraction.source),
                    retrieval="reference",
                )
            )
        return hits

    # -- full text ----------------------------------------------------------
    def _fts_search(
        self,
        query: str,
        *,
        limit: int,
        notice_id: str,
        category: str,
        subcategory: str,
        active_only: bool = False,
    ) -> list[SearchHit]:
        """Postgres ranking to pick notices, then sentence chunks to cite.

        ``websearch`` rather than ``plain``: it accepts the quotes and ``or``
        that people type into a search box instead of treating them as literal
        words, and it never raises on syntax the way ``raw`` does — a search
        endpoint that 500s on an unbalanced quotation mark is a support ticket
        waiting to happen.

        **A GIN index backs this now, and that is a change of circumstance
        rather than of mind.** The original note here said a sequential scan
        was acceptable *because this was the fallback* — it ran when the vector
        path was unavailable, not on every search. Hybrid retrieval (D58) runs
        it on every question, so the argument no longer holds and migration
        ``tenders.0022`` adds the index. Still no stored ``SearchVectorField``
        and no trigger: a functional index needs neither, and neither belongs on
        the busiest table in the product.

        The match is a ``@@`` predicate rather than ``rank > 0`` for the same
        reason. The two select identically, and only the first is a predicate a
        GIN index can serve — ranking every row and then discarding the zeros
        is a full scan however well the column is indexed.

        **Matching and ranking are two queries, and that is what makes this
        affordable.** The index finds matches; it does not store a vector to
        rank them with, so ``ts_rank`` recomputes ``to_tsvector`` over a full
        notice body *per matching row*. Cost therefore scales with how many
        notices matched, not with how many are returned: on the development
        mirror "annual turnover requirement" matches 697 notices and ranking
        all of them took 570 ms — for twenty-five results.

        So the first query takes ids only, bounded by ``LEXICAL_RANK_CAP``, and
        the second ranks that sample. 570 ms becomes about 250 ms at the
        default cap, and nothing at all is lost on the common query whose match
        count is under it. Above it, the sample is index order rather than the
        best-ranked — a real loss, stated in the setting, and the reason the
        cap is a setting rather than a constant.

        The alternative is a stored ``tsvector``. Postgres generated columns
        make that trigger-free now, which retires half the original objection —
        but it is still a rewrite of the busiest table in the product and a
        Postgres-only column on a model that has none, so it is logged as an
        open decision rather than taken here (D58).
        """
        vector = SearchVector("bid_description", "notice_text_sanitized", config=FTS_CONFIG)
        search_query = SearchQuery(query, search_type="websearch", config=FTS_CONFIG)

        queryset = TenderNotice.objects.all()
        if notice_id:
            queryset = queryset.filter(notice_id=notice_id)
        if category:
            queryset = queryset.filter(category=category)
        if subcategory:
            queryset = queryset.filter(subcategory=subcategory)
        if active_only:
            # Cheaper here than on the vector side: the fallback is already
            # querying Postgres, so this is one more predicate rather than a
            # second round trip.
            queryset = queryset.bidding_open()

        # Which column the two probes below read. `search_vector` is the stored
        # tsvector (tenders 0023-0025); `vector` recomputes it per row. Same
        # value, and the difference is only ever cost — 68 ms against 652 on
        # the deployed corpus, because ranking becomes a heap read rather than
        # re-parsing a notice body per match (D63).
        #
        # The flag is off until `backfill_search_vector` reports zero rows
        # left: a partly-filled column does not rank badly, it *omits* the
        # unfilled rows from the match, and a search that quietly returns less
        # is worse than a slow one. See settings.RAG["LEXICAL_STORED_VECTOR"].
        stored = settings.RAG["LEXICAL_STORED_VECTOR"]
        match_expr = F("search_vector") if stored else vector
        rank_expr = (
            SearchRank(F("search_vector"), search_query)
            if stored
            else SearchRank(vector, search_query)
        )

        try:
            # Ids first: the match is what the index answers, and asking for
            # the bodies here would carry megabytes of text out of Postgres to
            # rank rows that mostly will not be returned.
            #
            # The cap survives the stored column and is not vestigial: it is
            # what stops a query matching most of the corpus from sorting most
            # of the corpus. "procurement" matches 22,341 notices and ranking
            # all of them costs 2.6 s even from the stored vector.
            matched = list(
                queryset.annotate(search=match_expr)
                .filter(search=search_query)
                .values_list("pk", flat=True)[: settings.RAG["LEXICAL_RANK_CAP"]]
            )
            notices = list(
                TenderNotice.objects.filter(pk__in=matched)
                .annotate(rank=rank_expr)
                .order_by("-rank")
                .only(
                    "notice_id", "category", "subcategory",
                    "bid_description", "project_name", "notice_text_sanitized",
                )[:FTS_NOTICE_SCAN]
            ) if matched else []
        except DatabaseError as exc:
            # The last line of defence. Both paths being down is a real state
            # and the endpoint answers it with an empty list rather than a 500:
            # the caller is a search box, and "nothing found" is a renderable
            # answer where a stack trace is not.
            logger.error("Full-text fallback failed: %s", exc)
            return []

        terms = set(_WORD_RE.findall(query.casefold()))
        hits: list[SearchHit] = []
        for notice in notices:
            hits.extend(self._notice_hits(notice, terms))

        hits.sort(key=lambda hit: hit.score, reverse=True)
        return hits[:limit]

    def _notice_hits(self, notice: TenderNotice, terms: set[str]) -> list[SearchHit]:
        """The best-matching passage of one notice, positioned like a vector hit.

        One passage per notice, not all of them. A notice that says "turnover"
        in nine paragraphs would otherwise fill the whole result list on its
        own, and the question a search box is asking is almost always "which
        tenders", not "how many times does this tender say it".
        """
        extraction = self.extraction.from_notice(notice)
        source: SourceRef = extraction.source

        best = None
        best_score = 0.0
        for chunk in extraction.chunks:
            words = set(_WORD_RE.findall(chunk.content.casefold()))
            overlap = len(terms & words)
            if not overlap:
                continue
            # Fraction of the query's terms present. Bounded to (0, 1] so the
            # number at least *reads* like the cosine score beside it in the
            # response — while `retrieval: "fts"` says plainly that it is not
            # one, and no caller may compare the two. See the module docstring.
            score = overlap / max(len(terms), 1)
            if score > best_score:
                best, best_score = chunk, score

        if best is None:
            return []
        return [
            SearchHit(
                score=best_score,
                payload=best.payload(source),
                retrieval="fts",
            )
        ]


def _reason_of(exc: Exception) -> str:
    """A stable code for why the vector path could not run.

    A code rather than the exception's message: the console renders it and the
    front ends localise it, and a sentence from a client library changes
    between versions.
    """
    if isinstance(exc, EmbeddingUnavailable):
        return "embeddings_unavailable"
    if isinstance(exc, QdrantUnavailable):
        return "vector_store_unavailable"
    return "vector_search_failed"


def _elapsed(started: float) -> int:
    return int((time.monotonic() - started) * 1000)


_service: SearchService | None = None


def get_search_service() -> SearchService:
    """The process-wide service. Stateless apart from its two clients."""
    global _service
    if _service is None:
        _service = SearchService()
    return _service


def reset_search_service() -> None:
    global _service
    _service = None
