"""Classify a tender's direction by reading its document with Claude.

The TZ asks for the notice document to be opened, read, and categorised by
direction (Construction, Consulting, Supply, …) so a company can follow only
its own category.

Design:

* The keyword rules in ``apps.tenders.categories`` run first and settle most
  notices for free.
* Claude is called only for the ones the rules are unsure about, and reads the
  sanitised notice body — never the raw upstream HTML.
* A structured output schema pins the response to the exact category set, so
  no free-text parsing or prompt-shaped guessing is involved.
* Any failure — no key, rate limit, refusal, bad JSON — falls back to the rule
  result. Classification never blocks the pipeline.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass

from django.conf import settings
from django.utils import timezone

from ...categories import CategorySource, TenderCategory, classify_by_rules
from ...consulting import classify_audience
from ...models import TenderNotice
from ...sanitizers import html_to_text
from ...subcategories import classify_sub
from .client import AIUnavailable, classification_enabled, get_client

logger = logging.getLogger(__name__)

# Categories the model may return — mirrors TenderCategory minus "unknown",
# which the model should never need (it has an "other" bucket).
_ALLOWED = [
    TenderCategory.CONSTRUCTION,
    TenderCategory.CONSULTING,
    TenderCategory.SUPPLY,
    TenderCategory.SERVICES,
    TenderCategory.IT,
    TenderCategory.OTHER,
]

RESPONSE_SCHEMA: dict = {
    "type": "object",
    "properties": {
        "category": {
            "type": "string",
            "enum": [str(value) for value in _ALLOWED],
            "description": "The single best-fitting direction for this tender.",
        },
        "confidence": {
            "type": "number",
            "description": "How certain the classification is, from 0 to 1.",
        },
        "rationale": {
            "type": "string",
            "description": "One short sentence naming the deciding evidence.",
        },
    },
    "required": ["category", "confidence", "rationale"],
    "additionalProperties": False,
}

SYSTEM_PROMPT = """\
You classify public procurement notices published by international financial \
institutions into exactly one direction, so that a company following that \
direction sees only relevant tenders.

Directions:
- construction: civil works, building, rehabilitation, roads, pipelines, \
installation of physical infrastructure.
- consulting: intellectual services — consultants (firm or individual), \
studies, design, supervision, audit, training, technical assistance.
- supply: delivery of goods — equipment, vehicles, furniture, materials, \
medicines, spare parts.
- it: software, information systems, IT equipment, networks, digital platforms. \
Prefer `it` over `supply` when the substance of the contract is technology.
- services: non-consulting services — cleaning, catering, security, logistics, \
transport, insurance, printing.
- other: none of the above fits.

Rules:
- Judge the substance of the contract, not the wording of the notice type.
- "Supply and installation" is `supply` when the goods dominate and \
`construction` when the works dominate.
- A Request for Expression of Interest for a consultant is `consulting` even \
when the project itself builds something.
- Base the answer on the text you are given; do not speculate about the \
wider project.
- Set confidence below 0.5 when the text is too thin to decide.\
"""


@dataclass(slots=True)
class ClassificationResult:
    category: str
    confidence: float
    source: str
    rationale: str = ""

    @property
    def is_confident(self) -> bool:
        return self.confidence >= 0.6


def classify_notice(
    notice: TenderNotice,
    *,
    use_ai: bool | None = None,
    force: bool = False,
) -> ClassificationResult:
    """Classify one notice, preferring rules and escalating to Claude.

    ``force`` sends the notice to Claude even when the rules were confident —
    used by the console when an operator disputes a category.
    """
    guess = classify_by_rules(
        description=notice.bid_description,
        project_name=notice.project_name,
        notice_text=notice.notice_text_sanitized,
        procurement_method_code=notice.procurement_method_code,
        procurement_group=notice.procurement_group,
    )
    rule_result = ClassificationResult(
        guess.category, guess.confidence, CategorySource.RULES, guess.rationale
    )

    threshold = settings.ANTHROPIC["CLASSIFY_RULE_THRESHOLD"]
    # `classification_enabled`, not `ai_enabled`: a key present for compliance
    # extraction is not consent to classify the archive (see settings).
    # An explicit `use_ai=True` from an operator still wins — that is a person
    # asking for one notice, not a schedule spending on 25,000.
    wants_ai = use_ai if use_ai is not None else classification_enabled()
    if not wants_ai:
        return rule_result
    if not force and rule_result.confidence >= threshold:
        return rule_result

    try:
        return _classify_with_claude(notice)
    except AIUnavailable as exc:
        logger.info("AI classification unavailable (%s) — using rules.", exc)
        return rule_result
    except Exception as exc:  # noqa: BLE001 - enrichment must never break sync
        logger.warning("AI classification failed for %s: %s", notice.notice_id, exc)
        return rule_result


def _classify_with_claude(notice: TenderNotice) -> ClassificationResult:
    client = get_client()
    config = settings.ANTHROPIC

    body = html_to_text(notice.notice_text_sanitized, max_length=config["CLASSIFY_MAX_CHARS"])
    user_content = "\n".join(
        part for part in (
            f"Notice type: {notice.notice_type}" if notice.notice_type else "",
            f"Title: {notice.bid_description}" if notice.bid_description else "",
            f"Project: {notice.project_name}" if notice.project_name else "",
            f"Procurement method: {notice.procurement_method_name}"
            if notice.procurement_method_name else "",
            f"Country: {notice.country}" if notice.country else "",
            "",
            "Notice text:",
            body or "(the notice published no body text)",
        ) if part is not None
    )

    response = client.messages.create(
        model=config["MODEL"],
        max_tokens=config["CLASSIFY_MAX_TOKENS"],
        system=[
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                # Stable across every call, so it is worth caching; below the
                # cacheable minimum this is simply ignored.
                "cache_control": {"type": "ephemeral"},
            }
        ],
        # `effort` keeps a high-volume classification cheap; the schema pins
        # the answer shape so no parsing heuristics are needed.
        output_config={
            "effort": config["CLASSIFY_EFFORT"],
            "format": {"type": "json_schema", "schema": RESPONSE_SCHEMA},
        },
        messages=[{"role": "user", "content": user_content}],
    )

    # A safety decline returns HTTP 200 with empty/partial content — check the
    # stop reason before touching `content`.
    if response.stop_reason == "refusal":
        raise RuntimeError("model declined to classify this notice")

    payload = _first_json(response)
    category = str(payload.get("category", "")).strip().lower()
    if category not in {str(value) for value in _ALLOWED}:
        raise ValueError(f"model returned an unknown category: {category!r}")

    try:
        confidence = float(payload.get("confidence", 0))
    except (TypeError, ValueError):
        confidence = 0.0

    return ClassificationResult(
        category=category,
        confidence=max(0.0, min(1.0, confidence)),
        source=CategorySource.AI,
        rationale=str(payload.get("rationale", ""))[:500],
    )


def _first_json(response) -> dict:
    """Decode the JSON the structured-output format guarantees."""
    for block in response.content:
        if getattr(block, "type", None) == "text":
            return json.loads(block.text)
    raise ValueError("model returned no text block")


def apply_classification(
    notice: TenderNotice, result: ClassificationResult, *, save: bool = True
) -> TenderNotice:
    """Write a classification — and, for consulting, its sub-direction."""
    notice.category = result.category
    notice.category_source = result.source
    notice.category_confidence = result.confidence
    notice.category_rationale = result.rationale[:500]
    notice.category_updated_at = timezone.now()

    # The sub-direction depends on the direction, so it is derived here rather
    # than separately — the two can never drift out of step.
    sub = classify_sub(
        category=result.category,
        description=notice.bid_description,
        project_name=notice.project_name,
        notice_text=notice.notice_text_sanitized,
    )
    notice.subcategory = sub.subcategory
    notice.subcategory_confidence = sub.confidence or None

    # The audience rides along for the same reason: it is derived from the
    # direction, so deriving it anywhere else lets the two disagree. Unlike
    # the sub-direction it costs nothing — it reads two fields already on the
    # notice and never touches the body.
    notice.consulting_audience = classify_audience(
        category=result.category,
        procurement_method_code=notice.procurement_method_code,
        procurement_method_name=notice.procurement_method_name,
    ).audience

    if save:
        notice.save(
            update_fields=[
                "category", "category_source", "category_confidence",
                "category_rationale", "category_updated_at",
                "subcategory", "subcategory_confidence",
                "consulting_audience", "updated_at",
            ]
        )
    return notice


def classify_pending(
    *, limit: int = 100, use_ai: bool | None = None, queryset=None
) -> dict[str, int]:
    """Classify notices that have never been classified.

    Restricted by default to the actionable feed — the notices a user can
    actually bid on — so AI spend follows product value rather than archive
    size.
    """
    if queryset is None:
        queryset = TenderNotice.objects.focus()

    pending = queryset.filter(category=TenderCategory.UNKNOWN).order_by("-notice_date")[:limit]

    counters = {"classified": 0, "by_rules": 0, "by_ai": 0, "unknown": 0}
    for notice in pending:
        result = classify_notice(notice, use_ai=use_ai)
        if result.category == TenderCategory.UNKNOWN:
            counters["unknown"] += 1
            continue
        apply_classification(notice, result)
        counters["classified"] += 1
        counters["by_ai" if result.source == CategorySource.AI else "by_rules"] += 1

    logger.info(
        "Classification: %s classified (%s rules, %s AI), %s left unknown",
        counters["classified"], counters["by_rules"], counters["by_ai"], counters["unknown"],
    )
    return counters


def reclassify_by_rules(*, limit: int = 100, queryset=None) -> dict[str, int]:
    """Run the current rules again over notices the rules already decided.

    `classify_pending` only ever looks at `unknown` rows, which is right for a
    backlog and wrong for a rule change: a keyword table that learns something
    new leaves every notice it previously got wrong exactly as wrong as it was.

    **Only `rules` rows are touched.** A manual override is a human decision
    and an AI classification was paid for; neither should be quietly replaced
    because a keyword table moved. Nothing here calls the model, so this is
    free to run over the whole mirror.
    """
    if queryset is None:
        queryset = TenderNotice.objects.all()

    stale = (
        queryset.filter(category_source=CategorySource.RULES)
        .order_by("-notice_date")[:limit]
    )

    counters = {"seen": 0, "changed": 0}
    for notice in stale:
        counters["seen"] += 1
        before = notice.category
        result = classify_by_rules(
            description=notice.bid_description,
            project_name=notice.project_name,
            notice_text=notice.notice_text_sanitized,
            procurement_method_code=notice.procurement_method_code,
            procurement_group=notice.procurement_group,
        )
        if result.category == TenderCategory.UNKNOWN:
            # The rules losing signal they once had is not a reason to throw a
            # direction away: leave the row as it stands.
            continue
        apply_classification(notice, result)
        if notice.category != before:
            counters["changed"] += 1

    logger.info(
        "Reclassification: %s re-read, %s changed direction",
        counters["seen"], counters["changed"],
    )
    return counters
