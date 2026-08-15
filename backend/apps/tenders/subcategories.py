"""Sub-directions inside Consulting.

"Consulting" is the broadest of the six directions and the least useful on its
own: a firm that supervises road construction and a firm that runs financial
audits both land in it, and neither wants the other's notices. This splits it
into the sub-directions a consulting firm actually specialises in.

Two things shape the rules:

* **The corpus is multilingual.** World Bank notices arrive in English, French,
  Spanish and Portuguese — `recrutement`, `travaux`, `consultoría`, `engenharia`
  are as common as their English equivalents in this data. Rules that only
  match English would silently sort every French notice into "other".
* **Only Consulting is split.** The other five directions are already narrow
  enough to act on, so a sub-direction there would be noise. `classify_sub`
  returns an empty string for them.

Scoring mirrors `categories.py`: weighted phrases, the title counted double
because it is the notice's own summary of itself, and a confidence that falls
away when the winning signal is thin.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from django.db import models

from .categories import TenderCategory
from .keywords import contains


class ConsultingSubcategory(models.TextChoices):
    ENGINEERING = "engineering", "Engineering, design & supervision"
    AUDIT = "audit", "Audit & financial management"
    ENVIRONMENT_SOCIAL = "environment_social", "Environmental & social"
    TRAINING = "training", "Training & capacity building"
    RESEARCH = "research", "Studies, research & evaluation"
    IT_ADVISORY = "it_advisory", "IT & digital advisory"
    LEGAL_PROCUREMENT = "legal_procurement", "Legal & procurement advisory"
    MANAGEMENT = "management", "Project management support"
    OTHER = "other", "Other consulting"
    UNKNOWN = "", "Not sub-classified"


# Weighted phrases per sub-direction, in English / French / Spanish. Longer and
# more specific phrases score higher so a passing mention cannot outvote the
# subject of the contract.
_KEYWORDS: dict[str, tuple[tuple[str, int], ...]] = {
    ConsultingSubcategory.ENGINEERING: (
        ("supervision of works", 8), ("construction supervision", 8),
        ("works supervision", 8), ("design and supervision", 8),
        ("detailed design", 7), ("engineering design", 7), ("feasibility design", 6),
        ("surveillance des travaux", 8), ("supervision des travaux", 8),
        ("etudes techniques", 7), ("maitrise d oeuvre", 8),
        ("supervision de obras", 8), ("diseno", 5),
        ("controle des travaux", 8), ("surveillance", 5), ("topograph", 6),
        ("engenharia", 6), ("supervisao", 6), ("fiscalizacao de obras", 8),
        ("travaux", 4), ("obras", 4),
        ("supervision", 5), ("engineering", 5), ("architect", 5),
        ("topographic", 4), ("geotechnical", 5), ("structural", 4),
        ("bill of quantities", 5), ("technical design", 6),
    ),
    ConsultingSubcategory.AUDIT: (
        ("external audit", 8), ("financial audit", 8), ("statutory audit", 8),
        ("audit of the financial statements", 9), ("internal audit", 7),
        ("audit externe", 8), ("audit financier", 8), ("auditoria", 8),
        ("audit des etats financiers", 9),
        ("financial management", 6), ("accounting", 5), ("bookkeeping", 5),
        ("audit", 5), ("auditor", 6), ("commissaire aux comptes", 8),
        ("auditoria externa", 8), ("contabilidade", 6),
    ),
    ConsultingSubcategory.ENVIRONMENT_SOCIAL: (
        ("environmental and social impact", 9), ("environmental impact assessment", 9),
        ("resettlement action plan", 9), ("social safeguard", 8),
        ("environmental and social", 7), ("resettlement", 7),
        ("etude d impact environnemental", 9), ("plan de reinstallation", 8),
        ("impacto ambiental", 8),
        ("safeguard", 6), ("environmental", 4), ("gender", 4),
        ("climate", 4), ("biodiversity", 5), ("grievance redress", 6),
    ),
    ConsultingSubcategory.TRAINING: (
        ("capacity building", 8), ("training of trainers", 8),
        ("training programme", 7), ("training program", 7),
        ("renforcement des capacites", 8), ("formation", 5),
        ("capacitacion", 7), ("fortalecimiento de capacidades", 8),
        ("training", 5), ("curriculum", 6), ("workshop", 4),
        ("coaching", 5), ("mentoring", 5), ("e learning", 6),
        ("capacitacao", 7), ("formacao", 6), ("encadrement", 6),
    ),
    ConsultingSubcategory.RESEARCH: (
        ("feasibility study", 8), ("baseline study", 8), ("baseline survey", 8),
        ("impact evaluation", 8), ("monitoring and evaluation", 8),
        ("household survey", 7), ("market study", 7), ("diagnostic study", 7),
        ("etude de faisabilite", 8), ("enquete", 5), ("evaluation d impact", 8),
        ("estudio de factibilidad", 8), ("encuesta", 5),
        ("ligne de base", 7), ("linea de base", 7), ("levantamiento", 6),
        ("research", 5), ("survey", 4), ("assessment", 4), ("study", 4),
        ("data collection", 6), ("analysis", 3),
        ("etude", 4), ("estudo", 4), ("elaboration", 5), ("elaboracao", 5),
        ("diagnostico", 5),
    ),
    ConsultingSubcategory.IT_ADVISORY: (
        ("management information system", 8), ("information system", 6),
        ("software development", 7), ("system integration", 6),
        ("digital transformation", 7), ("it consultancy", 8),
        ("developpement logiciel", 7), ("systeme d information", 7),
        ("database design", 6), ("erp", 5), ("gis", 5),
        ("cybersecurity", 6), ("digital", 4), ("software", 5),
        ("informaticien", 7), ("informatico", 6),
    ),
    ConsultingSubcategory.LEGAL_PROCUREMENT: (
        ("procurement specialist", 8), ("procurement consultant", 8),
        ("legal advisory", 8), ("legal counsel", 7), ("legal expert", 7),
        ("contract management", 6), ("regulatory reform", 6),
        ("specialiste en passation des marches", 9), ("conseil juridique", 7),
        ("especialista en adquisiciones", 9), ("asesoria legal", 8),
        ("procurement agent", 8), ("procurement advisory", 8),
        ("legal advisor", 7), ("legal services", 7), ("regulatory", 5),
    ),
    ConsultingSubcategory.MANAGEMENT: (
        ("project implementation unit", 8), ("implementation support", 7),
        ("project management support", 8), ("project coordinator", 7),
        ("technical assistance", 6), ("institutional strengthening", 7),
        ("appui a la mise en oeuvre", 8), ("unite de gestion de projet", 8),
        ("apoyo a la implementacion", 8),
        ("project manager", 6), ("coordination", 4), ("advisory services", 5),
        ("mise en oeuvre", 6), ("assistance technique", 7),
        ("asistencia tecnica", 7), ("assistencia tecnica", 7),
        ("apoio a ugp", 8), ("gestion de projet", 6),
    ),
}

_WORD_RE = re.compile(r"[^a-z0-9]+")


@dataclass(slots=True)
class SubcategoryGuess:
    subcategory: str
    confidence: float
    rationale: str = ""


def _normalise(text: str) -> str:
    """Lowercase and strip punctuation and accents to one flat token stream.

    Accents are folded so a rule written as `passation des marches` also
    matches `passation des marchés`.
    """
    lowered = (text or "").lower()
    folded = (
        lowered.replace("é", "e").replace("è", "e").replace("ê", "e")
        .replace("à", "a").replace("â", "a").replace("ô", "o")
        .replace("î", "i").replace("ï", "i").replace("û", "u")
        .replace("ç", "c").replace("ñ", "n")
        .replace("œ", "oe").replace("æ", "ae").replace("ã", "a").replace("õ", "o")
        .replace("á", "a").replace("í", "i").replace("ó", "o").replace("ú", "u")
    )
    return _WORD_RE.sub(" ", folded)


def classify_sub(
    *,
    category: str,
    description: str = "",
    project_name: str = "",
    notice_text: str = "",
) -> SubcategoryGuess:
    """Pick a sub-direction for a consulting notice.

    Returns an empty subcategory for every other direction — the five others
    are already actionable, and inventing sub-buckets there would add choices
    without adding information.
    """
    if category != TenderCategory.CONSULTING:
        return SubcategoryGuess(ConsultingSubcategory.UNKNOWN, 0.0, "Not consulting.")

    primary = _normalise(f"{description} {project_name}")
    body = _normalise(notice_text)[:4000]

    scores: dict[str, int] = {}
    for sub, keywords in _KEYWORDS.items():
        score = 0
        for phrase, weight in keywords:
            # `contains`, not `in`: a substring test read *information* as the
            # French *formation* and *registry* as GIS, which between them
            # touched two thirds of this table's input. See `keywords.py`.
            if contains(phrase, primary):
                score += weight * 2
            # A single generic word ("audit", "digital") appearing somewhere in
            # a multi-page body says nothing — every notice repeats the Bank's
            # procurement boilerplate. Only phrases are trusted that deep.
            elif " " in phrase and contains(phrase, body):
                score += weight
        if score:
            scores[sub] = score

    if not scores:
        return SubcategoryGuess(
            ConsultingSubcategory.OTHER, 0.0, "No sub-direction keyword matched."
        )

    best, best_score = max(scores.items(), key=lambda item: item[1])
    total = sum(scores.values())
    share = best_score / total
    # Same damping as the main classifier: two weak hits are not certainty.
    strength = min(1.0, best_score / 16)
    confidence = round(share * strength, 3)

    runner_up = sorted(scores.items(), key=lambda item: -item[1])[1:2]
    rationale = f"score={best_score}"
    if runner_up:
        rationale += f", runner-up {runner_up[0][0]}={runner_up[0][1]}"

    # Below this the winner is barely ahead of noise, and "other consulting" is
    # a more honest answer than a coin-flip between two sub-directions.
    if confidence < 0.18:
        return SubcategoryGuess(
            ConsultingSubcategory.OTHER, confidence, f"weak signal ({rationale})"
        )

    return SubcategoryGuess(best, confidence, rationale)
