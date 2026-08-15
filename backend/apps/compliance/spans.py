"""Where in the page a quote actually sits.

Every requirement already carries the sentence it was read from, and the
grounding verifier already proves that sentence is in the document. What
neither of them can say is *where* — and a vendor asked to accept that a tender
demands USD 22.4 million of turnover is entitled to see the line, on the page,
in the borrower's own file. That is what this module makes possible: the
document is indexed line by line with each line's rectangle, and a stored quote
is located against that index.

Two decisions shape everything here.

**The model is not asked for positions.** The obvious design hands the model a
line-numbered document and asks it to answer with line ids. It costs nothing
extra and it is wrong twice over: it replaces the verbatim quote — the thing the
whole grounding measurement is built on (D4) — with a reference the verifier
cannot check, and it makes the highlight a second claim the model can get wrong.
Here the model keeps doing exactly what it did, and ``locate`` finds the quote in
the index by string matching. A highlight is therefore never a new claim: it is
the grounded quote, pointed at. When the match fails there is simply no
highlight, which is the correct degradation — the quote is still shown, still
verified, just not located.

**Spans are stored and locations are not.** Indexing a PDF costs a parse of the
whole file and is worth persisting. Mapping a quote onto spans is a substring
search over a few hundred kilobytes, is derived entirely from data we hold, and
would go stale the moment either side changed — so it is computed per request
and never written down. A cached location that disagreed with its own quote is
the one failure this feature must not be able to have.

Only PDFs are indexed. A DOCX or an HTML page has no page geometry to point at,
and inventing one would put a rectangle on a coordinate system that does not
exist. Those documents fall back to the quote alone, which is what the product
showed before this module and is not made worse by it.
"""

from __future__ import annotations

import io
import logging
from dataclasses import dataclass
from typing import Any, Iterable, Sequence

from .text import canonical

logger = logging.getLogger(__name__)

#: Pages indexed for one document.
#:
#: A bidding document in the mirror runs to several hundred pages, and the
#: viewer renders what it is given. The cap is on the *indexing*, not on the
#: file: page 300 is still served and still readable, it simply carries no
#: highlight. That is the right way round — a quote nobody can find is a
#: degraded feature, while a request that spends a minute in pdfplumber is a
#: broken one.
MAX_PAGES = 120

#: Lines indexed per document, across all pages. The second bound, because page
#: count and line count come apart badly on documents that are one long table.
MAX_LINES = 12_000

#: Shortest line worth an entry. A single character is a bullet, a page number
#: or a table rule; it can never be the distinguishing part of a quote, and
#: thousands of them make every ``locate`` slower for nothing.
MIN_LINE_CHARS = 2


@dataclass(frozen=True)
class Span:
    """One line of one page, with the rectangle it occupies.

    ``bbox`` is in PDF points with the origin at the **top left**, which is
    pdfplumber's ``top``/``bottom`` convention and also the browser's. The
    alternative — PDF's own bottom-left origin — would mean the one coordinate
    flip in the system living in a viewer written months later by someone
    reading a JSON payload with no note attached.

    ``page_width`` and ``page_height`` travel with every span rather than being
    looked up per page, because the client needs them to scale the rectangle
    onto a canvas it rendered at whatever width the window happened to be.
    """

    span_id: str
    page: int
    order: int
    text: str
    x0: float
    top: float
    x1: float
    bottom: float
    page_width: float
    page_height: float

    def as_dict(self) -> dict[str, Any]:
        return {
            "span_id": self.span_id,
            "page": self.page,
            "text": self.text,
            "bbox": {
                "x0": round(self.x0, 2),
                "top": round(self.top, 2),
                "x1": round(self.x1, 2),
                "bottom": round(self.bottom, 2),
            },
            "page_width": round(self.page_width, 2),
            "page_height": round(self.page_height, 2),
        }


class SpansUnavailable(RuntimeError):
    """pdfplumber is not installed in this build.

    Raised rather than returning an empty list, so a deployment without the
    parser is distinguishable from a document that genuinely has no text. The
    caller turns it into a recorded state; see ``views.NoticeDocumentView``.
    """


def index_pdf(payload: bytes) -> list[Span]:
    """Index a PDF into lines with rectangles. Never raises for a bad file.

    Returns an empty list for a scan, an encrypted file, or anything pdfplumber
    cannot read — the same contract the harvester holds for a dead link, and for
    the same reason: a document that will not index is a missing highlight, not
    a broken page.

    ``extract_text_lines`` rather than ``extract_words``: a requirement's quote
    is a sentence, so the smallest unit worth pointing at is a line. Words would
    give a tighter rectangle around a partial match and would multiply the index
    by eight for a highlight nobody reads word by word.
    """
    try:
        import pdfplumber  # noqa: PLC0415 - optional parser, see the docstring
    except ImportError as exc:  # pragma: no cover - depends on the build
        raise SpansUnavailable(str(exc)) from exc

    spans: list[Span] = []
    try:
        with pdfplumber.open(io.BytesIO(payload)) as pdf:
            for page_number, page in enumerate(pdf.pages[:MAX_PAGES], start=1):
                if len(spans) >= MAX_LINES:
                    break
                spans.extend(_page_spans(page, page_number, len(spans)))
    except Exception as exc:  # noqa: BLE001 - a bad file is a state, not a crash
        logger.info("Could not index a PDF for highlighting: %s", exc)
        return []
    return spans[:MAX_LINES]


def _page_spans(page: Any, page_number: int, offset: int) -> list[Span]:
    """The lines of one page. A page that will not parse contributes none.

    Wrapped separately from the document so one damaged page costs its own
    highlights rather than the whole file's — the tail of a long bidding
    document is where the malformed pages are, and the qualification table is
    usually near the front.
    """
    try:
        lines = page.extract_text_lines(layout=False, strip=True)
    except Exception as exc:  # noqa: BLE001 - see the docstring
        logger.debug("Page %s of a document would not parse: %s", page_number, exc)
        return []

    width = float(page.width or 0)
    height = float(page.height or 0)
    spans: list[Span] = []
    for index, line in enumerate(lines):
        text = (line.get("text") or "").strip()
        if len(text) < MIN_LINE_CHARS:
            continue
        spans.append(
            Span(
                # Human-legible on purpose: this id appears in an API payload
                # and in the DOM, and "p3_l12" is something a person debugging
                # a misplaced highlight can find in the document by eye.
                span_id=f"p{page_number}_l{index}",
                page=page_number,
                order=offset + len(spans),
                text=text,
                x0=float(line.get("x0", 0)),
                top=float(line.get("top", 0)),
                x1=float(line.get("x1", 0)),
                bottom=float(line.get("bottom", 0)),
                page_width=width,
                page_height=height,
            )
        )
    return spans


# ---------------------------------------------------------------------------
# Finding a quote in the index
# ---------------------------------------------------------------------------
class Locator:
    """A source's pieces as one searchable string, with an offset map.

    Built once per request and reused across every requirement, because the
    expensive half is assembling the canonical text and the cheap half is the
    search — and a tender has fifteen quotes against one document.

    The canonical form is applied **per piece and then joined with a single
    space**, never to the concatenation. That is what keeps the offset map
    valid: ``canonical`` collapses whitespace, so canonicalising the join could
    shift every offset after the first multi-space run, and the highlight would
    drift further down the page the further into the document it sat. It is the
    same reason ``l2._bounded_source`` joins already-canonical sentences.

    Built over ``(key, text)`` pairs rather than over ``Span`` objects so the
    offset arithmetic is not tied to page geometry. The notice body deliberately
    does not use this class: its pieces are cut out of a canonical string that
    already exists (``viewer._split_blocks``), so its offsets are *read* rather
    than reconstructed — the safer arrangement wherever the whole text is in
    hand. Here it is not: a PDF's lines come from a different parse than the
    text the quote was copied out of, so a join is the best available.
    """

    def __init__(self, pieces: Sequence[tuple[Any, str]]):
        self._ranges: list[tuple[int, int, Any]] = []

        parts: list[str] = []
        cursor = 0
        for key, raw in pieces:
            piece = canonical(raw)
            if not piece:
                continue
            if parts:
                cursor += 1  # the joining space
            start = cursor
            cursor += len(piece)
            parts.append(piece)
            self._ranges.append((start, cursor, key))

        # Case-folded once. Every quote is folded the same way before it is
        # searched for, matching `text.contains_quote`, because a document that
        # prints a heading in capitals and the sentence in sentence case is the
        # ordinary shape of a TOR rather than a special case.
        self._haystack = " ".join(parts).casefold()

    @classmethod
    def over_spans(cls, spans: Sequence[Span]) -> "Locator":
        return cls([(span.span_id, span.text) for span in spans])

    def find(self, quote: str) -> list[tuple[Any, int, int]]:
        """Every piece the quote covers, with its offsets *within that piece*.

        Offsets are local rather than global because the caller renders one
        piece at a time — a paragraph, a line of a page — and a global offset
        would have to be un-summed against the same join this class performed.

        An empty list is a normal answer and the caller must treat it as one. A
        quote can be genuinely present in the source the verifier checked and
        absent from this index: pdfplumber and pypdf disagree about hyphenation
        and table cells, and the quote may sit past ``MAX_PAGES``. The
        requirement is still shown with its verified quote; only the pointer is
        missing.

        No fuzzy fallback. A near-match would put a box on a line that does not
        say what the card says, which is worse than no box: the vendor would be
        reading a highlight as proof of a claim it does not support, and the
        failure would be invisible to everyone who did not check the words.
        """
        needle = canonical(quote).casefold()
        if not needle or not self._haystack:
            return []

        start = self._haystack.find(needle)
        if start < 0:
            return []
        end = start + len(needle)

        hits: list[tuple[Any, int, int]] = []
        for piece_start, piece_end, key in self._ranges:
            # Overlap, not containment: a quote almost always begins mid-piece
            # and ends mid-piece, and the pieces it straddles are exactly the
            # ones worth highlighting.
            if piece_start < end and piece_end > start:
                hits.append(
                    (
                        key,
                        max(start - piece_start, 0),
                        min(end, piece_end) - piece_start,
                    )
                )
        return hits

    def locate(self, quote: str) -> list[Any]:
        """The keys a quote covers, without the offsets. See ``find``."""
        return [key for key, _, _ in self.find(quote)]


def locate_all(quotes: Iterable[tuple[Any, str]], spans: Sequence[Span]) -> dict[Any, list[str]]:
    """Map each ``(id, quote)`` onto its span ids, in one pass over the index.

    Ids with no match are absent from the result rather than present with an
    empty list: the client renders "no highlight for this one" from the absence,
    and two spellings of the same state is how a UI starts treating one of them
    as an error.
    """
    locator = Locator.over_spans(spans)
    found: dict[Any, list[str]] = {}
    for identifier, quote in quotes:
        ids = locator.locate(quote)
        if ids:
            found[identifier] = ids
    return found
