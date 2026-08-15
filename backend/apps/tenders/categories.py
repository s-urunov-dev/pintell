"""Tender direction categories and the rule-based classifier.

A company picks its direction once (Construction, Consulting, Supply, …) and
then follows only tenders in that category. Two classifiers produce it:

* :func:`classify_by_rules` — deterministic keyword scoring. Free, instant,
  offline, and correct on the large majority of notices because World Bank
  descriptions are formulaic ("Procurement of …", "Recruitment of …").
* ``services/ai/classification.py`` — reads the notice body with Claude for the
  cases rules cannot settle. Optional; falls back to the rules on any error.

Keeping the rules as the baseline means the feature works with no API key and
the AI is an accuracy upgrade rather than a hard dependency.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.db import models

from .keywords import contains


class TenderCategory(models.TextChoices):
    CONSTRUCTION = "construction", "Construction & works"
    CONSULTING = "consulting", "Consulting services"
    SUPPLY = "supply", "Supply of goods"
    SERVICES = "services", "Non-consulting services"
    IT = "it", "IT & digital"
    OTHER = "other", "Other"
    UNKNOWN = "unknown", "Not classified"


class CategorySource(models.TextChoices):
    RULES = "rules", "Rule-based"
    AI = "ai", "AI (Claude)"
    MANUAL = "manual", "Manual override"
    # A model read the notice and decided, but not through `_classify_with_claude`
    # — no JSON schema, no calibrated confidence, no metered per-notice call.
    # It is its own value rather than `ai` or `manual` because both would be
    # false: nobody can audit an `ai` row by looking for its API call, and no
    # human made a `manual` decision. See `review_categories`.
    AGENT = "agent", "Agent review"


# Weighted keywords per category. Longer, more specific phrases score higher so
# that "supply and installation of a water treatment plant" lands on supply
# rather than construction.
_KEYWORDS: dict[str, tuple[tuple[str, int], ...]] = {
    TenderCategory.CONSTRUCTION: (
        ("construction of", 6), ("civil works", 6), ("rehabilitation of", 5),
        ("reconstruction", 5), ("road works", 5), ("earthworks", 4),
        ("building", 3), ("bridge", 3), ("pipeline", 3), ("drilling", 3),
        ("installation works", 4), ("repair works", 4), ("infrastructure", 2),
        ("works contract", 5), ("turnkey", 3), ("irrigation", 2),
    ),
    TenderCategory.CONSULTING: (
        ("consultancy", 6), ("consulting serv", 6), ("consultant", 5),
        ("recruitment of", 5), ("individual consultant", 6), ("advisory", 4),
        ("feasibility study", 5), ("technical assistance", 5), ("audit", 4),
        ("design services", 4), ("supervision services", 5), ("training", 3),
        ("study", 2), ("assessment", 3), ("expression of interest", 2),
        ("capacity building", 3), ("survey", 2), ("evaluation", 2),
    ),
    TenderCategory.SUPPLY: (
        ("procurement of", 5), ("supply of", 6), ("supply and delivery", 6),
        ("purchase of", 5), ("goods", 3), ("equipment", 4), ("furniture", 4),
        ("vehicles", 4), ("medical equipment", 5), ("laboratory equipment", 5),
        ("spare parts", 4), ("materials", 3), ("machinery", 4),
        ("delivery of", 3),
    ),
    TenderCategory.IT: (
        ("software", 5), ("information technology", 5), ("it equipment", 5),
        ("computer", 4), ("hardware", 3), ("network", 3), ("data center", 5),
        ("digital", 3), ("erp", 4), ("management information system", 5),
        ("web portal", 4), ("cyber", 3), ("telecommunication", 4),
    ),
    TenderCategory.SERVICES: (
        ("non-consulting", 6), ("catering", 4), ("cleaning", 4),
        ("security services", 5), ("logistics", 3), ("transportation serv", 4),
        ("maintenance serv", 4), ("insurance", 4), ("printing", 3),
        ("hotel", 3), ("event", 2),
    ),
}

# Procurement method codes carry strong signal on their own: the World Bank
# groups consultant selection methods separately from goods and works.
_METHOD_HINTS: dict[str, str] = {
    "QCBS": TenderCategory.CONSULTING,
    "QBS": TenderCategory.CONSULTING,
    "CQS": TenderCategory.CONSULTING,
    "LCS": TenderCategory.CONSULTING,
    "FBS": TenderCategory.CONSULTING,
    "INDV": TenderCategory.CONSULTING,
    "SSS": TenderCategory.CONSULTING,
}

# Procurement group codes: CS = consulting services, GO = goods, CW = works.
# NEEDS_VERIFICATION — the expansions are read off the code names, not out of
# the Procurement Regulations. Asked as Q19.
_GROUP_HINTS: dict[str, str] = {
    "CS": TenderCategory.CONSULTING,
    "GO": TenderCategory.SUPPLY,
    "CW": TenderCategory.CONSTRUCTION,
    "NC": TenderCategory.SERVICES,
}

# The three codes the keyword table is not allowed to overrule, and the reason
# they are only three.
#
# The group comes from the source; the keyword score is our own guess. Measured
# over the 20,795 mirrored notices carrying both, the two agree on 98% of CS,
# 96% of GO and 96% of CW — so where they disagree, the guess is what is
# usually wrong. `OP00460089` is the case that prompted this: pump supply and
# installation, group `CW`, method "Request for Bids", classified `consulting`
# because *energy audit* appears in its description, which then put it at the
# top of a real audit tender's competitor panel.
#
# **`NC` is deliberately absent.** On the same measurement it agrees with the
# keywords on 49% — worse than a coin flip — so `NC → services` is more likely
# a wrong mapping than a reliable code. It stays a +6 hint until Q19 answers
# what the code actually means.
_GROUP_DECIDES: dict[str, str] = {
    "CS": TenderCategory.CONSULTING,
    "GO": TenderCategory.SUPPLY,
    "CW": TenderCategory.CONSTRUCTION,
}

_WORD_RE = re.compile(r"[^a-z0-9]+")


@dataclass(slots=True)
class CategoryGuess:
    category: str
    confidence: float
    source: str
    rationale: str = ""


def classify_by_rules(
    *,
    description: str = "",
    project_name: str = "",
    notice_text: str = "",
    procurement_method_code: str = "",
    procurement_group: str = "",
) -> CategoryGuess:
    """Score the notice against the keyword table.

    ``description`` dominates because it is the notice's own title; the body is
    included at a lower weight so a long document cannot outvote it.
    """
    haystack_primary = _normalise(f"{description} {project_name}")
    haystack_body = _normalise(notice_text)[:4000]

    scores: dict[str, int] = {}
    for category, keywords in _KEYWORDS.items():
        score = 0
        for phrase, weight in keywords:
            # `contains`, not `in`: a substring test read *enterprise* as ERP
            # and *prevention* as an event. See `keywords.py` for the counts.
            if contains(phrase, haystack_primary):
                score += weight * 2
            elif contains(phrase, haystack_body):
                score += weight
        if score:
            scores[category] = score

    method = (procurement_method_code or "").strip().upper()
    if method in _METHOD_HINTS:
        scores[_METHOD_HINTS[method]] = scores.get(_METHOD_HINTS[method], 0) + 8

    group = (procurement_group or "").strip().upper()
    if group in _GROUP_HINTS:
        scores[_GROUP_HINTS[group]] = scores.get(_GROUP_HINTS[group], 0) + 6

    if not scores:
        return CategoryGuess(TenderCategory.UNKNOWN, 0.0, CategorySource.RULES,
                             "No keyword or method signal matched.")

    winner, _ = max(scores.items(), key=lambda item: item[1])
    best = _settle_with_group(winner, group)

    best_score = scores.get(best, 0)
    total = sum(scores.values())
    # Confidence is the winner's share of all signal, damped by how much signal
    # there was at all: two weak hits should not read as certainty. Read off
    # the category actually returned, so an overruled guess reports the
    # confidence of what was chosen and not of what was rejected.
    share = best_score / total
    strength = min(1.0, best_score / 18)
    confidence = round(share * strength, 3)

    runner_up = sorted(scores.items(), key=lambda item: -item[1])[1:2]
    rationale = f"score={best_score}"
    if runner_up:
        rationale += f", runner-up {runner_up[0][0]}={runner_up[0][1]}"
    if best != winner:
        rationale += f", group {group} overruled {winner}"

    return CategoryGuess(best, confidence, CategorySource.RULES, rationale)


def _settle_with_group(winner: str, group: str) -> str:
    """The direction the procurement group allows, where it can speak at all.

    It can only speak about the three directions it codes for. `it` is left
    alone on purpose: computer equipment really is procured as goods, and
    forcing every `GO` notice to `supply` would empty a direction vendors
    subscribe to of the 223 notices that belong in it. The same holds for
    `services` and `other` — the code says nothing about them either way.
    """
    decided = _GROUP_DECIDES.get(group)
    if decided is None or winner == decided:
        return winner
    if winner not in set(_GROUP_DECIDES.values()):
        return winner
    return decided


def _normalise(text: str) -> str:
    return _WORD_RE.sub(" ", (text or "").lower())
