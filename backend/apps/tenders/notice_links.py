"""Pull the documents a bidder needs out of the notice body.

The Terms of Reference is the document that actually describes the work — for a
consulting notice the body itself is a few paragraphs and the TOR carries the
scope, deliverables and qualification requirements. It is not in either
upstream API: the projects feed publishes project-level documents only, and the
procurement feed has no attachment field at all. Borrowers therefore paste the
link into the notice text, in wording that is remarkably consistent:

    The detailed Terms of Reference (TOR) for the assignment can be
    found at the following link: https://docs.google.com/…

So the link is found by reading what comes *before* each URL. That is what
distinguishes a TOR from a registration form or the borrower's home page — the
URL itself is usually an opaque Google Drive or cloud id and says nothing.

Two details matter in practice:

* **URLs arrive HTML-escaped.** `?usp=sharing&amp;ouid=…` appears in the text
  as-is; six of the twenty-nine live notices carry one. Handing that to a
  browser produces a broken link, so entities are unescaped here.
* **Some notices have no link at all.** A third say "send your request to
  <e-mail>", and a few name the TOR without offering either. Those are reported
  as what they are rather than silently dropped — "ask them for it" is still
  more useful than an empty panel.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass
from urllib.parse import urlparse

#: Bare URLs in prose. A closing round bracket is *allowed* through here and
#: judged in `_clean_url`, because it is ambiguous: it ends the sentence in
#: "(see https://x.io/a)" but belongs to the path in ".../a_(final)".
_URL_RE = re.compile(r"""https?://[^\s<>"'\]}]+""", re.IGNORECASE)

#: The domain part is deliberately greedy — subdomains are common — so the
#: sentence's full stop is caught with it and trimmed in `_clean_email`.
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}")

#: How far back to read for the phrase that describes a link. Long enough to
#: catch "The detailed Terms of Reference (TOR) for the assignment can be found
#: at the following link:", short enough not to reach the previous paragraph.
_CONTEXT_CHARS = 180

_TOR_RE = re.compile(r"terms?\s+of\s+reference|\bto\s*rs?\b|\btors?\b", re.IGNORECASE)
_BIDDING_RE = re.compile(
    r"bidding\s+document|tender\s+document|registration\s+form|"
    r"request\s+for\s+proposal|dossier\s+d\s*appel|pliego",
    re.IGNORECASE,
)

#: Trailing characters that belong to the sentence, not the URL.
_TRAILING = ".,;:!? "

# Link kinds, most useful to least.
KIND_TOR = "tor"
KIND_BIDDING = "bidding"
KIND_OTHER = "other"

_KIND_ORDER = {KIND_TOR: 0, KIND_BIDDING: 1, KIND_OTHER: 2}


@dataclass(frozen=True, slots=True)
class NoticeLink:
    url: str
    kind: str
    #: The sentence fragment that introduced the link, so the UI can show what
    #: the borrower actually called it.
    context: str


def _clean_url(raw: str) -> str:
    """Drop the sentence punctuation glued to the end of a URL."""
    url = raw.rstrip(_TRAILING)
    # A closing bracket is part of the path only when the URL opened one
    # ("…/a_(final)"); an unmatched one closes the prose ("(see …/a)."), and
    # punctuation can hide behind it, so strip in turns.
    while url.endswith(")") and url.count(")") > url.count("("):
        url = url[:-1].rstrip(_TRAILING)
    return url


def _clean_email(raw: str) -> str:
    """Trim the sentence punctuation the greedy domain match swallowed."""
    address = raw.rstrip(".-")
    local, _, domain = address.rpartition("@")
    # Still an address after trimming? "a@b." collapses to something unusable.
    return address if local and "." in domain else ""


def _is_usable(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    return parsed.scheme in {"http", "https"} and "." in parsed.netloc


def _classify(context: str) -> str:
    """What the text called this link. TOR wins when both are mentioned."""
    if _TOR_RE.search(context):
        return KIND_TOR
    if _BIDDING_RE.search(context):
        return KIND_BIDDING
    return KIND_OTHER


def _tidy(fragment: str) -> str:
    return " ".join(fragment.split())


def extract_links(text: str) -> list[NoticeLink]:
    """Every usable link in the notice body, labelled by what precedes it.

    Ordered TOR first, then bidding documents, then the rest — a bidder opens
    them in that order. Duplicates are collapsed, keeping the most specific
    kind seen for a URL.
    """
    if not text:
        return []

    # `html_to_text` leaves entities in place, so `&amp;` inside a query string
    # and `&nbsp;` around it both survive into here. Decode once: a URL handed
    # to a browser with `&amp;` in it is simply a broken link.
    text = html.unescape(text)

    best: dict[str, NoticeLink] = {}
    for match in _URL_RE.finditer(text):
        url = _clean_url(match.group(0))
        if not _is_usable(url):
            continue

        context = _tidy(text[max(0, match.start() - _CONTEXT_CHARS) : match.start()])
        link = NoticeLink(url=url, kind=_classify(context), context=context[-160:])

        previous = best.get(url)
        # The same URL can appear twice, once with a describing sentence and
        # once bare; keep whichever reading is more specific.
        if previous is None or _KIND_ORDER[link.kind] < _KIND_ORDER[previous.kind]:
            best[url] = link

    return sorted(best.values(), key=lambda link: _KIND_ORDER[link.kind])


def find_tor_email(text: str) -> str:
    """Address to request the TOR from, when no link was published.

    Only an address introduced by TOR wording counts — a notice lists several
    e-mails (the contact, the submission address), and returning the wrong one
    sends the request nowhere.
    """
    if not text:
        return ""
    text = html.unescape(text)
    for match in _EMAIL_RE.finditer(text):
        context = text[max(0, match.start() - 220) : match.start()]
        if _TOR_RE.search(context):
            address = _clean_email(match.group(0))
            if address:
                return address
    return ""


def mentions_tor(text: str) -> bool:
    """True when the notice refers to a TOR at all, linked or not."""
    return bool(text) and bool(_TOR_RE.search(text))
