"""Does the quote an extraction claims actually appear in what it read?

Every layer hands back requirements carrying an ``evidence_quote``. This module
is the only thing that decides whether that quote is real, and the reason it is
a separate module rather than three lines inside the pipeline is that its output
is a **published number**: the grounding rate is a column of the DECISIONS.md D6
ablation table, and the headline claim of the whole system — "no assertion
without a verifiable quote" — is exactly what this function measures.

Three consequences shape it.

**A failed check is data, not an error.** A quote that cannot be found is the
hallucination signal. The requirement is still written to the database, marked
``not_found`` and excluded from every verdict by
``TenderRequirement.is_usable``. Discarding it instead would leave the model's
mistakes uncounted, which is the one outcome that makes the accuracy claim
unfalsifiable.

**Every layer can be grounded, and that is now by construction.** An earlier
design had a layer (L0) whose evidence was a clause of the Procurement
Regulations rather than a sentence of the tender, which needed an ``exempt``
state meaning "the question does not apply". Dropping that layer (DECISIONS.md
D17) removed the only rows this module could not check, and the state went with
it: every remaining layer reads a document we hold, so every claim it makes is
answerable against the text it came from. A state that opts a row out of the
measurement is worth having only when something genuinely cannot be measured.

**No Django here.** The module holds plain strings whose values match
``TenderRequirement.Grounding``; the enum lives in ``models.py`` and importing it
would drag a database into a function that is pure string comparison. The strings
are asserted equal to the enum in the tests, so the two cannot drift.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass

from .text import contains_quote

#: Mirrors ``TenderRequirement.Grounding`` — see the module docstring for why
#: these are literals rather than the enum.
VERIFIED = "verified"
NOT_FOUND = "not_found"
UNCHECKED = "unchecked"

#: Kept as an explicit empty set rather than deleted. ``verify`` still takes a
#: ``layer``, and a future layer whose evidence is not in a document we hold
#: would be added here — but adding one means arguing for a claim nobody can
#: check, and the empty set is the record that no such layer currently exists.
EXEMPT_LAYERS: frozenset[str] = frozenset()


def verify(quote: str | None, source: str | None, *, layer: str) -> str:
    """The grounding state of one extracted requirement.

    ``layer`` decides *whether* the question applies at all; ``quote`` and
    ``source`` decide the answer when it does. Both of the degenerate inputs
    resolve to ``not_found`` rather than to ``unchecked``, and that is
    deliberate in both cases:

    * **An empty quote** is already a contract violation — ``extraction.py``
      requires a verbatim quote for every proposal, so a layer that returns none
      has produced something no reader can check.
    * **An empty source** means nothing can be confirmed. ``unchecked`` would be
      the literally accurate word, but ``unchecked`` rows count as usable and
      would reach a bidder's verdict unverified. The safe answer and the honest
      answer diverge here, and the safe one wins.
    """
    if layer in EXEMPT_LAYERS:  # empty today — see EXEMPT_LAYERS
        return UNCHECKED
    return VERIFIED if contains_quote(source or "", quote or "") else NOT_FOUND


@dataclass(frozen=True)
class GroundingRate:
    """The counts behind the grounding rate, kept alongside the ratio itself.

    The ratio alone is not reportable: "94%" over eleven requirements and over
    eleven hundred are different claims, and the ablation needs to say which.
    """

    verified: int = 0
    not_found: int = 0
    unchecked: int = 0

    @property
    def total(self) -> int:
        return self.verified + self.not_found + self.unchecked

    @property
    def checked(self) -> int:
        """Rows where the question applied and was answered.

        ``unchecked`` rows are excluded from the denominator rather than
        counted as successes. Counting an unanswered question as a pass would
        mean that a layer which verifies nothing raises the grounding rate,
        which would make the number reward the wrong thing.
        """
        return self.verified + self.not_found

    @property
    def rate(self) -> float | None:
        """Verified share of the rows that could be checked, or ``None``.

        ``None`` rather than ``0.0`` when nothing was checked: an empty corpus
        has no grounding rate, and reporting zero would read as total failure.
        """
        return self.verified / self.checked if self.checked else None

    def as_dict(self) -> dict[str, float | int | None]:
        return {
            "verified": self.verified,
            "not_found": self.not_found,
            "unchecked": self.unchecked,
            "checked": self.checked,
            "total": self.total,
            "rate": round(self.rate, 4) if self.rate is not None else None,
        }


def rate_over(values: Iterable[str]) -> GroundingRate:
    """Tally grounding states — from rows, from a query, from anywhere.

    Takes bare strings rather than model instances so the same function serves
    the management command's aggregate query, a test over a list, and a future
    eval harness reading a JSON dump.
    """
    counts = Counter(str(value) for value in values)
    return GroundingRate(
        verified=counts.get(VERIFIED, 0),
        not_found=counts.get(NOT_FOUND, 0),
        unchecked=counts.get(UNCHECKED, 0),
    )
