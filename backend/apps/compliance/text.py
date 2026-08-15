"""One canonical form for source text, shared by extraction and verification.

This module exists because of a specific failure it prevents. Every extracted
requirement carries a quote copied from the source, and the grounding verifier
later searches the source for that quote; a quote it cannot find marks the
requirement as ungrounded. If extraction and verification normalise the text
differently — or if one of them does not normalise at all — then correct
extractions are reported as hallucinations and the headline accuracy number is
wrong in the direction that flatters nothing and helps no one.

So there is exactly one function, both sides call it, and quotes are always
verbatim **with respect to the canonical form** rather than to the raw bytes.

What the raw text actually looks like matters here. `TenderNotice.notice_text_
sanitized` is HTML that has had its tags stripped by `bleach`, which leaves
character entities intact: a real notice body contains runs of `&nbsp;` between
clause numbers and `&rsquo;` wherever an apostrophe belongs. A quote copied from
that text and matched against a PDF of the same document — or against the same
text after any other tool has unescaped it — will not be found.
"""

from __future__ import annotations

import html
import re
import unicodedata

import bleach

#: Typographic characters that mean the same thing as an ASCII one. Procurement
#: documents mix them freely — the same notice will use both ' and ’ — and a
#: quote that differs from its source by one apostrophe is still the same quote.
_LOOKALIKES = {
    "‘": "'", "’": "'", "‚": "'", "‛": "'",
    "“": '"', "”": '"', "„": '"',
    "‐": "-", "‑": "-", "‒": "-", "–": "-",
    "—": "-", "―": "-", "−": "-",
    " ": " ", " ": " ", " ": " ", " ": " ",
    "…": "...",
}

_LOOKALIKE_RE = re.compile("|".join(map(re.escape, _LOOKALIKES)))
_WHITESPACE_RE = re.compile(r"\s+")

#: Footnote markers the World Bank's own templates leave in the notice body —
#: "joint venture member[1]" is a real string from a real notice. Removed
#: because they land in the middle of the exact phrases the rules match on.
_FOOTNOTE_RE = re.compile(r"\[\d{1,2}\]")


#: Tags that end a sentence even though no full stop precedes them. A notice
#: body is a run of `<p>` and `<li>` elements whose text frequently omits the
#: terminating period; without this the whole notice collapses into one
#: "sentence" and every quote is the entire document.
_BLOCK_TAG_RE = re.compile(r"</(?:p|div|li|tr|h[1-6]|br)\s*>|<br\s*/?>", re.IGNORECASE)

#: The same pattern, exported.
#:
#: ``viewer`` splits a notice body into the paragraphs the pane renders, and it
#: has to cut in exactly the places ``canonical`` turns into ". " — otherwise a
#: quote that runs across a paragraph break carries a full stop the pane's own
#: index does not have, and the highlight silently fails on precisely the
#: longest quotes. Sharing the pattern is what stops the two drifting; a second
#: copy would be right on the day it was written and wrong by the next fix.
BLOCK_BOUNDARY_RE = _BLOCK_TAG_RE


def canonical(raw: str | None) -> str:
    """Normalise source text into the one form quotes are verbatim against.

    Order matters, and each step is here because leaving it out produced a real
    failure against real notices:

    1. **Block tags become full stops** before anything strips them. The source
       is HTML whose paragraphs often carry no sentence-ending punctuation, so
       stripping first merges unrelated clauses into a single sentence and a
       rule then reads a threshold from one paragraph and quotes another.
    2. **Tags are stripped**, because the raw column is HTML and a quote
       containing ``<strong>`` will never be found in a PDF of the same text.
    3. **Entities are unescaped after that**, because `&rsquo;` has to become a
       character before that character can be folded to an ASCII apostrophe.
       Entity-encoded markup therefore survives as literal text (``<script>``
       stays a string); that is safe here because this output is plain text
       stored in a JSON field and rendered by React, which escapes it — but it
       is the reason this function must never be used to build HTML.
    """
    if not raw:
        return ""
    text = _BLOCK_TAG_RE.sub(". ", raw)
    text = bleach.clean(text, tags=set(), attributes={}, strip=True)
    text = html.unescape(text)
    # A second pass: notice bodies that have been through two HTML-producing
    # systems carry doubly-escaped entities (`&amp;nbsp;`), and one unescape
    # leaves `&nbsp;` sitting in the output.
    if "&" in text:
        text = html.unescape(text)
    text = unicodedata.normalize("NFKC", text)
    text = _LOOKALIKE_RE.sub(lambda m: _LOOKALIKES[m.group()], text)
    text = _FOOTNOTE_RE.sub("", text)
    text = _WHITESPACE_RE.sub(" ", text)
    # Turning block tags into full stops leaves runs of them where a paragraph
    # did end in punctuation. Collapsed here rather than earlier so the
    # sentence splitter sees one boundary, not four.
    text = re.sub(r"(?:\.\s*){2,}", ". ", text)
    return text.strip()


#: Sentence boundary: a full stop, question or exclamation mark followed by
#: space and a capital. Deliberately conservative — it under-splits rather than
#: over-splits, because a quote that carries a neighbouring clause is still
#: findable in the source, while a quote cut in half may not be.
_SENTENCE_RE = re.compile(r"(?<=[.!?])\s+(?=[A-Z(])")

#: Abbreviations whose full stop is not a sentence end. Short list on purpose:
#: these are the ones that actually occur in procurement notices and each one
#: was costing a truncated quote.
_ABBREVIATIONS = ("No.", "Nos.", "Ltd.", "Inc.", "Co.", "St.", "approx.", "i.e.", "e.g.", "U.S.")


def sentences(text: str) -> list[str]:
    """Split canonical text into sentences, for quoting.

    A requirement's evidence is the sentence it was read from. Anything smaller
    tends to quote a number without the condition attached to it, which is
    exactly the kind of quote that looks grounded while proving nothing.
    """
    if not text:
        return []
    parts = _SENTENCE_RE.split(text)

    merged: list[str] = []
    for part in parts:
        if merged and merged[-1].endswith(_ABBREVIATIONS):
            merged[-1] = f"{merged[-1]} {part}"
        else:
            merged.append(part)
    return [p.strip() for p in merged if p.strip()]


def contains_quote(source: str, quote: str) -> bool:
    """Whether ``quote`` appears in ``source``, both in canonical form.

    The grounding check in its simplest form. Fuzzy alignment for quotes that
    span a page break or a table cell is a separate problem and is not solved
    here; this answers the exact-match case, which is the one L1 produces
    because L1 copies its quotes out of the very string it is matching against.
    """
    if not quote:
        return False
    return canonical(quote).casefold() in canonical(source).casefold()
