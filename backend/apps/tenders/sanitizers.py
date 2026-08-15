"""HTML sanitisation for upstream ``notice_text``.

The World Bank API returns the notice body as raw HTML authored by third
parties. It is sanitised **on write** (before it ever reaches the database)
with a strict allow-list, so nothing downstream has to trust it:

* only the tags in :data:`ALLOWED_TAGS` survive;
* every attribute not in :data:`ALLOWED_ATTRIBUTES` is dropped — which covers
  the whole ``on*`` event-handler family, ``style``, ``srcdoc``, …;
* ``<script>``/``<style>``/``<iframe>`` contents are removed entirely rather
  than being unwrapped into visible text;
* link targets are restricted to http/https/mailto, so ``javascript:`` and
  ``data:`` URLs cannot survive.

The frontend adds a second, independent layer: it never uses
``dangerouslySetInnerHTML`` and instead re-parses this HTML into React
elements through its own allow-list.
"""

from __future__ import annotations

import re

import bleach

ALLOWED_TAGS: frozenset[str] = frozenset(
    {
        "p", "br", "b", "strong", "i", "em", "u",
        "ul", "ol", "li",
        "div", "span",
        "h1", "h2", "h3", "h4",
        "a",
        "table", "thead", "tbody", "tr", "th", "td",
        "blockquote", "hr",
    }
)

ALLOWED_ATTRIBUTES: dict[str, list[str]] = {
    "a": ["href", "title"],
}

ALLOWED_PROTOCOLS: frozenset[str] = frozenset({"http", "https", "mailto"})

# Tags whose *contents* must disappear with them, not be unwrapped as text.
STRIP_WITH_CONTENT: tuple[str, ...] = (
    "script", "style", "iframe", "object", "embed", "template", "noscript",
)

_CLEANER = bleach.Cleaner(
    tags=set(ALLOWED_TAGS),
    attributes=ALLOWED_ATTRIBUTES,
    protocols=set(ALLOWED_PROTOCOLS),
    strip=True,                       # drop disallowed tags, keep inner text
    strip_comments=True,
)

_WHITESPACE_RE = re.compile(r"[ \t\r\f\v]+")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
_EMPTY_PARA_RE = re.compile(r"<p>\s*(?:&nbsp;|\s)*</p>", re.IGNORECASE)
# Upstream notices are full of &nbsp; runs used as manual spacing.
_NBSP_RUN_RE = re.compile(r"(?:&nbsp;\s*){2,}", re.IGNORECASE)


def sanitize_html(raw: str | None) -> str:
    """Return a safe HTML fragment for ``raw`` (``""`` when there is nothing)."""
    if not raw:
        return ""

    # Step 1: drop script-like elements *together with their content*. Left to
    # bleach's ``strip=True`` alone, "<script>alert(1)</script>" would survive
    # as the visible text "alert(1)".
    cleaned = raw
    for tag in STRIP_WITH_CONTENT:
        cleaned = re.sub(
            rf"<{tag}\b[^>]*>.*?</{tag}\s*>", "", cleaned,
            flags=re.IGNORECASE | re.DOTALL,
        )
        # Unclosed or self-closing variants of the same tags.
        cleaned = re.sub(rf"</?{tag}\b[^>]*/?>", "", cleaned, flags=re.IGNORECASE)

    # Step 2: the allow-list itself. Everything not explicitly permitted is
    # removed — including every attribute outside ALLOWED_ATTRIBUTES, which is
    # what disposes of the whole ``on*`` handler family.
    cleaned = _CLEANER.clean(cleaned)

    cleaned = _EMPTY_PARA_RE.sub("", cleaned)
    cleaned = _NBSP_RUN_RE.sub(" ", cleaned)
    cleaned = _WHITESPACE_RE.sub(" ", cleaned)
    cleaned = _BLANK_LINES_RE.sub("\n\n", cleaned)
    return cleaned.strip()


def html_to_text(raw: str | None, max_length: int | None = None) -> str:
    """Flatten HTML to plain text (used for previews and search snippets)."""
    if not raw:
        return ""
    stripped = bleach.clean(raw, tags=set(), attributes={}, strip=True)
    text = re.sub(r"\s+", " ", stripped).strip()
    if max_length and len(text) > max_length:
        text = text[: max_length - 1].rstrip() + "…"
    return text
