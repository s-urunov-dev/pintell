"""Cutting a source into passages that can still be pointed at.

Retrieval needs passages; the viewer needs coordinates. Producing them in two
places would be the obvious design and it is the one failure this app cannot
afford: a yellow box drawn from *this* module's parse, over a page rendered
from *another* module's parse, is a claim about the borrower's document that
nothing verified. So there is one parse of a PDF in this codebase and it is
``apps.compliance.spans.index_pdf``, which the compliance viewer already
renders against — this service groups the lines that parse produced and adds
nothing of its own.

That makes the dependency direction worth stating, because it looks backwards.
``rag_indexer`` imports from ``compliance``; ``compliance`` imports nothing
from here and never will. What is imported is three pure functions over text
and bytes (``spans.index_pdf``, ``text.canonical``/``sentences``,
``viewer.stored_bytes``) — no model of theirs is written, no verdict is
touched, no extraction run is read. A vendor's eligibility is decided by
exactly the code that decided it before this app existed.

**Chunks never straddle a page or a sentence.** A block stops at the foot of a
page because a rectangle has one page number, and it stops on a sentence
boundary because a passage cut mid-clause retrieves a threshold without the
condition attached to it — the same reason ``compliance.text.sentences`` exists
for quoting. Both bounds cost some chunk-size uniformity and buy a position
that is always a single contiguous range in one coordinate system.

**No overlap between chunks, by default.** The usual argument for overlapping
windows is that a fixed-width cut lands mid-idea; here the cut is already on a
sentence boundary, so the idea survives. What overlap would add is two points
whose ranges cover the same sentence, which means one search returning the same
passage twice and two highlights fighting over one line. ``CHUNK_OVERLAP`` is
in settings for anyone who measures otherwise on the gold set.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, replace
from typing import Iterable, Sequence

from django.conf import settings

from apps.compliance import spans as spans_module
from apps.compliance import text as text_module
from apps.compliance import viewer as viewer_module
from apps.tenders.models import HarvestedDocument, TenderNotice

from ..chunks import PDF, TEXT, Chunk, SourceRef
from . import structured

logger = logging.getLogger(__name__)

#: Suffixes whose structure this app can read (D61). A `.pdf` is absent on
#: purpose: its headings live in font sizes rather than in markup, and guessing
#: them from geometry is a different piece of work with a different failure
#: mode. PDFs keep the page-block path, which already carries a rectangle.
STRUCTURED_SUFFIXES = {".md", ".markdown", ".htm", ".html", ".docx"}

#: Content types a document may arrive under, mapped to what it actually is.
#: Read *beside* the stored file's suffix rather than instead of it: the
#: harvester names the file from the bytes it sniffed (`_suffix_for`), which is
#: the more trustworthy of the two, and a vendor's browser will happily upload
#: a Terms of Reference as `application/octet-stream`.
CONTENT_TYPE_SUFFIXES = {
    "text/markdown": ".md",
    "text/x-markdown": ".md",
    "text/html": ".html",
    "application/xhtml+xml": ".html",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document": ".docx",
}


def _suffix_of(document: HarvestedDocument) -> str:
    """What kind of file this is, from the stored name or the declared type."""
    stored = (document.stored_path or "").lower()
    for suffix in STRUCTURED_SUFFIXES:
        if stored.endswith(suffix):
            return suffix
    content_type = (document.content_type or "").split(";")[0].strip().lower()
    return CONTENT_TYPE_SUFFIXES.get(content_type, "")


@dataclass(frozen=True)
class Extraction:
    """What one source yielded: its chunks and the exact text they came from.

    ``canonical_text`` travels with the chunks because the caller hashes it to
    decide whether this source is already current (``IndexedSource.content_
    hash``). Hashing the raw column instead would look equivalent and is not:
    two raw bodies that differ only in an ``&nbsp;`` canonicalise to the same
    string and must not be re-embedded, while the same canonical string read
    through a newer parser is genuinely new work.
    """

    source: SourceRef
    chunks: list[Chunk]
    canonical_text: str

    @property
    def char_count(self) -> int:
        return len(self.canonical_text)


class ExtractionService:
    """Turn a notice row or a mirrored document into positioned passages.

    Stateless apart from its settings, so one instance is reused across a whole
    archive run — building it per source would re-read the settings dict tens
    of thousands of times for no gain.
    """

    def __init__(
        self,
        *,
        chunk_chars: int | None = None,
        min_chunk_chars: int | None = None,
        overlap: int | None = None,
    ):
        config = settings.RAG
        #: Target size of a passage. Not a maximum: a single sentence longer
        #: than this is emitted whole rather than cut, because a sentence is
        #: the smallest unit that still carries its own condition.
        self.chunk_chars = chunk_chars or config["CHUNK_CHARS"]
        #: Below this a passage is a heading, a page number or a table rule.
        #: Embedding it costs a call and returns noise at the top of results.
        self.min_chunk_chars = min_chunk_chars or config["MIN_CHUNK_CHARS"]
        #: Sentences (or lines) repeated from the previous chunk. See module.
        self.overlap = config["CHUNK_OVERLAP"] if overlap is None else overlap

    # -- entry points -------------------------------------------------------
    @staticmethod
    def _is_award(notice: TenderNotice) -> bool:
        """Whether this notice is a finished contract with a named winner.

        Read through the reverse relation rather than from ``notice_type``,
        because the question the payload has to answer is not "was this
        published as an award" but "is there a contract with a winner behind
        it" — and `services/awards.py` is what decides that, on two upstream
        templates and a placeholder that is not a company (D42a).
        """
        award = getattr(notice, "award", None)
        return bool(award and (award.supplier_name or "").strip())

    def from_notice(self, notice: TenderNotice) -> Extraction:
        """The notice body, as sentence chunks with character offsets.

        This is the common case and not a fallback. Most notices in the mirror
        state their requirements in the body and link no readable document at
        all, so the text path carries the majority of the archive.
        """
        source = SourceRef(
            source_key=f"notice:{notice.pk}",
            source_type=TEXT,
            notice_id=notice.notice_id,
            category=notice.category or "",
            subcategory=notice.subcategory or "",
            title=(notice.bid_description or notice.project_name or "").strip()[:300],
            is_award=self._is_award(notice),
        )
        canonical = text_module.canonical(notice.notice_text_sanitized)
        return Extraction(source, list(self.text_chunks(canonical)), canonical)

    def from_document(
        self, document: HarvestedDocument, notice: TenderNotice
    ) -> Extraction:
        """A mirrored document, as page blocks when it is a PDF we can index.

        ``notice`` supplies the filter keys, because a document has no
        direction of its own — it is the tender that is a consulting tender,
        and the TOR inherits that from whichever notice linked it. Where
        several notices share one document the caller picks one; the difference
        only shows in a filtered search, and the alternative (a point per
        notice per chunk) multiplies the collection by the fan-out.

        A PDF that will not index falls through to its extracted text rather
        than being dropped. The passage is still findable and still readable;
        it simply carries no rectangle, and the viewer already renders that
        case — the same degradation the compliance viewer performs.
        """
        source_type = PDF if viewer_module.is_pdf(document) else TEXT
        source = SourceRef(
            source_key=f"document:{document.pk}",
            source_type=source_type,
            notice_id=notice.notice_id,
            category=notice.category or "",
            subcategory=notice.subcategory or "",
            document_id=document.pk,
            title=(document.link_context or document.url or "").strip()[:300],
            is_award=self._is_award(notice),
        )

        if source_type == PDF:
            chunks, canonical = self._pdf_chunks(document)
            if chunks:
                return Extraction(source, chunks, canonical)
            logger.info(
                "No page geometry for %s — indexing its text without positions.",
                document.pk,
            )
            source = replace(source, source_type=TEXT)

        structural = self._structural_chunks(document)
        if structural is not None:
            chunks, canonical = structural
            if chunks:
                return Extraction(source, chunks, canonical)

        canonical = text_module.canonical(document.text)
        return Extraction(source, list(self.text_chunks(canonical)), canonical)

    # -- structure ----------------------------------------------------------
    def _structural_chunks(
        self, document: HarvestedDocument
    ) -> tuple[list[Chunk], str] | None:
        """Headings, paragraphs and whole tables — for uploads, and only those.

        ``None`` when this path does not apply, which the caller reads as "use
        the sentence chunker". Three conditions, and each is a decision:

        * **``origin`` must be a vendor's upload.** Re-chunking the mirror
          would move every offset in it and mean re-embedding 74,000 chunks to
          gain section paths that notice bodies do not have (D61).
        * **The format must be one whose structure is written down.** A
          heading in Markdown, Word or HTML is a heading; in a PDF it is a
          larger font, and reading it as one is a guess.
        * **The setting must be on.** ``RAG_STRUCTURAL_CHUNKING`` exists so a
          deployment that finds this worse on its own documents can go back to
          the sentence chunker without a release.

        Never raises. An unreadable upload yields no blocks and falls through
        to the chunker that reads whatever text the harvester managed to pull
        out of it — which is exactly what happened before this path existed.
        """
        if not settings.RAG["STRUCTURAL_CHUNKING"]:
            return None
        if document.origin != HarvestedDocument.Origin.CLIENT_SUPPLIED:
            return None

        suffix = _suffix_of(document)
        if suffix not in STRUCTURED_SUFFIXES:
            return None

        try:
            blocks = self._blocks_of(document, suffix)
        except Exception as exc:  # noqa: BLE001 - a bad upload is a state
            logger.info("Could not read the structure of %s: %s", document.pk, exc)
            return None
        if not blocks:
            return None

        rendered = structured.render(blocks)
        # The canonical form of *this* rendering, not of `document.text`. The
        # two differ — one has pipe tables and breadcrumbs, the other is what
        # the harvester's parser produced — and the offsets below index the one
        # the viewer will be served. Measuring against the other is the
        # mismatch `chunks.py` exists to prevent.
        canonical = text_module.canonical(rendered)
        return list(self._structured_chunks(blocks, canonical)), canonical

    def _blocks_of(
        self, document: HarvestedDocument, suffix: str
    ) -> list[structured.Block]:
        """The document's blocks, read from the bytes rather than the text.

        The stored file, because the structure is in it and not in the
        extracted text: the harvester's parsers exist to produce something
        searchable and flatten a table on the way. When the bytes are gone —
        a row kept after its file was pruned — Markdown still parses from the
        extracted text, because for Markdown the two are the same string.
        """
        payload = viewer_module.stored_bytes(document)

        if suffix == ".docx":
            return structured.blocks_from_docx(payload) if payload else []
        if suffix in {".htm", ".html"}:
            markup = payload.decode("utf-8", "replace") if payload else document.text
            return structured.blocks_from_html(markup or "")
        markdown = payload.decode("utf-8", "replace") if payload else document.text
        return structured.blocks_from_markdown(markdown or "")

    def _structured_chunks(
        self, blocks: list[structured.Block], canonical: str
    ) -> Iterable[Chunk]:
        """Grouped blocks as positioned chunks.

        Offsets are located by scanning the canonical string forward for each
        chunk's *body* — the breadcrumb the chunk carries into the embedding is
        prepended text that exists nowhere in the document, so it cannot be
        part of the range a highlight is drawn over. A body that cannot be
        located (canonicalisation folded a character inside it) yields a chunk
        with no offsets rather than wrong ones: the passage is still findable
        and still readable, and the viewer already renders that case.
        """
        cursor = 0
        for index, batch in enumerate(
            structured.group(
                blocks,
                chunk_chars=self.chunk_chars,
                min_chunk_chars=self.min_chunk_chars,
            )
        ):
            content = structured.chunk_text(batch)
            if len(content) < self.min_chunk_chars:
                continue

            body = text_module.canonical(
                "\n\n".join(block.text for block in batch if block.text.strip())
            )
            start = canonical.find(body, cursor) if body else -1
            end = start + len(body) if start >= 0 else -1
            if start >= 0:
                cursor = end

            yield Chunk(
                # Section-numbered rather than sentence-numbered, because that
                # is what this path actually cut on — and someone debugging a
                # misplaced highlight can count sections and find it by eye.
                position_id=f"b{index}",
                content=content,
                source_type=TEXT,
                char_start=max(start, 0),
                char_end=max(end, 0),
                sentence_index=index,
            )

    # -- text -------------------------------------------------------------
    def text_chunks(self, canonical: str) -> Iterable[Chunk]:
        """Sentence groups with ``char_start``/``char_end`` into ``canonical``.

        The offsets are into the **canonical** string, not the raw column, and
        the viewer must render the same canonical string for them to mean
        anything. That is why ``Extraction`` carries the text it measured:
        recomputing it downstream from the raw body would work until somebody
        changed ``compliance.text.canonical``, at which point every offset in
        the archive would be a few characters out and nothing would say so.
        """
        sentences = text_module.sentences(canonical)
        if not sentences:
            return

        # Offsets are read off the source by scanning forward rather than by
        # summing lengths: `sentences` splits on a whitespace run whose width
        # it does not report, so summing drifts by one character per sentence
        # and the highlight ends up a paragraph late by the foot of the page.
        spans: list[tuple[int, int, int, str]] = []
        cursor = 0
        for index, sentence in enumerate(sentences):
            start = canonical.find(sentence, cursor)
            if start < 0:  # pragma: no cover - only if `sentences` rewrote text
                continue
            end = start + len(sentence)
            cursor = end
            spans.append((index, start, end, sentence))

        for group in self._group(spans, key=lambda item: len(item[3])):
            first_index, start, _, _ = group[0]
            _, _, end, _ = group[-1]
            content = canonical[start:end].strip()
            if len(content) < self.min_chunk_chars:
                continue
            yield Chunk(
                # Human-legible, and the same register as the compliance
                # viewer's "p3_l12": someone debugging a misplaced highlight
                # can count sentences and find it by eye.
                position_id=f"s{first_index}",
                content=content,
                source_type=TEXT,
                char_start=start,
                char_end=end,
                sentence_index=first_index,
            )

    # -- pdf ----------------------------------------------------------------
    def _pdf_chunks(self, document: HarvestedDocument) -> tuple[list[Chunk], str]:
        """Line blocks with a union rectangle, per page.

        Returns ``([], "")`` for anything that will not index — a scan, an
        encrypted file, a build without pdfplumber, a document whose bytes are
        no longer on disk. Every one of those is a state the caller records,
        never an exception that reaches a request: the same contract the
        harvester holds for a dead link.
        """
        payload = viewer_module.stored_bytes(document)
        if not payload:
            return [], ""

        try:
            lines = spans_module.index_pdf(payload)
        except spans_module.SpansUnavailable as exc:
            logger.info("pdfplumber is unavailable in this build: %s", exc)
            return [], ""

        chunks: list[Chunk] = []
        for page_lines in self._by_page(lines):
            for group in self._group(page_lines, key=lambda span: len(span.text)):
                content = " ".join(span.text for span in group).strip()
                if len(content) < self.min_chunk_chars:
                    continue
                head = group[0]
                chunks.append(
                    Chunk(
                        position_id=f"p{head.page}_b{len(chunks)}",
                        content=content,
                        source_type=PDF,
                        page=head.page,
                        # The union of the lines' rectangles, which for a
                        # left-aligned paragraph is the paragraph's own box.
                        # A per-line list would draw tighter boxes and would
                        # make the payload proportional to the line count for
                        # a highlight nobody reads line by line.
                        bbox=(
                            min(span.x0 for span in group),
                            min(span.top for span in group),
                            max(span.x1 for span in group),
                            max(span.bottom for span in group),
                        ),
                        page_width=head.page_width,
                        page_height=head.page_height,
                    )
                )

        # The document's canonical text, assembled from the same lines the
        # chunks were cut from rather than from `document.text`. Those two come
        # from different parsers (pdfplumber here, pypdf in the harvester) and
        # hashing the one we did not read would let a re-parse go unnoticed.
        canonical = text_module.canonical(" ".join(span.text for span in lines))
        return chunks, canonical

    @staticmethod
    def _by_page(lines: Sequence[spans_module.Span]) -> Iterable[list[spans_module.Span]]:
        """Lines grouped into pages, in order. A block never spans two pages."""
        page: list[spans_module.Span] = []
        current: int | None = None
        for span in lines:
            if current is not None and span.page != current:
                yield page
                page = []
            current = span.page
            page.append(span)
        if page:
            yield page

    def _group(self, items: Sequence, key) -> Iterable[list]:
        """Consecutive items packed up to ``chunk_chars``, never splitting one.

        Shared by both paths because the packing rule is the same whether the
        unit is a sentence or a line — only the measure differs, which is what
        ``key`` supplies. A unit longer than the target becomes its own chunk;
        truncating it would produce a passage whose ``content`` disagrees with
        the range its payload points at.
        """
        batch: list = []
        size = 0
        for item in items:
            length = key(item)
            if batch and size + length > self.chunk_chars:
                yield batch
                batch = batch[-self.overlap :] if self.overlap else []
                size = sum(key(entry) for entry in batch)
            batch.append(item)
            size += length
        if batch:
            yield batch
