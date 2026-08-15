"""Merging two result lists that do not share a scale.

``search.py`` states the rule this module has to answer to: *the two scores are
not comparable and the API must never pretend they are.* A cosine similarity
and a ``ts_rank`` are different quantities, and averaging or thresholding them
together produces an ordering that means nothing.

**Reciprocal Rank Fusion does not break that rule; it is the reason the rule
can be relaxed.** RRF never reads either score. It reads each hit's *position*
in its own list — first, second, third — and sums ``1 / (k + rank)`` across the
lists a hit appears in. Position is a quantity both retrieval paths genuinely
produce, on one scale, with one meaning: "this arm ranked it above that one".
Nothing about a passage's cosine value survives into the fused ordering.

So a fused list is honest where a score-merged list is not, and the module
docstring's argument stands unchanged for anyone who tries to average again.

**What fusion buys, concretely.** A dense arm alone cannot find `TRIP-CS-01`:
an identifier has no semantic neighbourhood, so the nearest vectors are
paragraphs *about* consulting selection and the notice the reader named is not
among them. A lexical arm alone cannot answer "what turnover do IT tenders
want" — the corpus rarely uses the reader's words. Each arm's failure is the
other's ordinary case, and RRF is what lets a hit that one arm ranked first and
the other missed entirely still reach the top.

**The fused score is a fusion score and says so.** It is not a similarity, and
the hit carries the ranks it was computed from (``rank_dense``, ``rank_lexical``)
so an ordering can be reproduced by hand — the same standard D42/D45 hold every
other ranking in this product to. A client that renders the number as a
confidence is misreading it, which is why ``retrieval`` says ``hybrid``.
"""

from __future__ import annotations

from typing import Any, Iterable, Sequence

from .qdrant import SearchHit

#: What makes two hits the same passage.
#:
#: The chunk's own coordinates, not its text: the same sentence can appear in
#: two notices from one template, and fusing those into one hit would hide a
#: source the reader is entitled to. ``source_key`` names the document and
#: ``position_id`` the block inside it — the pair a citation badge opens.
def identity(hit: SearchHit) -> tuple[str, str]:
    payload = hit.payload
    return (
        str(payload.get("source_key") or payload.get("notice_id") or ""),
        str(payload.get("position_id") or ""),
    )


def reciprocal_rank_fusion(
    runs: Sequence[tuple[str, Sequence[SearchHit]]],
    *,
    k: int = 60,
    limit: int = 20,
) -> list[SearchHit]:
    """Fuse named ranked lists into one, best first.

    ``runs`` is ``(name, hits)`` pairs — the name is written onto each hit as
    ``rank_<name>`` so the fused order can be read back. A hit missing from a
    run simply contributes nothing from it; there is no penalty term and no
    imputed rank, because "this arm did not return it" is not evidence that the
    arm ranked it last.

    ``k`` smooths the head of the distribution: with ``k = 60`` the gap between
    first and second place is small enough that agreement across arms outweighs
    a single arm's confidence, which is the entire point of fusing. It is the
    constant the original paper measured, and it is left alone until a gold set
    says otherwise.

    Ties keep the order of the first run that produced them, because Python's
    sort is stable and the runs are visited in the order given — so passing the
    dense arm first means an exact tie reads as the dense arm's ordering rather
    than an arbitrary one.
    """
    fused: dict[tuple[str, str], float] = {}
    kept: dict[tuple[str, str], SearchHit] = {}
    ranks: dict[tuple[str, str], dict[str, int]] = {}

    for name, hits in runs:
        for position, hit in enumerate(hits, start=1):
            key = identity(hit)
            fused[key] = fused.get(key, 0.0) + 1.0 / (k + position)
            ranks.setdefault(key, {})[name] = position
            # The first run to produce a passage owns its payload. The dense
            # arm is passed first and carries the chunk exactly as it was
            # indexed; the lexical arm reconstructs the same offsets through
            # the same `ExtractionService`, so either is renderable — but
            # mixing them per hit would make the payload depend on which arm
            # happened to be quicker.
            kept.setdefault(key, hit)

    order = sorted(fused.items(), key=lambda item: item[1], reverse=True)

    results: list[SearchHit] = []
    for key, score in order[:limit]:
        hit = kept[key]
        payload = dict(hit.payload)
        for name, position in ranks[key].items():
            payload[f"rank_{name}"] = position
        results.append(
            SearchHit(
                score=score,
                payload=payload,
                # `hybrid` when both arms found it, otherwise the arm that did.
                # Named rather than averaged: a reader told "keyword match" for
                # a passage only the lexical arm returned knows what kind of
                # warrant they have, which is the same contract the fallback
                # already holds.
                retrieval="hybrid" if len(ranks[key]) > 1 else hit.retrieval,
            )
        )
    return results


def dedupe(hits: Iterable[SearchHit]) -> list[SearchHit]:
    """One hit per passage, keeping the first. Order preserved.

    Both arms are capable of returning the same chunk twice — the lexical arm
    once per matching notice, the dense arm never, but a caller concatenating
    runs can. Fusion assumes a *ranked list*, and a list with a repeat has two
    positions for one passage and would count it twice.
    """
    seen: set[tuple[str, str]] = set()
    unique: list[SearchHit] = []
    for hit in hits:
        key = identity(hit)
        if key in seen:
            continue
        seen.add(key)
        unique.append(hit)
    return unique


def as_dict(hits: Sequence[SearchHit]) -> list[dict[str, Any]]:
    """The hits as the API returns them. Here so callers need not import both."""
    return [hit.as_dict() for hit in hits]
