"""Which passage of a notice actually says what the notice is about.

A World Bank notice is mostly boilerplate. "Interested eligible bidders may
obtain further information from…" appears, near-verbatim, in thousands of them;
so does the paragraph about the Procurement Regulations and the one about
submitting bids to the address below. The two or three sentences that say *what
is being bought* are a minority of the text.

That matters twice over. Averaging a notice's chunks into one query vector —
the first thing this module did — produces a centroid dominated by the shared
paragraphs, so every notice ends up near every other notice, scores cluster in
a band (0.920–0.921 was measured on the deployed archive), and the passage
shown as the reason is a sentence about where to collect the tender documents.

**So this module picks chunks instead of averaging, on two axes that are both
necessary.**

*Rarity*, measured against the corpus rather than guessed from a stop list:
each candidate is searched for in the collection and the number of *other
notices* holding a near-duplicate is counted. A boilerplate paragraph matches
hundreds; the sentence naming a 220 kV substation matches almost none.

*Topicality*, measured against the notice's **own title**. Rarity alone was
tried first and it optimises for the wrong thing: the most unique text in a
procurement notice is proper nouns and figures, so it chose
``Lot 1: US$1,250,000 net of the Bidders other commitments`` for an irrigation
scheme and ``Project:P176060-Clean Energy for Buildings`` for an award — both
perfectly distinctive, neither a description of the work, and both pulling in
neighbours that shared a *figure* or a *project* rather than a trade. The title
column says what the tender is for by construction, so a candidate's overlap
with it is a free, checkable proxy for "is this passage on topic".

Rarity is the filter and topicality is the ranking: boilerplate is excluded
first, then the most on-topic of what survives wins.

Two things this deliberately does not do:

* **It does not use a stop list of boilerplate phrases.** That would be a fact
  about World Bank templates written from memory into code — the one thing
  this codebase refuses to write — and it would rot the first time a borrower reworded a
  template. The corpus is the authority on what is common in the corpus.
* **It does not embed anything.** Every vector it reads is already in the
  store, so distinctiveness costs Qdrant round trips and no metered call.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any

from django.conf import settings

from .qdrant import QdrantService, QdrantUnavailable, get_qdrant_service

logger = logging.getLogger(__name__)

#: Chunks considered as the representative passage. The distinguishing sentence
#: of a notice is near the front — the object of the procurement is stated
#: before the administrative apparatus — and every extra candidate costs a
#: search.
CANDIDATE_CHUNKS = 12

#: Similarity above which two passages count as the same boilerplate rather
#: than two documents making the same point. Deliberately high: this is meant
#: to catch near-verbatim template text, not topical agreement, and a lower
#: value would punish a chunk for the crime of being about irrigation in a
#: corpus with a lot of irrigation.
DUPLICATE_THRESHOLD = 0.93

#: Duplicates above which a candidate is treated as boilerplate and excluded
#: from the ranking entirely. Well below ``DUPLICATE_SCAN``: a passage shared
#: with a dozen other notices is template text whatever its topic.
BOILERPLATE_DUPLICATES = 8

#: Words too common in this corpus to say anything about a tender's topic.
#: **Not a boilerplate list** — these are the words shared by every *title*,
#: and they are here so title overlap measures the trade rather than the
#: grammar. Short and observable, unlike a list of template paragraphs.
_TITLE_STOPWORDS = frozenset({
    "the", "of", "and", "for", "in", "to", "a", "an", "on", "at", "by", "with",
    "procurement", "supply", "services", "service", "works", "work", "contract",
    "project", "consulting", "consultancy", "consultant", "tender", "bid",
    "bids", "notice", "award", "lot", "no", "phase", "part",
})

_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)

#: Distinctive chunks used as query vectors. See ``representatives``: one is
#: not enough, because the rarest sentence of a tender is often a figure rather
#: than a description of the work.
QUERY_CHUNKS = 3

#: Notices examined when counting a chunk's duplicates. A count is only used to
#: rank candidates against each other, so the ceiling matters more than the
#: precision: a chunk that hits the cap is boilerplate whatever the true total.
DUPLICATE_SCAN = 40


@dataclass(frozen=True)
class Representative:
    """The passage chosen to stand for a notice, and why it was chosen."""

    vector: list[float]
    content: str
    payload: dict[str, Any]
    #: Other notices holding a near-duplicate of this passage. Low is
    #: distinctive. Kept on the result because it is the whole justification
    #: for the choice, and a number nobody can see is a number nobody checks.
    duplicates: int
    #: Candidates that were compared to reach it.
    considered: int
    #: Title words this passage repeats. High is on-topic. Zero is common and
    #: not disqualifying — a notice whose title is "Lot 2" has no signal to
    #: give, and rarity then decides alone.
    topicality: int = 0

    @property
    def is_boilerplate(self) -> bool:
        """True when even the best candidate is common across the corpus.

        A notice can be boilerplate all the way down — a two-line cancellation,
        a template with the object left blank. Saying so lets the caller decide
        rather than presenting a shared paragraph as a distinguishing one.
        """
        return self.duplicates >= DUPLICATE_SCAN


class SimilarityService:
    """Choosing a notice's representative passage, and finding its neighbours."""

    def __init__(self, store: QdrantService | None = None):
        self.store = store or get_qdrant_service()

    def representatives(
        self, source_key: str, *, count: int = 1, title: str = ""
    ) -> list[Representative]:
        """The ``count`` least common chunks of a source, most distinctive first.

        More than one, because distinctiveness and *topicality* are not the
        same thing and the difference bit on the first live run. The rarest
        sentence of an irrigation tender turned out to be
        ``Lot 1: US$1,250,000 net of the Bidders other commitments`` — unique,
        because that exact figure appears nowhere else, and useless as a
        description of what is being bought. Its nearest neighbours were other
        notices' financial-requirement paragraphs rather than contracts for
        similar work.

        Averaging was the wrong fix for that (it is what put boilerplate at the
        centre in the first place). Searching with several distinctive chunks
        and merging is the right one: a tender has more than one distinguishing
        sentence, and the ones that are *about the work* pull in neighbours
        that are too.
        """
        try:
            candidates = self.store.points_for_source(
                source_key, limit=CANDIDATE_CHUNKS
            )
        except QdrantUnavailable as exc:
            logger.info("No representative for %s: %s", source_key, exc)
            return []

        wanted = _title_words(title or _title_of(candidates))

        scored: list[Representative] = []
        for point in candidates:
            vector = point.get("vector") or []
            if not vector:
                continue
            payload = dict(point.get("payload") or {})
            content = payload.pop("content", "")
            scored.append(
                Representative(
                    vector=vector,
                    content=content,
                    payload=payload,
                    duplicates=self._count_duplicates(vector, source_key),
                    considered=len(candidates),
                    topicality=len(wanted & _title_words(content)),
                )
            )

        # Boilerplate is excluded rather than merely ranked down, because a
        # template paragraph that happens to repeat a title word would
        # otherwise outrank a genuinely specific sentence that does not. If
        # every candidate is boilerplate the notice has nothing else to offer,
        # and the least common of them is the honest answer.
        on_topic = [item for item in scored if item.duplicates < BOILERPLATE_DUPLICATES]
        pool = on_topic or scored
        pool.sort(key=lambda item: (-item.topicality, item.duplicates))
        return pool[:count]

    def representative(self, source_key: str) -> Representative | None:
        """The most distinctive indexed passage of one source, or ``None``.

        ``None`` means there is nothing to represent: the source was never
        indexed, or the store cannot answer. Both are states the caller renders
        as an empty panel — never as an error, because this sits beside a
        tender a vendor is reading and the health of a cache is not their
        problem.
        """
        found = self.representatives(source_key, count=1)
        return found[0] if found else None

    def _count_duplicates(self, vector: list[float], source_key: str) -> int:
        """How many *other notices* carry a near-duplicate of this passage.

        Counted by notice rather than by point, because a long document
        repeating its own header on every page would otherwise look like the
        most common text in the archive rather than the most repetitive
        document in it.
        """
        try:
            hits = self.store.search(
                vector,
                limit=DUPLICATE_SCAN,
                query_filter=self.store.build_filter(exclude={"source_key": source_key}),
                score_threshold=DUPLICATE_THRESHOLD,
            )
        except QdrantUnavailable:
            # Unknown rather than zero: returning zero would crown whichever
            # candidate happened to fail, which is the opposite of the answer.
            return DUPLICATE_SCAN
        return len({hit.payload.get("notice_id", "") for hit in hits})

    # -- awarded contracts like this tender ---------------------------------
    def similar_award_notices(
        self, source_key: str, *, limit: int, scan: int = 200, title: str = ""
    ) -> list[tuple[str, float, str]]:
        """``(notice_id, score, passage)`` for the nearest *other* notices.

        Returns notices, not contracts: whether a notice carries an award, and
        whether that award names a winner, are facts about rows in Postgres,
        and answering them here would mean a second copy of
        ``ContractAward``'s rules living in the vector layer. The caller joins.

        ``scan`` is generous for that reason — the caller discards every
        candidate that turns out not to be an award with a named winner, and a
        tight scan would leave a panel of two rows.
        """
        chosen = self.representatives(source_key, count=QUERY_CHUNKS, title=title)
        if not chosen:
            return []

        # One row per notice, keeping its best passage across every query
        # chunk. Merged on the score rather than on which query found it: the
        # reader is shown the sentence that matched *best*, and which of the
        # tender's distinguishing sentences pulled it in is an implementation
        # detail of the search.
        best: dict[str, tuple[str, float, str]] = {}
        for representative in chosen:
            try:
                hits = self.store.search(
                    representative.vector,
                    limit=scan,
                    # **Awards only, inside the store.** Without this the
                    # search is structurally hopeless: an award notice is a
                    # table of bid prices and an open tender is prose about
                    # the work, so document type dominates the similarity and
                    # the nearest four hundred neighbours of a consulting REOI
                    # are four hundred other REOIs. Measured on OP00460945:
                    # 426 neighbours, zero awards, from an archive holding
                    # 13,255 of them.
                    query_filter=self.store.build_filter(
                        is_award=True, exclude={"source_key": source_key}
                    ),
                    score_threshold=settings.RAG["SCORE_THRESHOLD"],
                )
            except QdrantUnavailable as exc:
                logger.info("Award similarity failed for %s: %s", source_key, exc)
                return []

            for hit in hits:
                notice_id = hit.payload.get("notice_id", "")
                if not notice_id:
                    continue
                score = float(hit.score)
                current = best.get(notice_id)
                if current is None or score > current[1]:
                    best[notice_id] = (
                        notice_id,
                        score,
                        hit.payload.get("content", ""),
                    )

        # **Not truncated here.** The first version cut this to eight times the
        # panel's row limit, and the caller then discarded every candidate that
        # was not an award with a named winner — so a tender whose nearest
        # forty neighbours were all open notices got an empty panel while its
        # award neighbours sat at rank forty-one. The join decides how many it
        # needs; this returns what it found.
        return sorted(best.values(), key=lambda row: row[1], reverse=True)


_service: SimilarityService | None = None


def get_similarity_service() -> SimilarityService:
    global _service
    if _service is None:
        _service = SimilarityService()
    return _service


def reset_similarity_service() -> None:
    global _service
    _service = None


def _title_words(text: str) -> set[str]:
    """The words of a title worth matching on, folded and stripped of grammar."""
    return {
        word for word in _WORD_RE.findall((text or "").casefold())
        if word not in _TITLE_STOPWORDS
    }


def _title_of(points: list[dict[str, Any]]) -> str:
    """The title carried on the chunks themselves.

    Every payload holds the notice's title (``SourceRef.title``), so the caller
    does not have to pass one and a direct ``representative()`` call still gets
    topicality. Read from the store rather than the database because this
    module has no Django models in it and is not about to grow one.
    """
    for point in points:
        title = (point.get("payload") or {}).get("title")
        if title:
            return str(title)
    return ""
