"""L2 — the criteria in a notice body that no rule can be written for.

The second layer of the extraction stack (DECISIONS.md D6). It runs after
L1 and asks a language model for one thing only: the qualification criteria
those two could not establish from the same text.

**Why anything is left for it.** L1 reads the criteria whose phrasing the World
Bank's own templates fix — average annual turnover, liquid assets, a
similar-contract count, bid security — by matching a topic word next to a
number. What a rule cannot reach is a criterion stated in a sentence nobody
templated: a registration with a national authority, a named certification, a
team leader's years of experience, an audited-statements clause phrased in the
borrower's own words. Those are not rare; they are irregular, and a rule loose
enough to catch them matches the scope of works just as often.

**Why it must never re-derive L1's answers.** The layer stack's economic
argument is that each layer is asked only for the gap left by the one below it.
So ``exclude_keys`` — the keys L1 already established — goes into the
per-notice instruction, and any excluded key that comes back anyway is dropped
before it reaches the pipeline. A layer that silently re-extracted what L1 had
already read for free would still produce the right requirement set, and would
make the ablation table meaningless.

Three decisions this module owns, each stated where it is implemented below:
what text is sent (``_bounded_source``), when no text is sent at all
(``_worth_asking``), and what happens to a quote that is not in what was sent
(``extract``). The last is the one that decides whether the hallucination rate
stays measurable.

Nothing here talks to the API directly: ``llm.extract_with_model`` owns the
request, the schema and the never-raise contract, so L2 and L3 differ only in
what they were shown and what they were asked.
"""

from __future__ import annotations

import re
from collections.abc import Iterable

from . import l1
from .extraction import LayerResult
from .llm import LLMConfig, extract_with_model
from .text import canonical, contains_quote, sentences

#: How much of a notice body is sent, in characters of canonical text.
#:
#: Measured over 4,000 notices with a body (2026-08-07): median 1,070
#: characters, p95 5,112, p99 13,908, longest 54,887. A 20,000-character budget
#: therefore sends 99.25% of the corpus in full, and the 0.75% it bounds are
#: the notices that carry a whole annexed scope of works in the body — text
#: whose tail is description, not criteria.
#:
#: Rejected: selecting the sentences that look most likely to carry criteria and
#: sending only those. It is cheaper and it is exactly wrong for this layer —
#: the selector would have to decide what a requirement looks like, which is
#: L1's job and L1's blind spot. L2 exists to read the sentences a keyword test
#: would not pick, so it must be shown the ones it would not pick.
MAX_SOURCE_CHARS = 20_000

#: Language that has to be present somewhere before a request can pay off.
#:
#: Deliberately much looser than L1's rules, and the difference in kind matters:
#: L1's patterns are *sufficient* conditions for one specific criterion, this is
#: a *necessary* condition for any criterion at all. Gating on L1's patterns
#: instead would mean L2 only ever ran where L1 had already fired — the exact
#: inverse of what this layer is for.
#:
#: Measured over the same 4,000 notices: 42% contain one of these tokens. The
#: remaining 58% are pure announcements — project background, scope, submission
#: address — and sending them costs a request per notice to be told, correctly,
#: that they state nothing. Widening the vocabulary further was tested and moved
#: the figure by 0.6 percentage points, so the split is a property of the corpus
#: rather than of this list.
_REQUIREMENT_LANGUAGE_RE = re.compile(
    r"qualif|eligib|shall|must|required|requirement|minimum|at least|"
    r"not less than|no less than|turnover|liquid asset|bid security|net worth|"
    r"cash[-\s]?flow|certif|licen[cs]|registration|accredit|experien|personnel|"
    r"audited|financial statement|capabilit|capacit",
    re.IGNORECASE,
)

#: An already-established key mapped to the L1 pattern that recognises the
#: sentence it was read from. Imported from ``l1`` rather than copied: two
#: spellings of "this sentence is about turnover" would drift apart, and the
#: one that drifted would be the one deciding whether to spend money.
#:
#: Only L1's keys appear, and with no L0 (D17) that makes the map complete:
#: every key that can arrive in ``exclude_keys`` from a shallower layer is here,
#: so a sentence can always be attributed to the layer that already read it.
_KEY_TOPICS: dict[str, re.Pattern[str]] = {
    "annual_turnover_avg": l1._TURNOVER_RE,
    "liquid_assets": l1._LIQUID_RE,
    "similar_contracts_count": l1._CONTRACTS_RE,
    "bid_security": l1._BID_SECURITY_RE,
}


def extract(
    notice_text: str,
    *,
    reference_year: int | None = None,
    exclude_keys: Iterable[str] = (),
    config: LLMConfig | None = None,
    client: object | None = None,
    role_slugs: list[str] | None = None,
) -> LayerResult:
    """Every requirement a model can read from one notice body, minus the known.

    ``reference_year`` is the notice's own publication year; it is passed to the
    model for the same reason L1 uses it, and withheld the same way when absent
    (see ``_instruction``). ``exclude_keys`` is what L1 already
    established. ``config`` and ``client`` are injectable so the ablation can
    sweep model tier (D11) and so the tests need no key.

    ``role_slugs`` asks the same call for the expert positions the notice names
    (D20). It is a second answer about the same sentences, not a second request:
    the text is already being read, the system prompt is already cached, and the
    only extra cost is the output tokens the positions themselves occupy.
    Passing nothing turns the question off, which is what makes an ablation
    between the two prompt versions possible.

    **A quote that is not in the text we sent is kept, not dropped.** The
    tempting alternative — discard it, as DECISIONS.md D4 originally said — is
    what ``models.py::TenderRequirement.Grounding.NOT_FOUND`` exists to prevent:
    a discarded row and a row that was never proposed look identical in the
    table, so discarding here would report a hallucination rate of zero no
    matter how the model behaved. The row travels on with its unverifiable
    quote, the pipeline's verifier marks it NOT_FOUND, ``is_usable`` keeps it
    out of every verdict, and the count survives to be measured. What L2 adds is
    the count itself, in ``notes``, so a run's grounding rate is available even
    before the verifier has run.

    Note what this is checked against: the bounded text that was actually sent,
    never the full notice. That is the whole point of bounding on sentence
    boundaries — a sentence the model was not shown cannot be the source of a
    quote, so an unverifiable quote here is a claim about the source and not an
    artefact of truncation.
    """
    excluded = {key.strip() for key in exclude_keys if key and key.strip()}

    payload, sentences_sent, sentences_total = _bounded_source(notice_text)
    reason = _worth_asking(payload, excluded)
    if reason:
        # Not an error: nothing failed, and the pipeline should record a layer
        # that correctly declined to spend a request. ``model`` and
        # ``prompt_version`` stay empty because no prompt was ever built.
        return LayerResult(notes={"skipped": reason})

    result = extract_with_model(
        payload,
        _instruction(excluded, reference_year),
        config=config,
        client=client,
        source="notice_body",
        role_slugs=role_slugs,
    )

    kept = []
    returned_excluded = 0
    ungrounded = 0
    for requirement in result.requirements:
        if requirement.key in excluded:
            # The instruction asked for this not to come back. That it did is
            # worth counting — it is a prompt-quality signal, and a silent drop
            # would hide a model that ignores half of what it is told.
            returned_excluded += 1
            continue
        if not contains_quote(payload, requirement.evidence_quote):
            ungrounded += 1
        kept.append(requirement)

    result.requirements = kept

    # Expert positions are checked against the same payload and by the same
    # rule, but counted separately. One number that mixed them would make a
    # prompt regression in either half look like a smaller regression in both.
    experts_ungrounded = sum(
        1
        for position in result.experts
        if not contains_quote(payload, position.evidence_quote)
    )

    result.notes.update(
        {
            "source_chars": len(payload),
            "sentences_sent": sentences_sent,
            "sentences_dropped": sentences_total - sentences_sent,
            "excluded_keys_returned": returned_excluded,
            "quotes_verified": len(kept) - ungrounded,
            "quotes_not_in_source": ungrounded,
            "experts_returned": len(result.experts),
            "expert_quotes_not_in_source": experts_ungrounded,
        }
    )
    return result


# ---------------------------------------------------------------------------
# What gets sent
# ---------------------------------------------------------------------------
def _bounded_source(notice_text: str) -> tuple[str, int, int]:
    """The canonical text to send, bounded at a whole-sentence boundary.

    Returns the text, how many sentences it holds, and how many the notice had.

    The guarantee that has to hold is that **a quote is checkable against what
    the model was shown**. Cutting at ``MAX_SOURCE_CHARS`` mid-word would break
    it in the worst possible way: the model would faithfully copy the half
    sentence it was given, the verifier would fail to find that fragment in the
    notice as a whole, and a correct extraction would be recorded as a
    hallucination. So sentences go in whole, in document order, and the first
    one that does not fit ends the payload.

    Stopping rather than skipping over the oversized sentence and continuing is
    also deliberate: a payload with a hole in it shows the model two clauses as
    neighbours that are not, and the qualification paragraph is exactly the
    place where a condition in one sentence governs the number in the next.

    One sentence longer than the whole budget is kept anyway. It means the
    splitter found no boundary — a notice body that is one unpunctuated block —
    and sending an over-budget notice is a bounded cost, while sending nothing
    is a silent gap in the corpus.
    """
    text = canonical(notice_text)
    if not text:
        return "", 0, 0

    fragments = sentences(text)
    kept: list[str] = []
    used = 0
    for fragment in fragments:
        # +1 for the space that rejoins them; the join has to reproduce the
        # canonical string exactly, or a quote spanning the seam stops matching.
        cost = len(fragment) + (1 if kept else 0)
        if kept and used + cost > MAX_SOURCE_CHARS:
            break
        kept.append(fragment)
        used += cost
    return " ".join(kept), len(kept), len(fragments)


def _worth_asking(payload: str, excluded: set[str]) -> str:
    """Why not to spend a request, or an empty string to spend one.

    Three refusals, cheapest first:

    * an empty body — there is nothing to read;
    * no requirement-shaped language anywhere in what would be sent. 58% of the
      corpus, measured. These notices are announcements, and a request buys the
      correct answer "nothing" at full price;
    * every requirement-shaped sentence is one an excluded key was already read
      from. The notice restates its criteria in the qualification paragraph and
      L1 read all of them; asking again is asking for what we hold.

    The third has a known cost, stated rather than hidden: a sentence that names
    an excluded criterion *and* an unrelated one in the same breath — "turnover
    of USD 10 million and at least five years of experience" — counts as read,
    and the unrelated half is lost. It requires every other requirement-shaped
    sentence in the notice to be covered too, which bounds how often it can
    happen, and the alternative is a paid request on every notice L1 already
    answered for free.
    """
    if not payload:
        return "empty notice body"

    candidates = [s for s in sentences(payload) if _REQUIREMENT_LANGUAGE_RE.search(s)]
    if not candidates:
        return "no requirement language in the notice body"

    topics = [pattern for key, pattern in _KEY_TOPICS.items() if key in excluded]
    if topics and all(
        any(pattern.search(sentence) for pattern in topics) for sentence in candidates
    ):
        return "every requirement sentence was already read by an earlier layer"
    return ""


# ---------------------------------------------------------------------------
# What gets asked
# ---------------------------------------------------------------------------
#: The per-notice half of the prompt. It sits in the user turn, after the cache
#: breakpoint, precisely because it varies — interpolating any of this into the
#: frozen system prompt would invalidate the cached prefix for every call after
#: it (see ``llm.SYSTEM_PROMPT``).
_TASK = """\
Below is the body of a World Bank procurement notice. Return every qualification
criterion the text itself states.

This is the notice, not the bidding document. Most notices state no criterion at
all and point at a section of a document that is not shown to you; for those,
return an empty list. "Qualification requirements are set out in Section III"
states no requirement."""

_ALREADY_KNOWN = """\

ALREADY EXTRACTED — DO NOT RETURN THESE
Deterministic rules have already read the following criteria out of this same
text, with their exact quotes:
{keys}
Do not return them again in any form, however the text phrases them. Return only
what those rules could not reach."""

#: Mirrors L1's own handling of relative periods (DECISIONS.md D6): an explicit
#: printed range always beats the arithmetic, and with no publication year the
#: condition is dropped rather than computed against today — a 2019 notice read
#: in 2026 would otherwise exclude every contract that qualified.
_WITH_YEAR = """\

TIME PERIODS
This notice was published in {year}. Where it requires work "within the last N
years" and prints no year range, the earliest qualifying year is {year} - N + 1.
Where it prints a range such as "(2016-2025)", use the printed range instead:
the borrower already did the arithmetic and its answer governs."""

_WITHOUT_YEAR = """\

TIME PERIODS
The publication year of this notice is not known. Where it requires work "within
the last N years" and prints no explicit year range, omit the year condition
entirely rather than computing it from today's date. Where it prints a range
such as "(2016-2025)", use the printed range."""


def _instruction(excluded: set[str], reference_year: int | None) -> str:
    """The per-notice instruction, assembled in a fixed order.

    Sorted keys, and the year block present in both branches: the instruction is
    part of what a prompt version identifies, so two runs over the same notice
    with the same inputs have to produce the same bytes.
    """
    parts = [_TASK]
    if excluded:
        keys = "\n".join(f"  - {key}" for key in sorted(excluded))
        parts.append(_ALREADY_KNOWN.format(keys=keys))
    parts.append(
        _WITH_YEAR.format(year=reference_year) if reference_year else _WITHOUT_YEAR
    )
    return "\n".join(parts)
