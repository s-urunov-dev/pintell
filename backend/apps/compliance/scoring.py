"""How ready a bid is, as one number — weighted by what each criterion decides.

A vendor working down a page of criteria wants to know how far they have got.
The obvious answer is the fraction they have satisfied, and it is the wrong one:
a tender with nine formalities and one turnover gate is not 90% met by a bidder
who fails the gate, and a page that told them so would be actively misleading at
the only moment it mattered.

So the fraction is over **weight**, not over rows. Every criterion carries an
``importance`` the extraction read off the document itself (see
``models.TenderRequirement.Importance``), and a criterion that decides
eligibility counts for more of the bar than one the document calls desirable.

Three properties this module is built to keep, each of which rejects a simpler
version:

* **A satisfied criterion is the only thing that fills the bar.** An unknown
  does not, and neither does the vendor merely having answered. The number
  reports what has been *established*, so it can only be read one way.

* **What is still open is reported separately, never folded in.** ``ceiling`` is
  the score a vendor would reach by satisfying everything not yet settled. The
  gap between ``score`` and ``ceiling`` is the work left; the gap between
  ``ceiling`` and 1.0 is what has already been lost. A single number cannot say
  both, and a bar that showed only the first would let a bidder with a failed
  mandatory criterion watch it fill to 100%.

* **The percentage never overrides the verdict.** ``blocked`` says a mandatory
  criterion has actually failed. A high percentage beside a failed hard gate is
  not a contradiction to be smoothed away — it is a bidder who meets most of
  what is asked and cannot bid — and the interface has to be able to say both,
  so both are in the payload.

No Django here, for the same reason as ``expressions.py`` and ``reporting.py``:
the arithmetic behind a number shown to a vendor should be testable without a
database, and reproducible by hand from the same inputs.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Mapping

#: The levels an extraction may return, in decreasing order of what they decide.
#: The single source of truth: ``llm`` builds its schema ``enum`` from this and
#: ``models.TenderRequirement.Importance`` mirrors it, so a level cannot be
#: added to the prompt without a weight existing for it.
IMPORTANCE_LEVELS: tuple[str, ...] = ("high", "medium", "low")

#: What an unjudged criterion counts as.
#:
#: L1 leaves ``importance`` blank because its rules match a number beside a
#: topic word and read none of the language that would say whether the criterion
#: gates the bid (``models.TenderRequirement.Importance.UNSET``). Rows extracted
#: before the field existed are blank for the same reason: nobody judged them.
#: Both are counted as ``medium`` — the value the prompt itself calls the
#: default — and the fallback lives here, in one place, rather than being
#: guessed at by each layer.
DEFAULT_LEVEL = "medium"

#: What one criterion at each level is worth.
#:
#: **These ratios are a product decision, not a fact about procurement.** No
#: regulation assigns weights to qualification criteria, and inventing a source
#: for these would be exactly the kind of confident fabrication this codebase
#: refuses elsewhere. They are set here, in one table, so the choice is visible
#: and can be changed by argument rather than discovered in a formula.
#:
#: What they were chosen for is a pair of properties:
#:
#: * one unmet ``high`` cannot be papered over by answering formalities — at
#:   5:1, five satisfied preferences are needed to offset a single missed gate,
#:   which is more than most tenders contain;
#: * a ``low`` still moves the bar. Weighting preferences at zero would be
#:   defensible arithmetic and a bad interface: a vendor who has answered every
#:   desirable attribute has done real work, and a bar that did not move would
#:   teach them the control does nothing.
#:
#: Medium sits nearer high than low deliberately. It is the default level, so
#: it is what most rows carry, and a default that scored close to "preference"
#: would make the ordinary body of a tender nearly weightless.
WEIGHTS: Mapping[str, int] = {"high": 5, "medium": 3, "low": 1}


def weight_of(importance: str | None) -> int:
    """What one criterion contributes to the bar. Never zero, never raises.

    An unrecognised level lands on the default rather than on zero. Zero would
    silently drop the criterion out of both the numerator and the denominator,
    which is a criterion vanishing from a percentage shown to a vendor — the
    failure mode hardest to notice and worst to explain.
    """
    return WEIGHTS.get((importance or "").strip().lower(), WEIGHTS[DEFAULT_LEVEL])


@dataclass(frozen=True)
class Score:
    """The readiness of one bid against one tender.

    ``score`` and ``ceiling`` are fractions in ``[0, 1]``, not percentages: the
    formatting is the interface's business, and a float that has already been
    rounded to a percentage cannot be re-derived.
    """

    #: Weight satisfied, over total weight. What has been established.
    score: float
    #: What the score would become if everything still unknown were satisfied.
    #: Equal to ``score`` when nothing is open; below 1.0 once anything failed.
    ceiling: float

    #: The three weights the two fractions are built from, exposed so a reader
    #: can check the division rather than take it.
    earned: int
    open: int
    lost: int
    total: int

    #: Rows behind each state, for an interface that wants to say "3 of 11".
    counts: Mapping[str, int]

    #: Weight per importance level: ``{"high": {"earned": 5, "total": 15}, …}``.
    #: This is what lets a page say *which* half is missing — "you have answered
    #: the formalities and none of the gates" is the sentence a bar cannot say.
    by_importance: Mapping[str, Mapping[str, int]]

    #: True when a mandatory criterion has actually failed. Read *before* the
    #: percentage: a bid can be 85% established and impossible.
    blocked: bool

    def as_dict(self) -> dict[str, Any]:
        return {
            "score": round(self.score, 4),
            "ceiling": round(self.ceiling, 4),
            "earned": self.earned,
            "open": self.open,
            "lost": self.lost,
            "total": self.total,
            "counts": dict(self.counts),
            "by_importance": {
                level: dict(weights) for level, weights in self.by_importance.items()
            },
            "blocked": self.blocked,
        }


#: What a scored row has to tell us. A tuple rather than a class because the two
#: callers already hold these values side by side — the view has the stored row
#: and the engine's verdict — and a wrapper type would only be built to be
#: unpacked again.
#:
#: ``(importance, verdict, is_mandatory)``, where ``verdict`` is one of
#: ``satisfied`` / ``failed`` / ``unknown`` — ``expressions.Verdict``'s own
#: values, passed as strings so this module stays independent of the engine's
#: enum and can be exercised from a test with three literals.
Row = tuple[str, str, bool]


def score_rows(rows: Iterable[Row]) -> Score:
    """Weigh a set of assessed criteria into one readiness figure.

    An empty set scores 0 with a ceiling of 0, not 1. The vacuous-truth answer
    would be arithmetically defensible — every criterion of none is satisfied —
    and it would put "100% ready" on a tender nobody has read, which is the
    single most misleading thing this page could display. Callers distinguish
    the case by ``total == 0``, and the assessment already carries ``unrated``
    for it.
    """
    earned = 0
    open_weight = 0
    lost = 0
    blocked = False
    counts = {"satisfied": 0, "failed": 0, "unknown": 0, "total": 0}
    by_importance: dict[str, dict[str, int]] = {
        level: {"earned": 0, "total": 0, "count": 0} for level in IMPORTANCE_LEVELS
    }

    for importance, verdict, is_mandatory in rows:
        weight = weight_of(importance)
        level = (importance or "").strip().lower()
        if level not in by_importance:
            level = DEFAULT_LEVEL

        counts["total"] += 1
        by_importance[level]["total"] += weight
        by_importance[level]["count"] += 1

        if verdict == "satisfied":
            earned += weight
            counts["satisfied"] += 1
            by_importance[level]["earned"] += weight
        elif verdict == "failed":
            lost += weight
            counts["failed"] += 1
            # Only a *mandatory* failure blocks. A failed preference is a real
            # verdict worth showing and does not stop anyone bidding, and
            # conflating the two would put a red bar on a bid that is fine.
            blocked = blocked or is_mandatory
        else:
            open_weight += weight
            counts["unknown"] += 1

    total = earned + open_weight + lost
    return Score(
        score=earned / total if total else 0.0,
        ceiling=(earned + open_weight) / total if total else 0.0,
        earned=earned,
        open=open_weight,
        lost=lost,
        total=total,
        counts=counts,
        by_importance=by_importance,
        blocked=blocked,
    )
