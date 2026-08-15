"""An assessment as JSON: the verdict, and everything needed to re-check it.

The product's claim is that a verdict is *verifiable* — a bidder, or a
reviewer, can recompute it by hand from the same inputs. A response carrying
only ``{"eligible": false}`` would break that claim however correct the engine
is, so this module serialises the whole evaluation: every requirement, the
quote it came from, the threshold it was compared against, and the trace tree
that produced the verdict.

Two things are added here that the engine deliberately does not carry.

**What is missing.** ``UNKNOWN`` is the verdict that asks a question, and the
question is only useful with the name of the value that would answer it. That
is an interface concern rather than a decision concern, so the walk that
collects it lives here: ``expressions.py``'s job ends at the verdict.

**Nothing is coerced.** ``hard_eligibility_pass`` goes out as ``true``,
``false`` or ``null``. The third state is the point of DECISIONS.md D3, and a
JSON ``false`` in its place would tell a bidder they had been rejected when
nobody had decided anything. For the same reason an ``UNKNOWN`` requirement is
never counted among the failures at any level of this payload.

Django is not imported here, for the reason ``expressions.py`` does not import
it: the payload is a pure function of the assessment, so it can be tested
without a database. Everything that comes from a stored row — the layer, the
grounding state, the harvested document — is passed in by the view as
``provenance`` rather than read back off a model here.
"""

from __future__ import annotations

from typing import Any, Iterable, Mapping, Sequence

from . import scoring
from .expressions import (
    All,
    Any_,
    AppliesTo,
    Assessment,
    Bid,
    Count,
    Exists,
    Node,
    Not,
    Portfolio,
    Requirement,
    Result,
    Scalar,
    Verdict,
)


# ---------------------------------------------------------------------------
# What the bidder has not told us
# ---------------------------------------------------------------------------
def missing_inputs(requirement: Requirement, bid: Bid) -> list[dict[str, Any]]:
    """The declarations that would let an unknown requirement be decided.

    Read against the same portfolio the requirement was evaluated against, so
    the question matches the verdict: a ``jv_combined`` criterion asks for the
    joint venture's pooled figure rather than each member's, because pooling is
    what the engine actually did.

    Three kinds come back, and the interface renders each differently:

    * ``scalar`` — a single value was never declared
    * ``collection`` — a whole record type was never declared, which is *not*
      the same as declaring none of them (see ``Portfolio.declares``)
    * ``record_field`` — records exist, but some are blank in the field this
      requirement filters on, which is why a shortfall came back as unknown
      instead of as a refusal
    """
    if requirement.applies_to is AppliesTo.SINGLE or not bid.is_joint_venture:
        portfolios: Sequence[Portfolio] = (bid.members[0],)
    elif requirement.applies_to is AppliesTo.JV_COMBINED:
        portfolios = (bid.combined(),)
    else:
        portfolios = tuple(bid.members)

    found: list[dict[str, Any]] = []
    for portfolio in portfolios:
        _walk(requirement.expression, portfolio, found)
    return _dedupe(found)


def _walk(node: Node, portfolio: Portfolio, out: list[dict[str, Any]]) -> None:
    """Collect the undeclared inputs one node depends on.

    Every branch is walked, including branches that already have an answer. A
    disjunction that came back unknown may be answerable through either arm,
    and choosing which arm to put to the bidder would be the interface making a
    decision the engine did not make.
    """
    if isinstance(node, Scalar):
        if portfolio.scalar(node.key) is None:
            out.append(
                {
                    "kind": "scalar",
                    "key": node.key,
                    "label": node.label,
                    "unit": node.unit,
                }
            )
        return

    if isinstance(node, (Count, Exists)):
        if not portfolio.declares(node.entity):
            out.append(
                {"kind": "collection", "entity": node.entity, "label": node.label}
            )
            return
        # Records exist, so name the fields that are blank on at least one of
        # them: those are exactly the records the engine could not read, and
        # the reason a count short of its target is unknown rather than failed.
        items = portfolio.items(node.entity)
        for field in sorted({f.field for f in node.where}):
            if any(item.get(field) is None for item in items):
                out.append(
                    {
                        "kind": "record_field",
                        "entity": node.entity,
                        "field": field,
                        "label": node.label,
                    }
                )
        return

    if isinstance(node, Not):
        _walk(node.child, portfolio, out)
        return

    if isinstance(node, (All, Any_)):
        for child in node.children:
            _walk(child, portfolio, out)


def _dedupe(entries: Iterable[Mapping[str, Any]]) -> list[dict[str, Any]]:
    """Drop repeats, keeping the order they were discovered in."""
    seen: set[tuple] = set()
    unique: list[dict[str, Any]] = []
    for entry in entries:
        signature = tuple(sorted(entry.items()))
        if signature not in seen:
            seen.add(signature)
            unique.append(dict(entry))
    return unique


# ---------------------------------------------------------------------------
# Payloads
# ---------------------------------------------------------------------------
def requirement_payload(
    requirement: Requirement,
    result: Result,
    bid: Bid,
    provenance: Mapping[str, Any] | None = None,
    *,
    declared: bool | None = None,
) -> dict[str, Any]:
    """One requirement, its verdict, and the working behind it.

    ``expression`` is sent as well as ``trace``. The trace says what happened;
    the expression says what was asked, in a form a reader can check the trace
    against. Sending only one of the two leaves a verdict that has to be taken
    on trust.

    **``provenance`` is merged last and may therefore override ``label``.** That
    is intended and is the one key where it happens: the engine's requirement
    carries the English label it was parsed with, while the stored row carries
    the same label in three languages and the view resolves the reader's one
    (``views._parse``). The engine has no business knowing what language its
    caller reads in, so the substitution happens here, at the edge.
    """
    return {
        "key": requirement.key,
        "label": requirement.label or requirement.key,
        # Blank unless the view supplied it through provenance, for the reason
        # above: importance is a property of the stored row, not of the parsed
        # expression. The interface reads a blank as "not judged"; the scoring
        # weighs it as medium (`scoring.DEFAULT_LEVEL`).
        "importance": "",
        "verdict": result.verdict.value,
        # The vendor's own answer, and therefore the switch's position when the
        # page renders. `None` is "not answered" — distinct from `False`, which
        # is a statement the vendor made.
        "declared": declared,
        # Which side produced the verdict. The interface has to be able to say
        # "because you told us" rather than presenting a self-declaration as
        # though it were computed from evidence.
        "decided_by": "declaration" if declared is not None else "engine",
        "is_mandatory": requirement.is_mandatory,
        "applies_to": requirement.applies_to.value,
        # Verbatim from the source, never paraphrased — D4. Never empty: with
        # no L0 (D17) every layer reads a document we hold, so every row can
        # name the sentence it came from.
        "evidence_quote": requirement.evidence_quote,
        "source": requirement.source,
        "expression": requirement.expression.to_dict(),
        "trace": result.trace.as_dict(),
        # The same trace flattened to indented text. The frontend could derive
        # this itself, but then two implementations of the explanation would
        # exist and could disagree — and the deterministic explanation is the
        # product claim, not a convenience.
        "explanation": result.explanation,
        # Populated for UNKNOWN only. On a satisfied or failed requirement the
        # question has been answered, and listing inputs there would read as
        # "supply these and the verdict changes" — which for a failure is a lie.
        "missing": (
            missing_inputs(requirement, bid)
            if result.verdict is Verdict.UNKNOWN
            else []
        ),
        **dict(provenance or {}),
    }


def assessment_payload(
    assessment: Assessment,
    bid: Bid,
    *,
    provenance: Sequence[Mapping[str, Any]] = (),
    excluded: Mapping[str, Any] | None = None,
    declarations: Sequence[bool | None] = (),
) -> dict[str, Any]:
    """The whole assessment, ready to render.

    ``provenance`` is positional: ``assess`` preserves the order of the
    requirements it was given, so entry *i* belongs to requirement *i*. Keying
    it by ``requirement.key`` instead would be wrong — the same key can be
    produced by two extraction layers, and those two rows differ in exactly the
    fields provenance carries.
    """
    verdicts = [result.verdict for _, result in assessment.results]

    # Weighed over the same rows, in the same order, from the same provenance
    # the requirement payloads are built from — so the bar at the top of the
    # page and the cards under it can never be describing different sets.
    score = scoring.score_rows(
        (
            str((provenance[index] if index < len(provenance) else {}).get("importance", "")),
            result.verdict.value,
            requirement.is_mandatory,
        )
        for index, (requirement, result) in enumerate(assessment.results)
    )

    return {
        # The four surface states (D3). ``unrated`` is the one that matters for
        # honesty: nothing was extracted for this notice, so no claim about the
        # bidder has been made at all — and the boolean below is vacuous.
        "status": assessment.status,
        # True / False / None, passed straight through. Null is a state, not a
        # missing field; see the module docstring.
        "hard_eligibility_pass": assessment.hard_eligibility_pass,
        "coverage": assessment.coverage,
        # How much of what this tender demands the bidder has established,
        # weighted by what each criterion decides (``scoring``). Beside
        # ``hard_eligibility_pass``, never instead of it: the percentage is a
        # summary of verdicts and the boolean is the verdict that stops a bid.
        "score": score.as_dict(),
        "explanation": assessment.explanation(),
        "counts": {
            "total": len(assessment.results),
            "satisfied": verdicts.count(Verdict.SATISFIED),
            "failed": verdicts.count(Verdict.FAILED),
            "unknown": verdicts.count(Verdict.UNKNOWN),
            # Mandatory failures — the ones that actually block a bid. A failed
            # preference is worth showing and must not be counted here.
            "blockers": len(assessment.blockers),
        },
        "is_joint_venture": bid.is_joint_venture,
        "requirements": [
            requirement_payload(
                requirement,
                result,
                bid,
                provenance[index] if index < len(provenance) else None,
                declared=(
                    declarations[index] if index < len(declarations) else None
                ),
            )
            for index, (requirement, result) in enumerate(assessment.results)
        ],
        # Rows that existed but took no part, with the reason. Withholding them
        # silently would make a thin assessment look like a complete one, and
        # the ``not_found`` count is how the hallucination rate stays visible in
        # the product rather than only in the evaluation harness.
        "excluded": dict(excluded or {}),
    }
