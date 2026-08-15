"""The one place a language model is called for extraction.

L2 reads a notice body and L3 reads a mirrored document, but the *mechanics*
are identical: a frozen system prompt, one user block of source text, and a
JSON schema that constrains the answer to requirement objects. Putting that in
one module is not tidiness — it is what makes the ablation in DECISIONS.md D6
meaningful, because L2 and L3 then differ only in what they were shown and what
they were asked, never in how the model was configured or how its answer was
parsed.

Three decisions worth stating.

**The schema does the work, not the prose.** ``output_config.format`` with a
json_schema means a malformed answer is a transport-level impossibility rather
than something the prompt has to beg for. What the prompt is left to enforce is
the one thing a schema cannot: that ``evidence_quote`` is copied rather than
composed.

**The system prompt is frozen and cached.** It is a module constant with a
version string, marked ``cache_control``, and it never interpolates the notice,
the date, or anything else per-request — an interpolated byte at the front of
the prefix would invalidate the cache for every call after it. The source text
goes in the user turn, after the breakpoint.

**Failure is a return value.** A missing key, a refusal, a rate limit, an
unparseable tree: every one of them comes back as ``LayerResult(error=...)``
with whatever was salvaged. Nothing here raises into a Celery task. Same
contract as the harvester, for the same reason — a tender with a broken
extraction should show what L1 found, not a 500.

Not implemented here, deliberately: the Batch API. DECISIONS.md calls for it
(50% cheaper, and this work is offline by nature) and the request shape below
is already batch-compatible — ``build_request`` returns the params dict a batch
entry needs. What is missing is the submit/poll/collect cycle, which is a
separate piece of machinery and is tracked as such rather than half-built here.
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import dataclass
from decimal import Decimal
from typing import Any

from django.conf import settings

from .extraction import Extracted, ExtractedExpert, LayerResult
from .scoring import IMPORTANCE_LEVELS

logger = logging.getLogger(__name__)

#: The importance vocabulary, as a set for the boundary check below.
#:
#: Imported from ``scoring`` rather than written twice: the schema's ``enum``
#: and the weight table have to name the same levels, and the way that stops
#: being true is somebody adding a level to one of them.
IMPORTANCE_VALUES = frozenset(IMPORTANCE_LEVELS)

#: Bump when the system prompt below changes in a way that could move results.
#: Stored on every ExtractionRun so a quality shift is attributable to the
#: prompt rather than guessed at.
#:
#: v2 added the expert-position section and the second output array (D20).
#: v3 added `importance` and the Uzbek/Russian labels (D28). Both are new
#: *fields* on an otherwise byte-identical requirement object, which is the
#: cheap kind of prompt change — but it is still a change, and a shift in
#: requirement recall between v2 and v3 runs is exactly what this string exists
#: to make attributable rather than arguable.
#: v4 rewrote the translation instruction and changed nothing else. v3 produced
#: Uzbek that was a word-by-word gloss and frequently not Uzbek at all — "Ingliz
#: tilida shunoslik", "halollik izchili" — which matters more than it sounds now
#: that the label is the only statement of the requirement a vendor reads (D30,
#: revised). The requirement schema is byte-identical, so recall should not move.
PROMPT_VERSION = "v4"

#: Per-million-token prices, input and output. Kept here rather than fetched
#: because the ablation needs the price that was true when the run happened,
#: and a run re-costed later against a changed price list is not a measurement.
#: Source: Anthropic pricing, recorded 2026-08-07.
MODEL_PRICES: dict[str, tuple[Decimal, Decimal]] = {
    "claude-opus-5": (Decimal("5"), Decimal("25")),
    "claude-sonnet-5": (Decimal("3"), Decimal("15")),
    "claude-haiku-4-5": (Decimal("1"), Decimal("5")),
}

#: Models that accept ``output_config.effort``.
#:
#: A capability table rather than a version check, and it exists because the
#: alternative failed in production: pointing ``AI_MODEL`` at Haiku to save
#: money during tuning made every call return 400 "This model does not support
#: the effort parameter". The tier is meant to be a measured axis of the
#: evaluation (D6), so switching it must not be able to break the request.
#:
#: Listing what *does* support it is the safe direction: a model missing from
#: this table loses a tuning knob, while a model wrongly assumed to support it
#: loses every call.
MODELS_WITH_EFFORT: frozenset[str] = frozenset(
    {"claude-opus-5", "claude-sonnet-5"}
)

#: What a single extracted requirement must look like coming back. The
#: expression grammar mirrors ``apps.compliance.expressions``; anything the
#: model returns that will not parse there is discarded at the boundary, so
#: this schema is the first filter rather than the only one.
REQUIREMENT_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "requirements": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "key": {
                        "type": "string",
                        "description": (
                            "Short snake_case name for what is being required, "
                            "e.g. annual_turnover_avg, similar_contracts_count, "
                            "years_experience, bid_security, specific_certification."
                        ),
                    },
                    "label": {
                        "type": "string",
                        "description": (
                            "Human-readable name of the criterion, in English."
                        ),
                    },
                    # Three columns for the three languages the product ships
                    # in, asked for in the same call rather than by a second
                    # translation pass. The sentence is already in context and
                    # the system prompt is already cached, so a label in three
                    # languages costs the output tokens of two short strings —
                    # against a second request per requirement, with its own
                    # input bill and its own failure mode, for a translator that
                    # would be working from the label alone with the tender it
                    # came from no longer in front of it.
                    "label_uz": {
                        "type": "string",
                        "description": (
                            "The same label in Uzbek (Latin script). Translate "
                            "the criterion's name only — never the quote."
                        ),
                    },
                    "label_ru": {
                        "type": "string",
                        "description": (
                            "The same label in Russian. Translate the "
                            "criterion's name only — never the quote."
                        ),
                    },
                    "importance": {
                        "type": "string",
                        "enum": list(IMPORTANCE_LEVELS),
                        "description": (
                            "How much of the bid this criterion decides, "
                            "according to the document itself. See the system "
                            "prompt — this is a reading, not an opinion."
                        ),
                    },
                    "applies_to": {
                        "type": "string",
                        "enum": ["single", "jv_combined", "jv_each", "jv_at_least_one"],
                        "description": (
                            "Who must satisfy it. Use 'single' unless the source "
                            "explicitly says how a joint venture is treated."
                        ),
                    },
                    "is_mandatory": {
                        "type": "boolean",
                        "description": (
                            "True when failing it disqualifies. False for a stated "
                            "preference or desirable attribute."
                        ),
                    },
                    "evidence_quote": {
                        "type": "string",
                        "description": (
                            "One sentence copied EXACTLY from the source text, "
                            "character for character. Never paraphrased, never "
                            "shortened, never translated."
                        ),
                    },
                    "expression": {
                        "type": "object",
                        "description": (
                            "The criterion as an evaluable tree, in the engine's "
                            "own vocabulary. See the system prompt for the grammar."
                        ),
                        "properties": {
                            "kind": {
                                "type": "string",
                                "enum": ["scalar", "count", "exists"],
                            },
                            # `scalar` names the declared value; `count`/`exists`
                            # name the kind of record being counted. Two fields
                            # rather than one because the engine's own
                            # serialisation uses two, and a schema that invented
                            # a shared name would produce trees that look right
                            # and fail to parse.
                            "key": {"type": "string"},
                            "entity": {"type": "string"},
                            "op": {
                                "type": "string",
                                "enum": [">=", ">", "<=", "<", "==", "!="],
                            },
                            "value": {"type": ["number", "string", "boolean", "null"]},
                            "unit": {"type": "string"},
                            "label": {"type": "string"},
                            "where": {
                                "type": "array",
                                "items": {
                                    "type": "object",
                                    "properties": {
                                        "field": {"type": "string"},
                                        "op": {
                                            "type": "string",
                                            "enum": [">=", ">", "<=", "<", "==", "!="],
                                        },
                                        "value": {
                                            "type": ["number", "string", "boolean", "null"]
                                        },
                                    },
                                    "required": ["field", "op", "value"],
                                    "additionalProperties": False,
                                },
                            },
                        },
                        "required": ["kind"],
                        "additionalProperties": False,
                    },
                },
                "required": [
                    "key",
                    "label",
                    "label_uz",
                    "label_ru",
                    "importance",
                    "applies_to",
                    "is_mandatory",
                    "evidence_quote",
                    "expression",
                ],
                "additionalProperties": False,
            },
        }
    },
    "required": ["requirements"],
    "additionalProperties": False,
}


def expert_property(role_slugs: list[str]) -> dict[str, Any]:
    """The second output array: the expert positions the document asks for.

    ``role`` is an ``enum`` of the directory's own role slugs rather than a free
    string, and that is the whole design (D20). A free string would come back as
    "Environmental & Social Safeguards Expert" and leave someone to decide,
    later and fuzzily, which of 36 taxonomy rows that is. Constraining the
    schema moves the decision into the call that is already reading the
    sentence, and what returns is a key that either exists in the database or is
    the explicit ``other`` sentinel.

    ``title`` is required alongside it, always, because the slug loses what the
    tender actually wrote and the vendor needs to read that.
    """
    return {
        "type": "array",
        "description": (
            "Expert positions the document requires the bidder's team to "
            "include. Empty when it names none."
        ),
        "items": {
            "type": "object",
            "properties": {
                "title": {
                    "type": "string",
                    "description": (
                        "The position exactly as the document names it, e.g. "
                        "'Senior Resettlement and Livelihoods Specialist'."
                    ),
                },
                "role": {
                    "type": "string",
                    "enum": role_slugs,
                    "description": (
                        "The closest role in our directory. Use the last value "
                        "when none of them fits — a wrong role is worse than an "
                        "unclassified one."
                    ),
                },
                "count": {
                    "type": "integer",
                    # No "minimum". `output_config.format` rejects the whole
                    # request with 400 "For 'integer' type, property 'minimum'
                    # is not supported" — so this one word failed every L2 and
                    # L3 call rather than constraining anything. The bound is
                    # enforced in `_to_experts` instead, which is where it
                    # belonged: the model reads, the code decides.
                    "description": (
                        "How many people in this position the document asks "
                        "for. 1 unless it states a number."
                    ),
                },
                "is_mandatory": {
                    "type": "boolean",
                    "description": (
                        "False only where the document itself calls the "
                        "position desirable, optional or an advantage."
                    ),
                },
                "evidence_quote": {
                    "type": "string",
                    "description": (
                        "The sentence naming this position, copied EXACTLY "
                        "from the source text. Same rule as a requirement."
                    ),
                },
            },
            "required": ["title", "role", "count", "is_mandatory", "evidence_quote"],
            "additionalProperties": False,
        },
    }


def build_schema(role_slugs: list[str] | None = None) -> dict[str, Any]:
    """The response schema for one call, with the expert array when we can use it.

    An empty vocabulary means the taxonomy fixture has not been loaded — a fresh
    deployment, or a test database that did not need it. The array is then left
    out of the schema entirely rather than shipped with an empty ``enum``, which
    no model can satisfy and which would turn a missing fixture into a failed
    extraction. Requirements keep working; the second rabbit is simply not
    hunted. Degrade, never break.
    """
    if not role_slugs:
        return REQUIREMENT_SCHEMA

    schema = json.loads(json.dumps(REQUIREMENT_SCHEMA))
    schema["properties"]["expert_positions"] = expert_property(role_slugs)
    # Required, so "this document names no experts" comes back as an empty list
    # rather than as an absent key that could equally mean the model forgot.
    schema["required"] = ["requirements", "expert_positions"]
    return schema


#: Frozen. Interpolating anything here would invalidate the prompt cache for
#: every subsequent call — the per-notice text belongs in the user turn.
SYSTEM_PROMPT = """\
You extract qualification criteria from World Bank procurement documents.

You are a reader, not a judge. Your only job is to state what the document
requires, in a form that can be evaluated by arithmetic. You never decide
whether any particular bidder qualifies, and you never soften or strengthen a
requirement to make it more reasonable.

WHAT COUNTS AS A REQUIREMENT
Extract a criterion only when the document states a condition a bidder must
meet to be eligible or to be evaluated: minimum turnover, liquid assets or
credit lines, a number of similar contracts within a period, years of
experience, required personnel or their qualifications, bid security, a
required certification or registration.

Do NOT extract:
- descriptions of the work to be done, the scope, or the deliverables;
- deadlines, submission addresses, or contact details;
- statements about what the employer will do;
- anything phrased as a mere expectation with no threshold attached.

THE QUOTE IS THE PRODUCT
Every requirement carries `evidence_quote`: one sentence copied out of the
source text character for character. Not shortened. Not tidied. Not
translated. Not stitched together from two places. If the sentence that states
the requirement runs long, quote it in full; length is not a problem, but an
altered quote is. If you cannot copy an exact sentence that states the
requirement, do not return that requirement at all. A missing requirement is a
gap we can measure; an invented quote is a claim we cannot trust.

THE EXPRESSION GRAMMAR
`expression` is a tree with one of these three node kinds. Field names are not
negotiable — they are the engine's own, and a tree with the right meaning under
the wrong names is discarded unparsed.

  scalar  - one declared number about the bidder, compared to a threshold.
            "key" names the value; "unit" is the currency exactly as written.
            {"kind":"scalar","key":"annual_turnover_avg","op":">=",
             "value":22400000,"unit":"USD"}

  count   - how many records of one kind satisfy some filters.
            "entity" names the kind of record; each filter is
            {"field":..,"op":..,"value":..}.
            {"kind":"count","entity":"contract","op":">=","value":2,
             "where":[{"field":"value","op":">=","value":22400000},
                      {"field":"year","op":">=","value":2016}]}

  exists  - at least one record of a kind satisfying the filters. No "op" or
            "value" of its own.
            {"kind":"exists","entity":"certification",
             "where":[{"field":"name","op":"==","value":"ISO 9001"}]}

There is no way to combine nodes here, and that is deliberate. Where a document
states two conditions, return two requirements — both are mandatory, so both
must hold, and two simple trees a reader can check beat one nested tree they
cannot.

Rules for values: amounts are plain numbers with no separators or currency
symbols, with "unit" carrying the currency code as written. Years are
four-digit numbers. Where the document spells a number and repeats it in digits
— "two (2)", "ten (10)" — use the digits.

HOW MUCH THE CRITERION DECIDES
`importance` says how much of the bid rests on this one criterion. It is a
reading of the document, not an opinion about what matters in procurement, and
the only evidence for it is how the text itself treats the requirement:

  high    - the document makes it a gate. It says a bidder who fails it is
            ineligible, non-responsive, will be rejected or will not be
            evaluated further; or it is stated as a minimum qualification the
            bidder "must" or "shall" meet to be considered at all.
  medium  - the document requires it, in the ordinary way, without saying what
            happens to a bidder who falls short. This is the default: most
            requirements are phrased this way, and most of them belong here.
  low     - the document itself softens it. It calls the attribute desirable,
            preferable, an advantage, "will be an asset", or lists it among
            things that will be taken into account rather than required.

Read the sentence you are quoting and the heading it sits under, nothing else.
Do not rank criteria against each other, do not reason about which is harder to
meet, and do not import a view about which requirements usually decide World
Bank tenders — a criterion the document states plainly is `medium` however
important it looks. If the text gives you no signal at all, `medium` is the
answer; it is not a failure to have used it.

`is_mandatory` and `importance` are different questions and both are asked. The
first is whether the document requires the thing; the second is what the
document says happens if you do not have it.

THE LABEL IS TRANSLATED; THE QUOTE IS NEVER
`label` names the criterion in English. `label_uz` and `label_ru` are the same
name in Uzbek (Latin script) and Russian — the reader is a vendor in Uzbekistan
who may not read the borrower's language, and on their screen the label *is* the
requirement. It is not a subtitle to an English original; it is what they read.

So write Uzbek a procurement officer would write, not a word-by-word gloss.
Translating each English word in turn produces strings that are not Uzbek at
all, and every one of these came back from a real run:

  "Fluency in English"            -> "Ingliz tilini ravon bilish"
                                     NOT "Ingliz tilida shunoslik"
  "Track record of integrity"     -> "Halollik bo'yicha ijobiy tarix"
                                     NOT "halollik izchili"
  "Working knowledge of public
   procurement procedures"        -> "Davlat xaridlari tartiblarini amalda
                                      bilish"
                                     NOT "sotib olish protseduralarining
                                      ishchi bilib-texnikasi"
  "Similar contracts"             -> "Shunga o'xshash shartnomalar"
  "Average annual turnover"       -> "O'rtacha yillik aylanma"

Read the whole criterion, then name it in Uzbek as if writing the requirement
from scratch. Use established Uzbek procurement vocabulary — "xarid" for
procurement, "malaka" for qualification, "tajriba" for experience, "aylanma"
for turnover. If a term has no settled Uzbek form, keep the English word rather
than inventing one: a vendor recognises "procurement", and "sotib olish
protseduralarining bilib-texnikasi" tells them nothing.

A label is a phrase to scan in a list, not a sentence. Keep numbers, currency
codes and standard names (ISO 9001) as they are.

Institutions are named, never translated word by word. Where Uzbek has an
established name for one, use it; otherwise keep the English:

  "World Bank"                    -> "Jahon banki"
                                     NOT "Dunyoning banki"
  "Asian Development Bank (ADB)"  -> "Osiyo taraqqiyot banki (ADB)"
  "EIB", "EBRD", "AIIB"           -> unchanged

The same rules apply to `label_ru`: "World Bank" is "Всемирный банк", not a
literal rendering of the two words.

`evidence_quote` stays in the language the document is written in, character for
character, in every case. It is checked against the source text, so a translated
quote is an altered quote and will be recorded as an invention.

EXPERT POSITIONS ARE A SEPARATE ANSWER
Consulting assignments name the people the team must include — a Team Leader, a
Procurement Specialist, an Environmental Safeguards Expert. Return those under
`expert_positions`, never under `requirements`. They are two different answers
about the same text and mixing them corrupts both.

For each position: the title exactly as the document writes it, the closest role
from the list the schema gives you, how many are asked for, and the sentence
that names it — copied, under the same quote rule as everything else.

Two ways to get this wrong, both worse than returning nothing:

- Naming a role because the text uses a word associated with it. A document
  mentioning "resettlement" is not thereby asking for a Resettlement
  Specialist. Return a position only where the document says the team must
  include one.
- Forcing a position into a role that does not fit. The list ends with a value
  for "none of these"; use it. An unclassified position still tells the bidder
  what to look for, while a wrong one sends them after the wrong person.

A position's qualifications — "with at least 10 years of experience" — stay in
its quote. Do not turn them into a requirement: nothing in a bidder profile
answers them, and a criterion nobody can be measured against is noise.

WHEN THE DOCUMENT DOES NOT SAY
Return an empty list. A notice that says only "qualification requirements are
set out in Section III" states no requirement itself, and inventing one from
what such tenders usually ask is the single worst thing you can do here. Do
not fill gaps from your own knowledge of World Bank procurement. Do not carry
a requirement over from a similar tender you have seen. Only what this text
says.
"""


@dataclass
class LLMConfig:
    """Resolved model settings for one extraction call."""

    model: str
    effort: str = "low"
    max_tokens: int = 8000
    timeout: int = 120

    @classmethod
    def resolve(cls, model: str | None = None, **overrides: Any) -> "LLMConfig":
        config = settings.ANTHROPIC
        return cls(model=model or config["MODEL"], **overrides)


def is_available() -> bool:
    """Whether an LLM layer can run at all.

    Checked before a run is recorded rather than after, so a deployment with no
    key produces no failed-run noise — it simply never reaches L2 or L3.
    """
    config = settings.ANTHROPIC
    return bool(config.get("ENABLED")) and bool(config.get("API_KEY"))


def build_request(
    source_text: str,
    instruction: str,
    config: LLMConfig,
    *,
    role_slugs: list[str] | None = None,
) -> dict[str, Any]:
    """The request body, separated from sending it.

    Split out for two reasons that both pay off later: a test can assert on the
    exact body without a network stub, and the Batch API takes this dict as a
    batch entry's ``params`` unchanged when that path is built.

    ``role_slugs`` is the only part of the request that varies with the
    database, and it lives in the schema rather than in the prompt precisely so
    the cached prefix stays byte-identical across every call (D20).
    """
    return {
        "model": config.model,
        "max_tokens": config.max_tokens,
        "system": [
            {
                "type": "text",
                "text": SYSTEM_PROMPT,
                # The breakpoint sits at the end of the frozen prefix, so every
                # call after the first reads it instead of paying for it.
                "cache_control": {"type": "ephemeral"},
            }
        ],
        "output_config": {
            # `effort` only where it is accepted — see MODELS_WITH_EFFORT. The
            # schema is the part that must always be sent; effort is a knob.
            **(
                {"effort": config.effort}
                if config.model in MODELS_WITH_EFFORT
                else {}
            ),
            "format": {"type": "json_schema", "schema": build_schema(role_slugs)},
        },
        "messages": [
            {
                "role": "user",
                "content": f"{instruction}\n\n<source>\n{source_text}\n</source>",
            }
        ],
    }


def extract_with_model(
    source_text: str,
    instruction: str,
    *,
    config: LLMConfig | None = None,
    client: Any | None = None,
    source: str = "",
    source_document_id: str | None = None,
    role_slugs: list[str] | None = None,
) -> LayerResult:
    """Ask the model for requirements. Never raises.

    ``client`` is injectable so the layer tests run with a scripted double and
    no key — the same shape the harvester uses for its session.

    ``role_slugs`` turns on the expert-position half of the answer. Passing
    nothing asks the older question and is still a valid one — an ablation run
    comparing v1 against v2 needs to be able to.
    """
    config = config or LLMConfig.resolve()

    if not source_text.strip():
        return LayerResult(model=config.model, prompt_version=PROMPT_VERSION)

    if client is None:
        if not is_available():
            return LayerResult(
                model=config.model,
                prompt_version=PROMPT_VERSION,
                error="no API key configured",
            )
        client = _default_client(config)

    started = time.monotonic()
    try:
        response = client.messages.create(
            **build_request(source_text, instruction, config, role_slugs=role_slugs)
        )
    except Exception as exc:  # noqa: BLE001 - an extraction must not break a run
        logger.warning("Extraction request failed: %s", exc)
        return LayerResult(
            model=config.model,
            prompt_version=PROMPT_VERSION,
            duration_ms=int((time.monotonic() - started) * 1000),
            error=f"request failed: {exc}"[:500],
        )

    duration_ms = int((time.monotonic() - started) * 1000)
    result = LayerResult(
        model=getattr(response, "model", config.model) or config.model,
        prompt_version=PROMPT_VERSION,
        duration_ms=duration_ms,
    )
    _record_usage(result, response)

    # A safety decline is a fact about the request, not an outage. Recorded as
    # an error so the ablation can count it, with whatever content arrived.
    if getattr(response, "stop_reason", None) == "refusal":
        result.error = "model declined the request"
        return result

    payload = _first_text(response)
    if not payload:
        result.error = "empty response"
        return result

    try:
        data = json.loads(payload)
    except json.JSONDecodeError as exc:
        result.error = f"unparseable JSON: {exc}"[:500]
        return result

    result.requirements = _to_extracted(
        data.get("requirements") or [], source=source, source_document_id=source_document_id
    )
    result.experts = _to_experts(
        data.get("expert_positions") or [],
        source=source,
        source_document_id=source_document_id,
    )
    return result


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _default_client(config: LLMConfig) -> Any:
    import anthropic

    settings_config = settings.ANTHROPIC
    return anthropic.Anthropic(
        api_key=settings_config["API_KEY"],
        timeout=config.timeout,
        max_retries=settings_config.get("MAX_RETRIES", 2),
    )


def _first_text(response: Any) -> str:
    """The JSON body, from whichever content block carries it.

    Iterates rather than indexing ``content[0]``: with thinking enabled the
    first block is a thinking block, and indexing it returns nothing at all.
    """
    for block in getattr(response, "content", None) or []:
        if getattr(block, "type", None) == "text":
            return getattr(block, "text", "") or ""
    return ""


def _record_usage(result: LayerResult, response: Any) -> None:
    usage = getattr(response, "usage", None)
    if usage is None:
        return
    result.input_tokens = int(getattr(usage, "input_tokens", 0) or 0)
    result.output_tokens = int(getattr(usage, "output_tokens", 0) or 0)

    cached = int(getattr(usage, "cache_read_input_tokens", 0) or 0)
    written = int(getattr(usage, "cache_creation_input_tokens", 0) or 0)
    if cached or written:
        result.notes["cache_read_tokens"] = cached
        result.notes["cache_write_tokens"] = written

    result.cost_usd = estimate_cost(
        result.model,
        input_tokens=result.input_tokens,
        output_tokens=result.output_tokens,
        cached_tokens=cached,
        cache_write_tokens=written,
    )


def _prices_for(model: str) -> tuple[Decimal, Decimal] | None:
    """The price row for a model id, tolerating a dated suffix.

    ``AI_MODEL`` is set to an alias — ``claude-haiku-4-5`` — but the API answers
    with the snapshot it resolved to, ``claude-haiku-4-5-20251001``, and
    ``ExtractionRun.model`` records what actually ran rather than what was asked
    for. Keying the table strictly on the alias therefore priced every real run
    at zero: the console read "$0 spent" over a page of successful extractions,
    which is the same false reassurance a failed run gave before it, and the
    reason it is worth handling rather than documenting.

    Longest prefix wins, so a future ``claude-haiku-4-5-2`` cannot be captured
    by a shorter, unrelated key.
    """
    exact = MODEL_PRICES.get(model)
    if exact is not None:
        return exact
    candidates = [key for key in MODEL_PRICES if model.startswith(f"{key}-")]
    if not candidates:
        return None
    return MODEL_PRICES[max(candidates, key=len)]


def estimate_cost(
    model: str,
    *,
    input_tokens: int,
    output_tokens: int,
    cached_tokens: int = 0,
    cache_write_tokens: int = 0,
) -> Decimal:
    """Cost of one call, in USD.

    Cache reads bill at a tenth of the input rate and cache writes at 1.25×;
    both are counted because on this workload — one frozen system prompt across
    thousands of notices — they are most of the difference between the sticker
    price and the real one.
    """
    prices = _prices_for(model)
    if prices is None:
        # An unknown model is a real possibility (a tier added upstream, a
        # model pinned in the environment). Zero is honest; a guessed price
        # would quietly corrupt the ablation table.
        logger.info("No price recorded for model %r — cost left at 0.", model)
        return Decimal("0")

    input_price, output_price = prices
    per_token_in = input_price / Decimal(1_000_000)
    per_token_out = output_price / Decimal(1_000_000)
    total = (
        Decimal(input_tokens) * per_token_in
        + Decimal(output_tokens) * per_token_out
        + Decimal(cached_tokens) * per_token_in / Decimal(10)
        + Decimal(cache_write_tokens) * per_token_in * Decimal("1.25")
    )
    return total.quantize(Decimal("0.000001"))


def _to_extracted(
    rows: list[dict[str, Any]], *, source: str, source_document_id: str | None
) -> list[Extracted]:
    """Map raw schema rows onto the shared contract, dropping unusable ones.

    A row with no quote is dropped here rather than passed on to fail grounding
    later. The two look the same in the table but mean different things: a
    dropped row is a model that declined to claim, a NOT_FOUND row is a model
    that claimed something the source does not say.

    ``importance`` is filtered against the vocabulary rather than trusted. The
    schema's ``enum`` already constrains it, so a value outside the set means
    something upstream is not enforcing the schema — a mocked client in a test,
    a provider change — and the safe answer there is the empty string, which the
    scoring reads as ``medium``. Storing an unrecognised word would put a weight
    nobody defined into a percentage shown to a vendor.
    """
    extracted: list[Extracted] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        quote = (row.get("evidence_quote") or "").strip()
        key = (row.get("key") or "").strip()
        expression = row.get("expression")
        if not quote or not key or not isinstance(expression, dict):
            continue
        importance = str(row.get("importance") or "").strip().lower()
        extracted.append(
            Extracted(
                key=key[:64],
                label=(row.get("label") or "")[:200],
                label_uz=(row.get("label_uz") or "")[:200],
                label_ru=(row.get("label_ru") or "")[:200],
                importance=importance if importance in IMPORTANCE_VALUES else "",
                expression=expression,
                applies_to=row.get("applies_to") or "single",
                is_mandatory=bool(row.get("is_mandatory", True)),
                evidence_quote=quote,
                source=source,
                source_document_id=source_document_id,
            )
        )
    return extracted


def _to_experts(
    rows: list[dict[str, Any]], *, source: str, source_document_id: str | None
) -> list[ExtractedExpert]:
    """The same mapping for expert positions, with the same one strict rule.

    A position with no quote or no title is dropped rather than carried. It is
    the identical trade to ``_to_extracted``: what is dropped here is a model
    declining to claim, which is silence, and what survives to fail grounding
    later is a model claiming something the source does not say, which is a
    measurement. Keeping the two apart is the whole reason the hallucination
    rate means anything.

    ``count`` is clamped rather than trusted, and now it is the *only* thing
    bounding the value: the schema cannot say ``minimum``, because
    ``output_config.format`` rejects that keyword outright. So this clamp is
    load-bearing, not defence in depth. It matters because the number is
    displayed to a vendor — an absurd one would be read as a fact about the
    tender rather than as the artefact it is.
    """
    experts: list[ExtractedExpert] = []
    for row in rows:
        if not isinstance(row, dict):
            continue
        quote = (row.get("evidence_quote") or "").strip()
        title = (row.get("title") or "").strip()
        if not quote or not title:
            continue

        try:
            count = int(row.get("count") or 1)
        except (TypeError, ValueError):
            count = 1

        experts.append(
            ExtractedExpert(
                title=title[:200],
                role_slug=(row.get("role") or "").strip(),
                count=min(max(count, 1), 99),
                is_mandatory=bool(row.get("is_mandatory", True)),
                evidence_quote=quote,
                source=source,
                source_document_id=source_document_id,
            )
        )
    return experts
