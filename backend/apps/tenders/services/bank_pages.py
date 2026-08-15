"""The World Bank's own staff page for a team lead.

The Bank runs an author directory for its blog, and for staff who write there
it publishes exactly the things the third contact tier is missing: their
official job title, a full professional biography, and an official portrait —
on the Bank's own domain, under the Bank's own byline.

That makes it a categorically better source than a web search, and it needs
neither. The URL is deterministic:

    https://blogs.worldbank.org/en/team/{first initial}/{first-last}

so this is one HTTP GET per person with no model in the loop, no API key, no
metering and no quota. A 404 simply means that person does not blog — most do
not — and is not an error.

**Why this source and not the ones a search also returns.** What is collected
here is published *by the employer, about the employee, in their professional
capacity*: the Bank chose to put this title, this biography and this portrait
online under its own name. Personal accounts on social platforms are a
different thing entirely — not employer-published, not professional, and
gathering them per-person is dossier-building regardless of how public each
piece is. Those platforms' terms also prohibit exactly this. So this module
reads one first-party page and stops there.

Only the portrait's URL is stored, never a copy: the image stays hosted by the
Bank, so it remains theirs to change or withdraw.
"""

from __future__ import annotations

import html
import logging
import re
from dataclasses import dataclass

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

logger = logging.getLogger(__name__)

AUTHOR_BASE = "https://blogs.worldbank.org/en/team"
SOURCE_BANK_PAGE = "worldbank_author_page"

_TIMEOUT = 15

#: The portrait sits on the Bank's image CDN and is the only `<img>` on the
#: page that does. Everything else is chrome: logos, search icons, footers.
_PORTRAIT_RE = re.compile(
    r'<img[^>]+src="(https://s7d1\.scene7\.com/is/image/wbcollab/picture-[^"]+)"',
    re.IGNORECASE,
)

#: "Mohini Kak | Senior Health Specialist" — the title the Bank gives them.
_TITLE_RE = re.compile(r"<title>\s*([^<]*?)\s*</title>", re.IGNORECASE | re.DOTALL)

_TAG_RE = re.compile(r"<[^>]+>")
_SCRIPT_RE = re.compile(r"<(script|style)[^>]*>.*?</\1>", re.IGNORECASE | re.DOTALL)

#: Navigation text that precedes the biography on every author page. The bio
#: starts after the language switcher, which is the last piece of furniture.
_BIO_START = "This page in:"

#: Where the biography stops and the page furniture starts: first the author's
#: post list, then the site-wide footer. Observed on live pages — without the
#: footer markers a bio ends "…University of Mumbai, India. More Posts By
#: Mohini Legal Privacy Notice Access to Information Jobs Contact…".
_BIO_END = (
    "More Posts By", "Blogs by", "Recent Blogs", "Latest Blogs",
    "Legal Privacy Notice", "Privacy Notice", "Access to Information",
    "All Rights Reserved", "Skip to",
)

#: A biography is prose; anything shorter is a stray caption.
_MIN_BIO = 80


@dataclass(slots=True)
class AuthorPage:
    url: str = ""
    title: str = ""
    bio: str = ""
    photo_url: str = ""
    #: False when the person has no author page — a normal outcome, not a
    #: failure, and worth recording so it is not retried on every run.
    found: bool = False


def author_url(name: str) -> str:
    """The Bank's author URL for a name, or "" when one cannot be formed.

    The path uses the first name's initial, not the surname's — Koji Nishida
    lives under ``/k/``, and ``/n/`` is a 404.
    """
    parts = [p for p in re.split(r"[\s,]+", (name or "").strip()) if p.strip(".")]
    words = [re.sub(r"[^a-z]", "", p.lower()) for p in parts]
    words = [w for w in words if len(w) > 1]
    if len(words) < 2:
        return ""
    return f"{AUTHOR_BASE}/{words[0][0]}/{words[0]}-{words[-1]}"


def _session() -> requests.Session:
    session = requests.Session()
    retry = Retry(
        total=2, backoff_factor=0.4,
        status_forcelist=(429, 500, 502, 503, 504),
        allowed_methods=frozenset({"GET"}),
    )
    session.mount("https://", HTTPAdapter(max_retries=retry))
    session.headers["User-Agent"] = "Pintell/1.0 (+staff page lookup)"
    return session


def _text_of(markup: str) -> str:
    stripped = _SCRIPT_RE.sub(" ", markup)
    return re.sub(r"\s+", " ", html.unescape(_TAG_RE.sub(" ", stripped))).strip()


def _extract_bio(markup: str, name: str) -> str:
    """The biography paragraph, cut out of the page's flattened text.

    The page is a single template with the bio in the middle, so it is found by
    what surrounds it rather than by a class name — markup changes, the
    language switcher above the bio does not.
    """
    text = _text_of(markup)
    start = text.find(_BIO_START)
    if start == -1:
        return ""

    after = text[start + len(_BIO_START):]
    # The switcher lists the available languages before the prose begins; the
    # bio itself opens with the person's name.
    anchor = after.find(name)
    if anchor == -1:
        return ""

    bio = after[anchor:].strip()
    # Everything after the biography is page furniture — the post list, then
    # the global footer. Cut at the earliest marker rather than each in turn,
    # so a later one cannot re-extend past an earlier cut.
    cuts = [bio.find(marker) for marker in _BIO_END]
    earliest = min((c for c in cuts if c > _MIN_BIO), default=-1)
    if earliest > 0:
        bio = bio[:earliest]
    bio = bio.strip()
    return bio if len(bio) >= _MIN_BIO else ""


def fetch_author_page(name: str) -> AuthorPage:
    """Look up one person's World Bank staff page.

    Never raises: a missing page, a redirect, a timeout and a transport error
    all return ``found=False``, because this is an enrichment and the caller
    has a profile to save either way.
    """
    url = author_url(name)
    if not url:
        return AuthorPage()

    try:
        response = _session().get(url, timeout=_TIMEOUT, allow_redirects=True)
    except requests.RequestException as exc:
        logger.info("Author page lookup failed for %r: %s", name, exc)
        return AuthorPage(url=url)

    if response.status_code != 200:
        # Most staff do not blog. Not an error worth logging loudly.
        return AuthorPage(url=url)

    markup = response.text
    title = ""
    match = _TITLE_RE.search(markup)
    if match:
        raw = html.unescape(match.group(1))
        # "Mohini Kak | Senior Health Specialist" → the half after the bar.
        if "|" in raw:
            title = raw.split("|", 1)[1].strip()

    portrait = _PORTRAIT_RE.search(markup)

    return AuthorPage(
        url=url,
        title=title,
        bio=_extract_bio(markup, name),
        photo_url=portrait.group(1) if portrait else "",
        found=True,
    )
