"""Who a consulting notice is addressed to: a firm, or one named person.

This is a different question from `subcategories.py`, and keeping them in
separate columns is the point. The sub-direction says *what* the work is
(audit, supervision, environmental); this says *who may answer*. A consulting
firm and a freelance specialist reading the same "IT advisory" tag want
opposite halves of it, and folding the two axes into one field would make the
combination unaskable — you could no longer request "individual IT advisory"
without inventing a sub-direction per audience.

Where the answer comes from
---------------------------
Not from keywords, and not from what anyone remembers about the Procurement
Regulations. Upstream already states it: the selection method arrives with its
own name, and one of them is spelled `Individual Consultant Selection`. So the
individual side is read off `procurement_method_name` — a fact in the mirror,
quotable, and robust to a new code appearing.

The firm side has no such sentence anywhere in the data. That every *other*
consultant-selection method selects a firm is a claim about the Regulations,
which this codebase does not write from memory — see `_FIRM_METHODS` and
docs/OPEN-QUESTIONS.md Q18.
"""

from __future__ import annotations

from dataclasses import dataclass

from django.db import models

from .categories import TenderCategory


class ConsultingAudience(models.TextChoices):
    FIRM = "firm", "Consulting firms"
    INDIVIDUAL = "individual", "Individual consultants"
    UNKNOWN = "", "Not determined"


# The one signal upstream states in words. Matched on the method *name* rather
# than the `INDV` code so that a differently-coded individual selection is
# still recognised — the name is what carries the meaning.
_INDIVIDUAL_MARKER = "individual"

# NEEDS_VERIFICATION — awaiting docs/OPEN-QUESTIONS.md Q18.
#
# These are the remaining consultant-selection codes present in the mirror.
# Reading them as "selects a firm" is an inference from their names, not
# something upstream or the Regulations say in this data, so it is written
# here as a reviewable list rather than buried in a condition. Until the COO
# confirms it, a wrong entry mislabels an audience; it can never change a
# compliance verdict, which is why this ships rather than blocking.
_FIRM_METHODS: frozenset[str] = frozenset({
    "CQS",   # Consultant Qualification Selection
    "QCBS",  # Quality And Cost-Based Selection
    "QBS",   # Quality Based Selection
    "LCS",   # Least Cost Selection
    "FBS",   # Fixed Budget Selection
})

# Deliberately in neither set. `CDS`/`DIR` (Direct Selection) is available for
# both audiences, and `UN`/`NPO` engage an organisation that is not a vendor in
# this product's sense. Guessing here would fill the column at the cost of
# making it untrue, and an empty value is answerable later.
_AMBIGUOUS_METHODS: frozenset[str] = frozenset({"CDS", "DIR", "UN", "NPO"})


@dataclass(slots=True)
class AudienceGuess:
    audience: str
    rationale: str = ""


def classify_audience(
    *,
    category: str,
    procurement_method_code: str = "",
    procurement_method_name: str = "",
) -> AudienceGuess:
    """Decide whether a consulting notice is for firms or for individuals.

    Returns an empty audience for every non-consulting direction: goods and
    works are procured from firms by definition, so the column would carry no
    information there, and a populated field invites a filter that silently
    excludes nothing.
    """
    if category != TenderCategory.CONSULTING:
        return AudienceGuess(ConsultingAudience.UNKNOWN, "Not consulting.")

    name = (procurement_method_name or "").strip().lower()
    code = (procurement_method_code or "").strip().upper()

    if _INDIVIDUAL_MARKER in name:
        return AudienceGuess(
            ConsultingAudience.INDIVIDUAL, f"method name: {procurement_method_name}"
        )

    if code in _FIRM_METHODS:
        return AudienceGuess(ConsultingAudience.FIRM, f"method code: {code}")

    if code in _AMBIGUOUS_METHODS:
        return AudienceGuess(
            ConsultingAudience.UNKNOWN, f"{code} is open to both audiences"
        )

    return AudienceGuess(ConsultingAudience.UNKNOWN, "No method signal.")
