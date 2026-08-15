"""L3 — qualification criteria read out of a mirrored tender document.

The deepest layer of the extraction stack (DECISIONS.md D6) and the only
genuinely expensive one. Everything here is shaped by four facts about the
corpus rather than by any preference about design.

**What L3 reads is the consulting TOR, not the bidding document.** The plan was
a Section III parser; BLOK 0.1 measured every open Invitation for Bids in the
focus corpus and none of the seven publishes a fetchable bidding document, so
that parser has no input and was abandoned (DECISIONS.md D12). Consulting Terms
of Reference *are* reachable — nine of ten, every one with a text layer — and
they are what this module targets. Nothing here is IFB-specific, and a bidding
document that does become readable goes through the same path; but no accuracy
claim rests on that case.

**A document that fits is sent whole.** It did not used to be. Chunks were
scored and only the ones carrying qualification language were bought, on the
reasoning that most of a TOR is background and scope of work. Measured against
the corpus that is actually mirrored, the trade was the wrong way round: the
prefilter sends at most six 6 000-character chunks — 36 000 characters of an
average 67 000-character document — and it chooses them by counting keyword
families, so any criterion phrased outside that vocabulary is not wrong but
invisible. What it saves is a fraction of the cheap half of the bill, input
being $5/Mtok against $25 for output on Opus; a full pass over the whole corpus
at full context measures at roughly $7. So below ``DEFAULT_WHOLE_DOCUMENT_CHARS``
the document goes in as one request, and the prefilter now serves only the tail
that genuinely will not fit.

What is *kept* from that design is the document-level skip, which is the part
that was earning: a document with no qualification language anywhere selects
nothing and spends nothing. The judgement that was dropped is the one made
*inside* a document, which never had the evidence behind it that the
document-level one does.

**A chunk boundary must never cut a sentence.** Every requirement carries a
quote that the grounding verifier later searches for (D4). A quote that spans
the cut is present in the document but absent from the chunk the model was
shown, so it is scored as a hallucination that never happened — the extraction
was right and the measurement is wrong, which is the worst of the four
possible outcomes. Chunks are therefore assembled from whole units produced by
``text.sentences``, never from a character offset.

**The parsers' output is rougher than the harvester's status field suggests.**
``pypdf`` frequently emits one word per line, which ``text.canonical`` repairs
by collapsing whitespace; and ``status=fetched, has_text_layer=True`` includes
web pages that answered 200 with an error screen — the largest "TOR" in the
mirror is 400k characters of minified CSS from an expired ``cloud.mail.ru``
share. Neither is detected by a format check. Both are handled by the same
mechanism: text with no qualification language selects no chunks and spends no
request.

Like every other network-touching module here, failure is a return value.
A missing key, a refusal, a timeout on chunk 4 of 6 — all of them come back as
``LayerResult(error=...)`` carrying whatever the earlier chunks produced.
"""

from __future__ import annotations

import logging
import re
from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

from .extraction import Extracted, ExtractedExpert, LayerResult
from .llm import LLMConfig, extract_with_model
from .text import canonical, contains_quote, sentences

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# Tuning
# ---------------------------------------------------------------------------
# All four are parameters of ``extract`` rather than constants, because they are
# the axes the ablation in D6 varies. The defaults below are the ones measured
# against the mirror; see the module docstring for the size distribution.

#: Target size of one chunk, in canonical characters (~1,500 tokens).
#:
#: Not larger, and the reason is the prefilter rather than the token bill: the
#: prefilter decides at chunk granularity, so a 20k chunk drags twelve pages of
#: scope-of-work along with the one paragraph that mentioned a degree
#: requirement. Smaller chunks buy resolution. Not smaller than this either —
#: a qualification table split across four requests loses the heading that says
#: what its columns mean.
DEFAULT_CHUNK_CHARS = 6000

#: Whole units repeated between adjacent chunks. Two rather than one because a
#: requirement is regularly stated across a heading and the sentence under it
#: ("Required qualifications:" / "The Team Leader shall hold ..."), and a cut
#: between them leaves the second chunk with a threshold and no subject.
DEFAULT_OVERLAP_UNITS = 2

#: Most chunks a single document may be charged for.
#:
#: Six covers a median document (15.3k canonical characters ≈ 3 chunks)
#: entirely and bounds the upper quartile. It exists for the tail: the longest
#: document in the mirror produces 118 chunks, and one pathological row must
#: not be able to spend a day's budget. It binds on 17 of the 276 readable TOR;
#: raising it to 8 buys 24 more requests across the whole corpus, which is why
#: it is set where the tail is cut rather than where the median needs it.
#: Reaching the cap is recorded in ``notes`` so truncation is never silent.
DEFAULT_MAX_CHUNKS = 6

#: Above this a "sentence" is not one — it is a run of text whose boundaries
#: the parser lost. HTML documents in the mirror have a median of four
#: sentences for eleven thousand characters, because ``html_to_text`` marks
#: paragraphs with newlines and ``canonical`` collapses whitespace, so a page
#: whose paragraphs lack terminal punctuation arrives as a single blob. Such a
#: unit is split further; see ``_split_oversized``.
MAX_UNIT_CHARS = 1200


# ---------------------------------------------------------------------------
# Which parts of a document are worth paying for
# ---------------------------------------------------------------------------
# Marker families, not a bag of words. A chunk scores one point per *distinct*
# family it matches, so a page repeating "experience" forty times in a
# description of the work still scores one, while a paragraph that pairs a
# threshold with a credential scores two and is selected.
#
# The families were chosen against the 276 readable TOR in the mirror, where
# they occur in: qualification 73%, experience 73%, requirement 68%, minimum
# 51%, years-of-experience 48%, degree 44%, key experts 19%, credential 17%,
# eligibility 16%. They are ordinary markers of a stated condition — nothing
# here encodes a fact about World Bank procedure, which would need a source
# under the sourcing rule in docs/OPEN-QUESTIONS.md.
#
# **Each family carries its Russian form, and that was forced by measurement.**
# With English patterns alone, 19 of the 20 Cyrillic TOR in the mirror selected
# no chunk at all, against 35 of 256 for the Latin-script ones — and most of
# those 35 are not documents but expired share pages. An English-only filter
# does not degrade gracefully on a CIS corpus; it silently returns nothing for
# a borrower that published in Russian, which is the failure mode hardest to
# notice from an accuracy table. The Russian terms are ordinary vocabulary, not
# procurement terms of art.
_MARKERS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("qualification", re.compile(r"qualificat|квалификац", re.I)),
    ("eligibility", re.compile(r"eligib|критери\w*\s+отбора|соответстви\w+\s+требовани", re.I)),
    ("threshold", re.compile(
        r"\bat least\b|\bminimum\b|\bnot? less than\b|\bno fewer than\b"
        r"|не\s+менее|как\s+минимум|не\s+ниже", re.I)),
    ("experience_years", re.compile(
        r"\b\d{1,2}\+?\s*(?:\(\d{1,2}\)\s*)?years?\b|years?\s+of\s+(?:\w+\s+){0,2}experience"
        r"|\b\d{1,2}\s*(?:лет|год[аеу]?)\b|опыт\w*\s+работы|стаж\w*", re.I)),
    ("obligation", re.compile(
        r"\b(?:shall|should|must)\s+(?:have|hold|possess|demonstrate|provide)\b|\brequirements?\b"
        r"|долж\w+\s+(?:иметь|обладать|располагать|владеть)|требовани\w+", re.I)),
    ("key_experts", re.compile(
        r"key\s+experts?|key\s+staff|team\s+leader|proposed\s+(?:staff|personnel|experts?)"
        r"|ключев\w+\s+эксперт|руководител\w+\s+групп|состав\w*\s+специалист", re.I)),
    ("education", re.compile(
        r"(?:master|bachelor|university|advanced|postgraduate)'?s?\s+degree"
        r"|\bph\.?\s?d\b|\bm\.?sc\b|\bb\.?sc\b"
        r"|высш\w+\s+образовани|степен\w+\s+(?:магистра|бакалавра)|диплом\w*", re.I)),
    ("financial", re.compile(
        r"turnover|liquid assets|financial (?:capacity|standing|capability)"
        r"|audited (?:financial|accounts)|net worth"
        r"|годов\w+\s+оборот|финансов\w+\s+(?:состояни|устойчивост|отчетност)", re.I)),
    ("credential", re.compile(
        r"\biso\s*\d|certificat|accredit|licen[cs]e|registration\s+(?:with|as)"
        r"|сертифика\w*|лицензи\w*|аккредит\w*", re.I)),
    ("shortlist", re.compile(r"short-?list|(?:корот|крат)к\w+\s+список", re.I)),
)

#: Send the document whole below this many canonical characters.
#:
#: 250 000 characters is ~62 000 input tokens, which on Opus is about $0.31 —
#: and it covers 46 of the 47 documents in the mirrored corpus. The one it does
#: not cover is a 400 000-character row that is itself the harvester's
#: truncation ceiling; that one falls back to the chunk prefilter, which is what
#: the prefilter is now for: pathological documents, not ordinary ones.
DEFAULT_WHOLE_DOCUMENT_CHARS = 250_000

#: A chunk needs this many distinct families to be bought.
#:
#: Two, not one. One family alone is met by ordinary scope prose — a deliverable
#: due "within 6 months" matches ``experience_years``, and "the Client's
#: requirements" matches ``obligation``. Requiring two is what separates a
#: paragraph that states a condition from a paragraph that mentions a number.
#: The fallback in ``_select`` keeps this from silencing a document entirely.
#:
#: Measured over the 276 readable TOR in the mirror, at the defaults above:
#:
#:   min_score  requests  median share of document sent
#:       1         748              87%
#:       2         566              51%
#:       3         441              43%
#:
#: Three is 22% cheaper and was rejected. The number of documents that select
#: nothing is 38 either way (the fallback absorbs the difference) and the
#: highest-scoring chunk survives at every setting, so the whole choice is
#: about the *second* chunk — and the second chunk is where a criterion stated
#: outside the qualification section lives. Recall is what D6 measures; the
#: cost difference is a few hundred requests once.
DEFAULT_MIN_SCORE = 2


# ---------------------------------------------------------------------------
# What the model is asked
# ---------------------------------------------------------------------------
# The rules live in the frozen system prompt in ``llm.py``; this is only the
# per-call framing, and it sits in the user turn after the cache breakpoint so
# interpolating into it costs nothing.
_INSTRUCTION = (
    "The text below is an extract from a tender document — a Terms of Reference "
    "or bidding document — that a procurement notice linked to. It is one part "
    "of a longer document and may begin or end mid-section.\n\n"
    "Extract the qualification criteria stated in THIS extract only. Do not "
    "complete a sentence that is cut off at either end, and do not restate a "
    "criterion you can only infer from a heading whose text is not here: quote "
    "what is present or return nothing."
)


@dataclass(frozen=True)
class Chunk:
    """One contiguous run of whole units, and why it was or was not bought."""

    #: 1-based position in document order. Reaches the user as ``tor:chunk 3/7``.
    index: int
    total: int
    text: str
    #: Distinct marker families matched. Length is the selection score.
    markers: tuple[str, ...] = ()

    @property
    def score(self) -> int:
        return len(self.markers)


@dataclass
class _Refusal:
    """Why a document was not read, in the harvester's own vocabulary."""

    reason: str
    note: str


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------
def extract(
    document: Any,
    *,
    reference_year: int | None = None,
    exclude_keys: Iterable[str] = (),
    config: LLMConfig | None = None,
    client: Any | None = None,
    chunk_chars: int = DEFAULT_CHUNK_CHARS,
    overlap_units: int = DEFAULT_OVERLAP_UNITS,
    max_chunks: int = DEFAULT_MAX_CHUNKS,
    min_score: int = DEFAULT_MIN_SCORE,
    whole_document_chars: int = DEFAULT_WHOLE_DOCUMENT_CHARS,
    role_slugs: list[str] | None = None,
) -> LayerResult:
    """Every requirement L3 can read out of one mirrored document.

    ``document`` is a :class:`apps.tenders.models.HarvestedDocument`. Only its
    fields are touched, never the database, so the layer stays testable against
    an unsaved row and the pipeline keeps sole responsibility for persistence.

    ``reference_year`` is accepted for signature parity with L1 and
    deliberately unused. L1 needs it to turn "within the last five years" into a
    year floor by subtraction; L3 must not do that arithmetic, because the
    result would be a number that appears nowhere in the document and therefore
    cannot be quoted. A document states its own dates, and where it does not,
    the honest output is a window we did not invent.

    ``exclude_keys`` are keys a cheaper layer already established. They are
    named in the instruction *and* filtered out of the result: naming them saves
    output tokens, filtering them is what actually holds when the model names
    one anyway. Note what this costs — L3 reading a fuller version of a
    criterion L1 half-read from the notice is exactly the case L3 exists for,
    and excluding the key discards it. That policy belongs to the pipeline,
    which decides what to exclude; this layer only honours it.

    ``role_slugs`` asks for expert positions alongside the requirements (D20).
    This is the layer where it pays most: a consulting TOR names its team in a
    dedicated section, in full, while the notice that linked it usually says
    only that a team is needed.
    """
    # ``model`` is deliberately left empty until a response arrives: ``merge``
    # keeps the first non-empty value, so pre-filling it with the configured
    # model would report the tier we asked for rather than the one that
    # answered — and the D6 ablation is a table of what actually ran.
    result = LayerResult()

    refusal = _refuse(document)
    if refusal is not None:
        # No request is spent, and the reason is the harvester's own — "we
        # cannot read this" and "there is nothing to read" are different facts
        # about the corpus and the ablation counts them separately.
        result.model = (config or LLMConfig.resolve()).model
        result.error = refusal.reason
        result.notes["refused"] = refusal.note
        return result

    text = canonical(document.text)
    units = _units(text)

    # A document that fits goes in whole, as one request.
    #
    # The prefilter below it was written as a cost mechanism and it was a bad
    # trade, which only became visible once the numbers were measured against
    # the real corpus: it sends at most six 6 000-character chunks — 36 000 of
    # an average 67 000-character document — and it chooses them by counting
    # keyword families. What it saves is a fraction of the cheap half of the
    # bill (input is $5/Mtok against $25 for output on Opus); what it costs is
    # every criterion phrased in words the marker list does not contain, and
    # those are invisible rather than wrong. A full pass over the whole mirrored
    # corpus at full context measures at roughly $7.
    #
    # The document-level skip below is kept, and it is the part that was
    # actually earning: an expired share page or a pure scope-of-work TOR still
    # costs nothing. What is dropped is deciding *within* a document, which is
    # the decision there was never good evidence for.
    whole = len(text) <= whole_document_chars
    if whole:
        chunks = _score([Chunk(index=1, total=1, text=text)])
        # `min_score` still applies, so "no qualification language anywhere in
        # this document" remains free rather than becoming a paid request.
        selected = [c for c in chunks if c.score >= min_score]
    else:
        chunks = _chunk(text, units, chunk_chars=chunk_chars, overlap_units=overlap_units)
        chunks = _score(chunks)
        selected = _select(chunks, max_chunks=max_chunks, min_score=min_score)

    result.notes.update(
        {
            "document_chars": len(text),
            "units": len(units),
            "whole_document": whole,
            "chunks_total": len(chunks),
            "chunks_selected": len(selected),
            "chunks_skipped": len(chunks) - len(selected),
            "selected_chars": sum(len(c.text) for c in selected),
            "chunk_cap_reached": (
                not whole and len(selected) == max_chunks and len(chunks) > max_chunks
            ),
        }
    )
    if not selected:
        # Not an error: a document with no qualification language anywhere is a
        # measurement about the corpus, and it is the case that keeps expired
        # share pages and pure scope-of-work TOR from costing anything.
        result.model = (config or LLMConfig.resolve()).model
        result.notes["refused"] = "no chunk carried qualification language"
        return result

    kind = getattr(document, "kind", "") or "document"
    excluded = {key for key in exclude_keys if key}
    instruction = _instruction_for(excluded)

    seen: set[tuple[str, str]] = set()
    seen_experts: set[tuple[str, str]] = set()
    for position, chunk in enumerate(selected):
        chunk_result = extract_with_model(
            chunk.text,
            instruction,
            config=config,
            client=client,
            source=f"{kind}:chunk {chunk.index}/{chunk.total}",
            source_document_id=getattr(document, "pk", None),
            role_slugs=role_slugs,
        )
        chunk_result.notes["chunks_read"] = 1
        chunk_result.requirements = _keep(
            chunk_result.requirements, chunk=chunk, excluded=excluded, seen=seen,
            notes=chunk_result.notes,
        )
        chunk_result.experts = _keep_experts(
            chunk_result.experts, chunk=chunk, seen=seen_experts,
            notes=chunk_result.notes,
        )
        result.merge(chunk_result)

        if chunk_result.error:
            # Stop, and keep what the earlier chunks produced. An error out of
            # ``extract_with_model`` is almost always a condition of the run —
            # no key, a rate limit, a timeout — not of this chunk's text, so
            # continuing would multiply one systemic failure by the chunk cap
            # and delay the partial result the caller can still use.
            result.notes["chunks_aborted"] = len(selected) - position - 1
            break

    return result


def best_document(notice: Any) -> Any | None:
    """The one document of a notice's several that L3 should read.

    A notice routinely links a TOR, a project appraisal document and a form or
    two. Ordering is by kind first, because D12 established that the TOR is
    where consulting qualification criteria live and the others are background;
    then by extracted length, because borrowers re-upload the same file and the
    shorter copy is normally the truncated parse rather than a different
    document.

    Returns ``None`` when nothing attached to the notice is readable — which
    the pipeline should record, not treat as an extraction failure.
    """
    order = {"tor": 0, "bidding": 1, "project_doc": 2, "other": 3}
    candidates = [
        document
        for document in notice.harvested_documents.all()
        if _refuse(document) is None
    ]
    if not candidates:
        return None
    return min(candidates, key=lambda d: (order.get(d.kind, 9), -d.text_chars))


# ---------------------------------------------------------------------------
# Refusal
# ---------------------------------------------------------------------------
def _refuse(document: Any) -> _Refusal | None:
    """Whether this document must not be sent, and why.

    The harvester already drew every line that matters here, and this function
    does no more than read its verdict. ``has_text_layer is False`` means a
    scan; ``status != fetched`` means the bytes never arrived or arrived as a
    sign-in wall; ``text_chars`` below ``MIN_USEFUL_CHARS`` means the fetch
    succeeded and produced a cover page. Re-deciding any of that here would put
    two different definitions of "readable" in the codebase, and the one in the
    corpus statistics would stop matching the one in the extraction runs.
    """
    status = getattr(document, "status", "")
    if status != "fetched":
        return _Refusal(f"document not fetched (status={status or 'unknown'})", "not_fetched")
    if getattr(document, "has_text_layer", None) is False:
        return _Refusal("document has no text layer", "no_text_layer")

    minimum = getattr(type(document), "MIN_USEFUL_CHARS", 200)
    if (getattr(document, "text_chars", 0) or 0) < minimum:
        return _Refusal(f"document text below {minimum} characters", "too_short")
    if not (getattr(document, "text", "") or "").strip():
        # ``text_chars`` is a stored counter and the text is the thing actually
        # sent; a row where they disagree is a harvest bug, and paying for an
        # empty request would hide it.
        return _Refusal("document text is empty", "empty_text")
    return None


# ---------------------------------------------------------------------------
# Chunking
# ---------------------------------------------------------------------------
#: Boundaries inside an over-long unit that a real sentence does not cross: a
#: semicolon or colon that ends a clause, a bullet, a lettered sub-item, or a
#: numbered clause like "3.2". Preferred over cutting at a word because each of
#: these is a boundary the *document* drew and the parser flattened.
#:
#: The hyphen is in the bullet class because ``pypdf`` renders every bullet
#: glyph in this corpus as "- ", and a qualification list is exactly where that
#: matters: "practice. - At least five (5) years of experience ..." is not a
#: sentence boundary to ``text.sentences`` (the next character is not a capital)
#: so the whole list arrives as one unit. Requiring a following capital keeps it
#: off "Dushanbe -2" and off a date range.
_SOFT_BOUNDARY_RE = re.compile(
    r"(?<=[;:])\s+"
    r"|\s+(?=[-–—•▪·o]\s+[A-Z])"
    r"|\s+(?=\(?[a-z]\)\s+[A-Z])"
    r"|\s+(?=\d{1,2}(?:\.\d{1,2})+\s+[A-Z])"
)


#: One unit, as ``(start, end)`` offsets into the canonical document text.
Span = tuple[int, int]


def _units(text: str) -> list[Span]:
    """The atoms a chunk is built from, as offsets rather than strings.

    Offsets, and this is not a micro-optimisation. A chunk is then produced as
    ``text[first_start:last_end]`` — a **literal slice** of the canonical
    document — so "a quote verified against the chunk is verified against the
    document" holds by construction rather than by an argument about how the
    pieces were rejoined. That argument turned out to be wrong: an earlier
    version split into strings and rejoined them with a single space, which is
    correct for prose but inserted a space that was never there in the 51
    mirrored documents whose "text" is minified CSS from an expired
    ``cloud.mail.ru`` share, where a 1,200-character run contains no whitespace
    at all. Those documents are never selected, so the bug was harmless — and
    it would have stayed invisible until the day it was not.

    Units are sentences, except where the source had none to give: see
    ``MAX_UNIT_CHARS``. An over-long one is broken on boundaries the document
    itself drew, and only failing that on a word boundary.
    """
    spans: list[Span] = []
    cursor = 0
    for sentence in sentences(text):
        # ``sentences`` returns stripped substrings in order, so a forward scan
        # recovers where each one sat. ``find`` failing would mean the splitter
        # rewrote its input; the fallback keeps the walk monotonic rather than
        # raising, because a malformed document is data.
        start = text.find(sentence, cursor)
        if start < 0:
            start = cursor
        end = min(len(text), start + len(sentence))
        cursor = end
        if end - start <= MAX_UNIT_CHARS:
            spans.append((start, end))
        else:
            spans.extend(_split_oversized(text, start, end))
    return spans


def _split_oversized(text: str, start: int, end: int) -> list[Span]:
    """Break one over-long unit, preferring boundaries the document drew."""
    unit = text[start:end]
    cuts = sorted({0, len(unit)} | {m.end() for m in _SOFT_BOUNDARY_RE.finditer(unit)})

    spans: list[Span] = []
    for left, right in zip(cuts, cuts[1:]):
        while right - left > MAX_UNIT_CHARS:
            # Last resort. Cut at the last space before the limit so no word is
            # broken: a clause cut at a bullet is incomplete, a clause cut
            # mid-word is unreadable *and* unquotable. Where there is no space
            # to cut at — minified CSS, never prose — the limit is taken as is.
            cut = unit.rfind(" ", left, left + MAX_UNIT_CHARS)
            if cut <= left:
                cut = left + MAX_UNIT_CHARS
            spans.append((start + left, start + cut))
            left = cut
        if right > left:
            spans.append((start + left, start + right))
    return spans


def _chunk(text: str, units: list[Span], *, chunk_chars: int, overlap_units: int) -> list[Chunk]:
    """Overlapping runs of whole units, each one a slice of ``text``.

    The slice runs from the first unit's start to the last unit's end, so the
    separators between units are carried along unchanged and the chunk is
    byte-identical to that stretch of the document.
    """
    if not units:
        return []

    windows: list[list[Span]] = []
    current: list[Span] = []
    index = 0
    while index < len(units):
        unit = units[index]
        if current and unit[1] - current[0][0] > chunk_chars:
            windows.append(current)
            # Step back so the next window repeats the tail of this one.
            back = min(overlap_units, len(current) - 1)
            tail = current[len(current) - back :] if back > 0 else []
            # An overlap large enough to leave no room for the unit that forced
            # the break would emit the same window again and never advance.
            # Shrinking it is the only exit; at worst the overlap is dropped,
            # which costs continuity on one boundary rather than hanging.
            while tail and unit[1] - tail[0][0] > chunk_chars:
                tail = tail[1:]
            current = tail
            continue
        current.append(unit)
        index += 1
    if current:
        windows.append(current)

    total = len(windows)
    return [
        Chunk(index=position, total=total, text=text[window[0][0] : window[-1][1]])
        for position, window in enumerate(windows, start=1)
    ]


# ---------------------------------------------------------------------------
# Selection
# ---------------------------------------------------------------------------
def _score(chunks: list[Chunk]) -> list[Chunk]:
    scored: list[Chunk] = []
    for chunk in chunks:
        markers = tuple(name for name, pattern in _MARKERS if pattern.search(chunk.text))
        scored.append(
            Chunk(index=chunk.index, total=chunk.total, text=chunk.text, markers=markers)
        )
    return scored


def _select(chunks: list[Chunk], *, max_chunks: int, min_score: int) -> list[Chunk]:
    """The chunks worth a request, in document order.

    **The risk, stated plainly: what is not selected cannot be quoted, and
    therefore cannot be extracted.** A qualification stated in language none of
    the marker families match is invisible to L3 and will read in the ablation
    as a miss by the model rather than by the filter. That is the trade for not
    paying for a whole document, and it is measurable — running with
    ``min_score=0`` sends everything and the difference between the two runs is
    the filter's own recall.

    Where that lands on the real corpus: 238 of the 276 readable TOR select at
    least one chunk, at a median of 51% of the document — and **all 200 of the
    PDF-parsed ones do**. Every one of the 38 that select nothing is an HTML
    row: expired ``cloud.mail.ru`` and Yandex.Disk share pages, Google Drive
    viewer shells, CMS chrome, a CAPTCHA screen. All of them are stored as
    ``fetched`` with a text layer, because what arrived was real HTML. So the
    filter is also the cheapest guard the pipeline has against paying to read
    an error page, and the split it produces is not arbitrary — it lands almost
    exactly on the line between a document and a web page that D7 says the
    corpus statistics conflate.

    Two softenings of a pure threshold, each for an observed shape:

    * **A document that reaches the threshold nowhere falls back to score 1.**
      Short TOR state the whole qualification in one line ("The consultant
      should have at least 5 years of experience"), which matches one family.
      Returning nothing there would be a filter artefact, not a fact about the
      document.
    * **Ties are broken by position, earliest first.** Qualification sections
      sit at the end of a TOR far more often than at the front, but a summary
      table near the front states the same criteria more compactly; taking the
      earlier one and letting the dedup drop the restatement is cheaper than
      the reverse.
    """
    ranked = [c for c in chunks if c.score >= min_score]
    if not ranked and min_score > 1:
        ranked = [c for c in chunks if c.score >= 1]
    if not ranked:
        return []

    ranked.sort(key=lambda c: (-c.score, c.index))
    return sorted(ranked[:max_chunks], key=lambda c: c.index)


def _instruction_for(excluded: set[str]) -> str:
    if not excluded:
        return _INSTRUCTION
    names = ", ".join(sorted(excluded))
    return (
        f"{_INSTRUCTION}\n\nThese criteria have already been read from another "
        f"source and must not be returned again: {names}."
    )


# ---------------------------------------------------------------------------
# Post-processing one chunk's rows
# ---------------------------------------------------------------------------
def _keep(
    rows: list[Extracted],
    *,
    chunk: Chunk,
    excluded: set[str],
    seen: set[tuple[str, str]],
    notes: dict[str, Any],
) -> list[Extracted]:
    """Filter one chunk's requirements: excluded keys, duplicates, quotes.

    **Unverifiable quotes are passed through, not dropped**, and this is the one
    decision here worth arguing. ``Grounding.NOT_FOUND`` exists precisely to
    hold a requirement whose quote could not be found: the row is kept out of
    every verdict but stays in the table, "because deleting the row would
    destroy the measurement it exists to produce". Dropping it in the layer
    would delete it before the pipeline ever saw it, and the hallucination rate
    — the number D4 says the whole approach is judged on — would silently read
    as zero. So L3 counts them, and lets the pipeline classify them.

    Note the asymmetry with ``llm._to_extracted``, which *does* drop a row with
    no quote at all. The two are different events: an empty quote is a model
    declining to claim, a quote that is not in the source is a model claiming
    something the source does not say. Only the second is a hallucination.
    """
    kept: list[Extracted] = []
    for row in rows:
        if row.key in excluded:
            notes["excluded_keys_dropped"] = notes.get("excluded_keys_dropped", 0) + 1
            continue

        # Same criterion, same sentence, seen in an earlier chunk: the overlap
        # window guarantees this, and a summary table restating the body
        # produces it even without overlap. First occurrence wins — it is the
        # one whose chunk index the user will be sent to.
        fingerprint = (row.key, canonical(row.evidence_quote).casefold())
        if fingerprint in seen:
            notes["duplicates_dropped"] = notes.get("duplicates_dropped", 0) + 1
            continue
        seen.add(fingerprint)

        if not contains_quote(chunk.text, row.evidence_quote):
            notes["quotes_unverified"] = notes.get("quotes_unverified", 0) + 1
        kept.append(row)
    return kept


def _keep_experts(
    rows: list[ExtractedExpert],
    *,
    chunk: Chunk,
    seen: set[tuple[str, str]],
    notes: dict[str, Any],
) -> list[ExtractedExpert]:
    """The same filter for expert positions, minus the exclusion step.

    There is no ``excluded`` here and there should not be: exclusion exists so a
    metered layer is not billed for a criterion a free rule already read, and no
    rule reads expert positions — L1 has no pattern for them, so nothing
    shallower can have established one.

    What remains is the deduplication the chunk overlap makes necessary, keyed
    on the position and its sentence rather than on the role. A TOR that asks
    for two different Environmental Specialists in two sections is asking for
    two people, and collapsing them on role alone would quietly halve the team.
    """
    kept: list[ExtractedExpert] = []
    for row in rows:
        fingerprint = (
            canonical(row.title).casefold(),
            canonical(row.evidence_quote).casefold(),
        )
        if fingerprint in seen:
            notes["expert_duplicates_dropped"] = (
                notes.get("expert_duplicates_dropped", 0) + 1
            )
            continue
        seen.add(fingerprint)

        if not contains_quote(chunk.text, row.evidence_quote):
            notes["expert_quotes_unverified"] = (
                notes.get("expert_quotes_unverified", 0) + 1
            )
        kept.append(row)
    return kept
