"""The source beside the criteria: what the split view is served from.

A vendor reading "the bidder shall have an average annual turnover of USD 22.4
million" has one reasonable next question — *where does it say that* — and the
quote on the card only half answers it. This module assembles the other half:
the text the criteria were read out of, and where in it each criterion's quote
sits, so pressing a criterion scrolls the source to the sentence and draws a box
round it.

Four things it is careful about.

**The source is chosen by the evidence, not by a preference.** A notice's
criteria can come from the notice body, from a Terms of Reference we mirrored,
or from a file a vendor handed over, and in this corpus the first is by far the
commonest — L1 reads the notice body and most notices link nothing readable. So
every candidate source is scored by *how many of the shown quotes are actually
in it*, and the winner is the one that can evidence the most rows. A rule that
preferred the TOR would open an empty document beside a page of criteria read
from the notice.

**A PDF gets rectangles; everything else gets characters.** Where the winning
source is a mirrored PDF, ``spans`` indexes it into lines with page geometry and
the viewer draws boxes on the rendered page. Where it is the notice body, an
HTML page or a DOCX, there is no page to point at — so the pane renders the text
and the highlight is a character range. Both answer the same question; only one
of them can be dressed up as the original document, and pretending otherwise
would mean inventing coordinates.

**The highlight is never a new claim.** Locations come from searching for the
requirement's already-verified quote (D4). No model is asked where a criterion
sits and no fuzzy match is attempted: a quote that cannot be located has no
highlight, and the card still shows the quote it was always showing. A
near-match would put a box on a line that does not say what the card says, which
is worse than no box — the vendor would read it as proof.

**It degrades all the way down.** No pdfplumber, a scan, a stored blob that has
gone missing, a document past the page cap: each produces a payload naming which
of those happened and carrying whatever else is available.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path
from typing import Any, Sequence

from django.conf import settings
from django.db import transaction

from apps.tenders.models import HarvestedDocument, TenderNotice

from . import spans as spans_module
from .models import ComplianceSpan, TenderRequirement
from .text import BLOCK_BOUNDARY_RE, canonical

logger = logging.getLogger(__name__)

#: What marks a document as having pages to point at.
#:
#: Two signals rather than one, because neither is reliable alone: a borrower's
#: server frequently serves a PDF as ``application/octet-stream``, and the
#: stored suffix is derived from whichever parser succeeded.
_PDF_HINTS = ("pdf",)

#: How many mirrored documents are considered as the source. The same cap and
#: the same reason as ``pipeline.L3_MAX_DOCUMENTS``: scoring a candidate means
#: canonicalising its whole text, and a notice can link a dozen files.
MAX_CANDIDATES = 4

#: Why a source has no rectangles. A single free-text field would be easier and
#: would leave the client matching on prose; these are the states the interface
#: renders differently.
UNSUPPORTED = "not_a_pdf"
MISSING_FILE = "file_missing"
NO_TEXT_LAYER = "no_text_layer"
PARSER_UNAVAILABLE = "parser_unavailable"

#: What the pane is showing. ``notice_body`` is not a lesser answer — for most
#: of this corpus it is the only text that states a criterion at all.
NOTICE_BODY = "notice_body"
DOCUMENT = "document"


# ---------------------------------------------------------------------------
# Choosing what to show
# ---------------------------------------------------------------------------
#: The point of blocks is *the line breaks*: a vendor comparing the pane against
#: the tender page should see the same paragraph rhythm in both, because a wall
#: of run-together text is a different document to read even when every
#: character matches. Inline emphasis is not preserved — the pane renders
#: canonical text, which is the form the offsets are measured in.
#:
#: What a block's tag maps onto in the pane. Anything unrecognised is a
#: paragraph, which is the safe direction: a wrong tag changes the spacing, a
#: dropped block loses the text.
_BLOCK_TAGS = {"h1": "h3", "h2": "h3", "h3": "h3", "h4": "h4", "h5": "h4", "h6": "h4",
               "li": "li", "blockquote": "blockquote"}


class _Candidate:
    """One text the criteria might have been read out of, canonicalised once.

    Canonical because that is the form quotes are verbatim against
    (``text.canonical``) — the same reason grounding uses it. Searching the raw
    text instead would miss every quote whose source had an ``&nbsp;`` or a
    curly apostrophe in it, which in this corpus is most of them.

    A candidate is a **list of blocks**, not one string, so the pane can
    reproduce the source's paragraph structure. A plain-text document is one
    block; HTML is split on its own block elements. The locator joins them back
    with a single space and hands back offsets *within* a block, so what is
    rendered and what is searched are the same text (``spans.Locator``).
    """

    def __init__(self, kind: str, document: HarvestedDocument | None, raw: str, *, html: bool):
        self.kind = kind
        self.document = document
        #: The exact string the quotes were copied out of. Everything else here
        #: is an index into it, never a reconstruction of it.
        self.text = canonical(raw)
        self._spans = _split_blocks(raw, self.text) if html else _plain_blocks(self.text)
        self._haystack = self.text.casefold()

    @property
    def blocks(self) -> list[tuple[str, str]]:
        """``(tag, text)`` per block, for rendering."""
        return [(tag, self.text[start:end]) for tag, start, end in self._spans]

    def find(self, quote: str) -> list[tuple[int, int, int]]:
        """``(block index, start, end)`` per block the quote covers.

        Offsets are relative to the block, because the pane renders one block at
        a time. They are exact rather than approximate: both the quote and the
        blocks are located inside the same canonical string, so the arithmetic
        is slicing rather than matching.
        """
        needle = canonical(quote).casefold()
        if not needle:
            return []
        at = self._haystack.find(needle)
        if at < 0:
            return []
        end = at + len(needle)
        return [
            (index, max(at - start, 0), min(end, stop) - start)
            for index, (_, start, stop) in enumerate(self._spans)
            if start < end and stop > at
        ]

    def score(self, quotes: Sequence[str]) -> int:
        return sum(1 for quote in quotes if self.find(quote))


def _split_blocks(raw: str, flat: str) -> list[tuple[str, int, int]]:
    """Where each of the notice's paragraphs sits inside ``flat``.

    **The blocks are found in the canonical string, not rebuilt into one.** The
    first two attempts both rebuilt: pair `<p>…</p>` with a regex and join the
    results, then split on boundaries and join with what ``canonical`` would
    have inserted. Both were wrong in ways that only show up as a highlight that
    silently never appears — the first lost whole paragraphs to nested `<div>`
    elements, the second differed from ``canonical`` by five characters across a
    5 000-character notice, because a paragraph ending in a space becomes
    ``"text . "`` and not ``"text. "``.

    Reconstruction is the mistake. ``flat`` is the very string the quotes were
    copied out of, so a block only has to be *located* in it: canonicalise each
    piece the boundaries cut out, walk forward through ``flat`` finding them in
    order, and keep the offsets. Then a quote's position and a block's position
    are measured against the same ruler and cannot disagree.

    A piece that cannot be found is skipped rather than guessed at. It costs one
    paragraph of the pane and can never misplace a highlight.
    """
    blocks: list[tuple[str, int, int]] = []
    cursor = 0
    for piece in BLOCK_BOUNDARY_RE.split(raw or ""):
        text = canonical(piece)
        if not text:
            continue
        at = flat.find(text, cursor)
        if at < 0:
            continue
        blocks.append((_tag_of(piece), at, at + len(text)))
        cursor = at + len(text)
    return blocks or _plain_blocks(flat)


def _tag_of(piece: str) -> str:
    """What the chunk was inside, for rendering. A paragraph unless it says so.

    Read off the last opening tag in the chunk rather than the first: the chunk
    begins with whatever tags closed before it, and the element that actually
    contains the text is the innermost one opened.
    """
    found = re.findall(r"<(h[1-6]|li|blockquote)\b", piece, re.IGNORECASE)
    return _BLOCK_TAGS.get(found[-1].lower(), "p") if found else "p"


def _plain_blocks(flat: str) -> list[tuple[str, int, int]]:
    """A document with no markup: one block, the whole of it.

    Splitting extracted text into paragraphs would mean guessing where they
    were, and the parsers this corpus runs on do not preserve that reliably —
    ``pypdf`` frequently emits one word per line. A guessed paragraph break is a
    line break the source does not have, which is the one thing the pane is
    supposed to get right.
    """
    return [("p", 0, len(flat))] if flat else []


def choose_source(
    notice: TenderNotice, rows: Sequence[TenderRequirement]
) -> _Candidate | None:
    """The text that evidences the most of what is being shown.

    **A tie goes to the real document, and to a PDF before anything else.** It
    used to go to the notice body, on the reasoning that it is the shorter read
    and always loads — and that was measurably the wrong call. A Terms of
    Reference normally restates the notice's qualification list word for word,
    so the two tie at every criterion, and the tie-break was quietly opening the
    announcement beside a page whose own strip said a TOR was held. The vendor
    asking "where does it say that" wants the borrower's document, and a PDF
    answers with a box on the rendered page rather than a mark in a transcript.

    The notice body still wins when it can evidence strictly more rows, which is
    the case that matters: most notices link nothing readable at all (D12).

    Returns ``None`` when nothing states anything: no notice body and no
    readable document, or no requirements to evidence.
    """
    quotes = [row.evidence_quote for row in rows if row.evidence_quote]

    candidates: list[_Candidate] = []
    if notice.notice_text_sanitized:
        candidates.append(
            _Candidate(NOTICE_BODY, None, notice.notice_text_sanitized, html=True)
        )

    priority = {
        HarvestedDocument.Kind.TOR: 0,
        HarvestedDocument.Kind.BIDDING: 1,
        HarvestedDocument.Kind.PROJECT_DOC: 2,
        HarvestedDocument.Kind.OTHER: 3,
    }
    documents = sorted(
        notice.harvested_documents.usable(),
        key=lambda doc: (priority.get(doc.kind, 9), doc.url),
    )[:MAX_CANDIDATES]
    candidates.extend(
        _Candidate(DOCUMENT, doc, doc.text, html=False) for doc in documents
    )

    candidates = [candidate for candidate in candidates if candidate.blocks]
    if not candidates:
        return None
    if not quotes:
        # Nothing to evidence, so nothing to choose between. The richest
        # candidate is still worth opening — a reader with no extracted criteria
        # is exactly the one who wants to read the tender themselves.
        return max(candidates, key=_richness)

    return max(candidates, key=lambda candidate: (candidate.score(quotes), *_richness(candidate)))


def _richness(candidate: _Candidate) -> tuple[int, int]:
    """How good a *read* a candidate is, used only to break a scoring tie.

    A PDF outranks any other document and any document outranks the notice
    body, because that is the order in which they answer the question the pane
    exists for: the borrower's own page, then the borrower's own words, then the
    announcement about them.
    """
    if candidate.document is None:
        return (0, 0)
    return (2 if is_pdf(candidate.document) else 1, 1)


# ---------------------------------------------------------------------------
# Rectangles, for the sources that have pages
# ---------------------------------------------------------------------------
def is_pdf(document: HarvestedDocument) -> bool:
    haystack = f"{document.content_type} {document.stored_path} {document.parser}".lower()
    return any(hint in haystack for hint in _PDF_HINTS)


def stored_bytes(document: HarvestedDocument) -> bytes | None:
    """The original file, or ``None`` when the blob is not on this disk.

    A missing blob is an ordinary state, not a corruption: the database is
    restored from a dump far more often than the harvest volume is, so a
    deployment routinely holds rows whose files it has never had. It reads as
    "no rectangles" rather than as an error.
    """
    if not document.stored_path:
        return None
    path = Path(document.stored_path)
    if not path.is_absolute():
        path = Path(settings.HARVEST["DIR"]) / path
    try:
        return path.read_bytes()
    except OSError as exc:
        logger.info("Stored document %s is not readable: %s", document.pk, exc)
        return None


def ensure_indexed(document: HarvestedDocument) -> tuple[list[spans_module.Span], str]:
    """The document's line index, building it on first use.

    Returns the spans and a problem code — empty when there is nothing to
    report. Both halves matter: an empty list with no problem means a document
    that indexed to nothing, which is a different thing to tell a reader than a
    document this build cannot parse.

    Indexed lazily, and that is a deliberate placement. Doing it in the
    harvester would have parsed hundreds of documents nobody has opened, most of
    them for tenders that closed months ago; doing it in the extraction pipeline
    would tie a viewer feature to a metered path. A document nobody has looked
    at is not worth a parse.

    The write is best-effort and never blocks the answer. Two requests arriving
    together both index and one loses the unique constraint; the loser still has
    its spans in hand, because the point of the write is to save the *next*
    request a parse, not to serve this one.
    """
    stored = list(document.spans.all())
    if stored:
        return [_from_row(row) for row in stored], ""

    if not is_pdf(document):
        return [], UNSUPPORTED

    payload = stored_bytes(document)
    if payload is None:
        return [], MISSING_FILE

    try:
        found = spans_module.index_pdf(payload)
    except spans_module.SpansUnavailable:
        return [], PARSER_UNAVAILABLE

    if not found:
        # The file parsed and yielded no lines: a scan, or a PDF whose text is
        # drawn as paths. `has_text_layer` says something similar for the whole
        # corpus, but a document can have a layer pypdf reads and pdfplumber
        # cannot line up, and this is the answer for *this* index.
        return [], NO_TEXT_LAYER

    _save(document, found)
    return found, ""


def _save(document: HarvestedDocument, found: list[spans_module.Span]) -> None:
    try:
        with transaction.atomic():
            ComplianceSpan.objects.bulk_create(
                [
                    ComplianceSpan(
                        document=document,
                        span_id=span.span_id,
                        page=span.page,
                        order=span.order,
                        text=span.text,
                        x0=span.x0,
                        top=span.top,
                        x1=span.x1,
                        bottom=span.bottom,
                        page_width=span.page_width,
                        page_height=span.page_height,
                    )
                    for span in found
                ],
                # Another request got there first. Its rows are the same rows —
                # the index is a pure function of the bytes — so there is
                # nothing to reconcile and nothing to report.
                ignore_conflicts=True,
            )
    except Exception as exc:  # noqa: BLE001 - a cache write must not fail a read
        logger.warning("Could not store the span index for %s: %s", document.pk, exc)


def _from_row(row: ComplianceSpan) -> spans_module.Span:
    return spans_module.Span(
        span_id=row.span_id,
        page=row.page,
        order=row.order,
        text=row.text,
        x0=row.x0,
        top=row.top,
        x1=row.x1,
        bottom=row.bottom,
        page_width=row.page_width,
        page_height=row.page_height,
    )


# ---------------------------------------------------------------------------
# The payload
# ---------------------------------------------------------------------------
def payload_for(notice: TenderNotice, rows: Sequence[TenderRequirement]) -> dict[str, Any]:
    """Everything the split view needs, in one request.

    One call rather than three (source, index, locations) because the three are
    useless apart: a viewer with an index and no text has nothing to draw on,
    one with text and no locations is a document in a scrollbox, and locations
    fetched separately can be computed against a source that has since been
    re-harvested.

    The two location shapes are both present in the payload and exactly one is
    populated. ``highlights`` maps a requirement onto span ids and is filled
    only for an indexed PDF; ``ranges`` maps it onto
    ``(block, start, end)`` triples and is filled otherwise. A client renders whichever it was given,
    and cannot be handed both for the same row.
    """
    source = choose_source(notice, rows)
    if source is None:
        return _empty("")

    document = source.document
    payload: dict[str, Any] = {
        "source": source.kind,
        "document": (
            {
                "id": document.pk,
                "kind": document.kind,
                "url": document.url,
                "origin": document.origin,
                "page_count": document.page_count,
                "is_pdf": is_pdf(document),
            }
            if document is not None
            else None
        ),
        "blocks": [],
        "ranges": {},
        "spans": [],
        "highlights": {},
        "problem": "",
    }

    if document is not None:
        found, problem = ensure_indexed(document)
        payload["problem"] = problem
        if found:
            located = spans_module.locate_all(
                ((row.pk, row.evidence_quote) for row in rows if row.evidence_quote),
                found,
            )
            # A PDF that indexes but locates nothing falls through to its own
            # text, rather than rendering a document with no highlight on it.
            # The two readings disagree more often than they should — pdfplumber
            # and pypdf split hyphenation and table cells differently — and the
            # source was chosen on the *text*'s score, so the text is what was
            # promised. Rendering the page would look richer and answer less.
            if located or not rows:
                payload["spans"] = [span.as_dict() for span in found]
                # Keyed by requirement id as strings: JSON object keys always
                # are, and pretending otherwise makes a client that indexes with
                # a number fail on a payload that looked fine in the browser.
                payload["highlights"] = {str(key): value for key, value in located.items()}
                return payload

    # No pages to point at — the notice body, or a document that could not be
    # indexed. The text itself is the view, and the highlight is a character
    # range computed here so no client has to reimplement `canonical` to find
    # the quote in it.
    #
    # Blocks rather than one string, so the pane can reproduce the source's own
    # paragraph breaks and read like the tender page rather than like a dump.
    payload["blocks"] = [{"tag": tag, "text": text} for tag, text in source.blocks]
    payload["ranges"] = {
        str(row.pk): [list(hit) for hit in found]
        for row in rows
        if row.evidence_quote and (found := source.find(row.evidence_quote))
    }
    return payload


def _empty(problem: str) -> dict[str, Any]:
    return {
        "source": "",
        "document": None,
        "blocks": [],
        "ranges": {},
        "spans": [],
        "highlights": {},
        "problem": problem,
    }
