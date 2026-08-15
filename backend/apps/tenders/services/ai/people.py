"""Fill in what the projects feed leaves out about a task team leader.

A notice's third contact tier is a list of names and nothing else: the projects
feed publishes ``teamleadname`` as prose ("Koji Nishida,Irina Voitekhovitch")
with no address, title or unit. This module turns a name into something a user
can act on, and draws a hard line around what "act on" means.

**What it collects**

* The Bank work address. Staff addresses follow a published pattern, so a
  candidate is derived locally from the name (:func:`derive_work_email`) and
  offered as a *candidate* — ``EmailSource.PATTERN``, never presented as
  confirmed. If a search turns up the address actually printed on a Bank page,
  that supersedes it as ``EmailSource.VERIFIED``.
* Title, unit and duty station, when published.
* Public professional links — a Bank staff or blog-author page, a paper, a
  conference bio — as URLs only.

**What it does not collect, and why**

Private contact details for a named individual are out of scope: a personal
mailbox or phone number is not published by the Bank and inferring one is a
guess about a real person. No third-party page is fetched either — a search
result URL is stored and the page behind it left alone, which keeps this the
right side of those sites' terms. The one page that *is* read is the Bank's own
author directory (:mod:`..bank_pages`), which is first-party: the employer
publishing about its employee. The tier exists so a user knows who owns a project at
the Bank and can reach them *through Bank channels*; the notices themselves
point at the borrower for everything procurement-related, and the Procurement
Regulations they cite (Section III, 3.14–3.17) are the reason that ordering is
worth preserving in the UI.

The search itself runs through :mod:`.providers`, so this works on either
Claude or Gemini — the latter's free tier bundles the Google Search grounding,
which is what makes the tier affordable to fill in at all.

Like every other AI feature here, this is best-effort: without a key it does
the pattern derivation and stops, and any failure leaves the profile untouched
rather than propagating.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from urllib.parse import urlparse

from django.conf import settings
from django.utils import timezone

from ...models import ProjectProfile, TeamLeadProfile
from ..bank_pages import fetch_author_page
from .client import AIUnavailable
from .providers import search_answer, search_enabled

logger = logging.getLogger(__name__)

SOURCE_AI_SEARCH = "ai_web_search"
SOURCE_PATTERN = "name_pattern"

STAFF_DOMAIN = "worldbank.org"

#: Hosts worth keeping as a professional reference. Everything else a search
#: returns — news, aggregators, people-search databases — is dropped: those are
#: about the person rather than published by or with them.
#: Order matters: the Bank's own repository lives on a worldbank.org host, so
#: the narrower publication test has to run before the institutional one or
#: every paper would be labelled as a staff page.
_LINK_KINDS = (
    ("publication", ("openknowledge.worldbank.org", "documents.worldbank.org",
                     "doi.org", "ssrn.com", "researchgate.net", "scholar.google.")),
    ("worldbank", ("worldbank.org", "ifc.org", "miga.org")),
    ("profile", ("linkedin.com/in/",)),
)

#: People-search and data-broker sites. Named explicitly so a plausible-looking
#: result never becomes a stored "profile" for a real person.
_REJECTED_HOSTS = (
    "rocketreach", "zoominfo", "apollo.io", "lusha", "signalhire",
    "contactout", "hunter.io", "snov.io", "clearbit", "spokeo",
    "whitepages", "peoplefinder", "beenverified", "intelius",
)

_URL_RE = re.compile(r"https?://[^\s<>\"')\]]+", re.IGNORECASE)
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]{2,}")

#: Name particles that are not part of the address pattern.
_PARTICLES = {"van", "von", "der", "den", "de", "del", "della", "da", "di",
              "du", "la", "le", "el", "al", "bin", "ibn", "dos", "das",
              "ter", "ten", "af", "av"}

SYSTEM_PROMPT = """\
You research the published professional profile of a World Bank staff member \
who leads a project team.

Collect only information the person or their employer has published: job \
title, Global Practice or unit, duty station, and their World Bank e-mail \
address if a World Bank page prints it.

Rules:
- Never guess an e-mail address. Report one only if you saw it published on a \
worldbank.org page. If you did not, leave the field empty.
- Never report a personal e-mail address, personal phone number, home address, \
or any private detail, even if a site displays one.
- Ignore people-search sites, contact-scraping databases and data brokers.
- If several people share the name, prefer the one whose employer is the World \
Bank and whose sector matches the project given. If you cannot tell them \
apart, leave the fields empty rather than mixing them.

Answer with these lines and nothing else. Omit a line you have no value for:
TITLE: <job title>
UNIT: <Global Practice or department>
LOCATION: <duty station>
EMAIL: <worldbank.org address printed on a World Bank page>
LINK: <url of a page published by or about their work>
SUMMARY: <one sentence on their focus area>

If you cannot confidently identify this person at all, answer exactly NONE.\
"""


@dataclass(slots=True)
class PersonResult:
    title: str = ""
    unit: str = ""
    location: str = ""
    email: str = ""
    email_source: str = ""
    email_confidence: float | None = None
    links: list[dict] = field(default_factory=list)
    summary: str = ""
    source: str = ""
    #: False when the lookup never ran (no key, name too short) — as opposed to
    #: having run and found nothing, which is a real answer worth recording.
    checked: bool = True

    @property
    def found(self) -> bool:
        return bool(self.title or self.unit or self.email or self.links)


def name_slug(name: str) -> str:
    """Stable key for a person, tolerant of spelling drift between projects."""
    from ...contacts import name_key

    return name_key(name)[:160]


def derive_work_email(name: str) -> str:
    """The Bank's staff address pattern applied to a name.

    Bank addresses are first-initial + surname at the staff domain — Mohini Kak
    is ``mkak@worldbank.org``. This is a *candidate*: the pattern collides for
    common surnames and the Bank appends a digit to resolve it, which cannot be
    guessed. Callers must keep it labelled as derived.
    """
    parts = [p for p in re.split(r"[\s,]+", name.strip()) if p]
    # Drop particles and initials — neither carries into the address.
    words = [p for p in parts if len(p.strip(".")) > 1 and p.lower() not in _PARTICLES]
    if len(words) < 2:
        return ""

    initial = re.sub(r"[^a-z]", "", words[0].lower())[:1]
    surname = re.sub(r"[^a-z]", "", words[-1].lower())
    if not initial or len(surname) < 2:
        return ""
    return f"{initial}{surname}@{STAFF_DOMAIN}"


#: Prose a model writes instead of leaving a line out. The prompt asks for the
#: line to be omitted, and Claude obliges; Gemini tends to answer the question
#: literally ("Not specified in the provided search results."), which would be
#: stored and shown as if it were this person's duty station.
_MISSING_RE = re.compile(
    r"^(?:n/?a|none|unknown|unspecified|not\s+(?:specified|available|found|"
    r"mentioned|provided|listed|stated|publicly)|no\s+(?:information|data)|-+)\b",
    re.IGNORECASE,
)

#: A model reasoning out loud inside a field. Observed live on Gemini: a UNIT
#: of "Energy (implied by ... isn't explicitly stated on a single page for
#: him)". The hedge is the tell — a published job title never contains one, so
#: the whole value is discarded rather than shown as fact.
_HEDGE_RE = re.compile(
    r"not\s+explicitly|isn'?t\s+explicitly|implied\s+by|inferred|based\s+on\s+"
    r"(?:the\s+)?(?:report|search|available)|appears?\s+to\s+be|likely|"
    r"presumably|search\s+results|though\s+a\s+specific",
    re.IGNORECASE,
)

#: A real job title, unit or duty station is a phrase. Past this it is prose,
#: and truncating prose only yields a shorter ramble.
_MAX_PHRASE = 140


def _clean_value(raw: str, limit: int, *, phrase: bool = False) -> str:
    """A published value, or "" when the model was really saying "I don't know".

    ``phrase=True`` marks the short factual fields (title, unit, duty station),
    which are rejected outright when they arrive as a sentence. ``summary`` is
    prose by design and is merely truncated.
    """
    value = " ".join((raw or "").split())
    # Unwrap a value that is *entirely* parenthesised — "(Not explicitly
    # stated…)" would otherwise slip past a pattern anchored at the start.
    # Only when it wraps the whole value: a trailing ")" is usually part of the
    # text ("Finance, Competitiveness & Innovation Global Practice (FCI)").
    stripped = value
    while len(stripped) > 1 and stripped[0] in "([" and stripped[-1] in ")]":
        stripped = stripped[1:-1].strip()
    if not stripped or _MISSING_RE.match(stripped) or _HEDGE_RE.search(stripped):
        return ""
    if phrase and len(stripped) > _MAX_PHRASE:
        return ""
    return stripped[:limit]


def _classify_link(url: str) -> str:
    lowered = url.lower()
    for kind, fragments in _LINK_KINDS:
        if any(fragment in lowered for fragment in fragments):
            return kind
    return ""


def _is_acceptable_link(url: str) -> bool:
    try:
        parsed = urlparse(url)
    except ValueError:
        return False
    if parsed.scheme not in {"http", "https"} or "." not in parsed.netloc:
        return False
    if any(broker in parsed.netloc.lower() for broker in _REJECTED_HOSTS):
        return False
    # Only links that place the person professionally are worth storing; an
    # arbitrary page that merely mentions them is noise on a contact card.
    return bool(_classify_link(url))


def _parse_answer(answer: str) -> PersonResult:
    text = (answer or "").strip()
    if not text or text.upper().startswith("NONE"):
        return PersonResult(source=SOURCE_AI_SEARCH)

    result = PersonResult(source=SOURCE_AI_SEARCH)
    seen: set[str] = set()

    for line in text.splitlines():
        key, _, value = line.partition(":")
        key = key.strip().upper()
        value = value.strip()
        if not value:
            continue

        if key == "TITLE":
            result.title = _clean_value(value, 255, phrase=True)
        elif key == "UNIT":
            result.unit = _clean_value(value, 255, phrase=True)
        elif key == "LOCATION":
            result.location = _clean_value(value, 255, phrase=True)
        elif key == "EMAIL":
            match = _EMAIL_RE.search(value)
            # A published address is the only kind worth taking from the model;
            # anything off the staff domain is a guess wearing a label.
            if match and match.group(0).lower().endswith(f"@{STAFF_DOMAIN}"):
                result.email = match.group(0).lower()
                result.email_source = TeamLeadProfile.EmailSource.VERIFIED
                result.email_confidence = 0.9
        elif key == "LINK":
            for match in _URL_RE.finditer(value):
                url = match.group(0).rstrip(".,);")
                if url not in seen and _is_acceptable_link(url):
                    seen.add(url)
                    result.links.append({"url": url, "kind": _classify_link(url)})
        elif key == "SUMMARY":
            result.summary = _clean_value(value, 500)

    return result


def look_up_team_lead(name: str, *, country: str = "", project: str = "") -> PersonResult:
    """Search for one team lead's published professional profile.

    Always returns the pattern-derived address when the name allows one, so the
    caller has something to show even with AI disabled.
    """
    clean = (name or "").strip()
    derived = derive_work_email(clean)
    fallback = PersonResult(
        email=derived,
        email_source=TeamLeadProfile.EmailSource.PATTERN if derived else "",
        # Deliberately low: the pattern is right often enough to be useful and
        # wrong often enough that the UI must say it is unconfirmed.
        email_confidence=0.5 if derived else None,
        source=SOURCE_PATTERN if derived else "",
        checked=False,
    )

    if len(clean) < 4 or " " not in clean:
        return fallback
    if not search_enabled():
        return fallback

    prompt = [f"Name: {clean}", "Employer: World Bank Group"]
    if country:
        prompt.append(f"Country of the project: {country}")
    if project:
        prompt.append(f"Project: {project[:200]}")
    prompt += ["", "What is this person's published professional profile?"]

    try:
        answer = search_answer(
            system=SYSTEM_PROMPT,
            prompt="\n".join(prompt),
            max_searches=settings.ANTHROPIC["ENRICH_MAX_SEARCHES"],
        )
    except AIUnavailable as exc:
        logger.info("Team lead lookup unavailable (%s).", exc)
        return fallback
    except Exception as exc:  # noqa: BLE001 - enrichment is best-effort
        logger.warning("Team lead lookup failed for %r: %s", clean, exc)
        return fallback

    if not answer:
        logger.info("Team lead lookup declined for %r.", clean)
        return fallback

    result = _parse_answer(answer)

    # The derived address stands only while nothing better was published.
    if not result.email and derived:
        result.email = derived
        result.email_source = TeamLeadProfile.EmailSource.PATTERN
        result.email_confidence = 0.5

    return result


def enrich_team_lead(
    name: str, *, country: str = "", project: str = "", force: bool = False
) -> TeamLeadProfile | None:
    """Look one team lead up and store the result, once per person."""
    slug = name_slug(name)
    if not slug:
        return None

    profile = TeamLeadProfile.objects.filter(pk=slug).first()
    if profile is not None and profile.checked_at and not force:
        return profile

    result = look_up_team_lead(name, country=country, project=project)
    if profile is None:
        profile = TeamLeadProfile(slug=slug, name=name.strip())

    profile.name = name.strip() or profile.name
    profile.title = result.title or profile.title
    profile.unit = result.unit or profile.unit
    profile.country_office = result.location or profile.country_office
    profile.work_email = result.email or profile.work_email
    profile.email_source = result.email_source or profile.email_source
    profile.email_confidence = (
        result.email_confidence if result.email_confidence is not None
        else profile.email_confidence
    )
    profile.links = result.links or profile.links
    profile.profile_url = next(
        (link["url"] for link in profile.links if link.get("kind") == "worldbank"),
        profile.profile_url,
    )
    profile.summary = result.summary or profile.summary
    profile.source = result.source or profile.source

    # The Bank's own staff page, when there is one: deterministic, free and
    # first-party, so it runs whether or not a model was available. Applied
    # last because its title is the employer's own wording and must outrank
    # the model's reading of it.
    page = fetch_author_page(name)
    if page.found:
        profile.bank_page_url = page.url
        profile.bio = page.bio or profile.bio
        profile.photo_url = page.photo_url or profile.photo_url
        profile.title = page.title or profile.title
    profile.bank_page_checked_at = timezone.now()
    # A run that searched and found nothing is still a run: recording it stops
    # the same empty lookup being paid for on every sync.
    if result.checked:
        profile.checked_at = timezone.now()
    profile.save()
    return profile


def enrich_pending_team_leads(*, limit: int = 25, force: bool = False) -> dict[str, int]:
    """Enrich team leads named by mirrored projects but not yet looked up.

    Small by default for the same reason as the website lookup: every name is a
    live web search.
    """
    counters = {"checked": 0, "found": 0, "skipped": 0}

    # What counts as "already handled" depends on what this run can do. With a
    # key, only a completed lookup does — a row holding just the derived
    # address is still waiting for one. Without a key there is nothing further
    # to learn, so any existing row is done; otherwise every run would rewrite
    # the same first `limit` names and never reach the rest.
    done = TeamLeadProfile.objects.all()
    if search_enabled():
        done = done.exclude(checked_at__isnull=True)
    known = set() if force else set(done.values_list("slug", flat=True))

    pending: dict[str, tuple[str, str]] = {}
    for profile in ProjectProfile.objects.exclude(team_lead="").iterator():
        for raw in profile.team_lead.split(","):
            name = " ".join(raw.split())
            slug = name_slug(name)
            if not slug or slug in known or slug in pending:
                continue
            pending[slug] = (name, profile.country, profile.name)
            if len(pending) >= limit:
                break
        if len(pending) >= limit:
            break

    for name, country, project in pending.values():
        result = enrich_team_lead(name, country=country, project=project, force=force)
        if result is None:
            counters["skipped"] += 1
            continue
        counters["checked"] += 1
        if result.title or result.unit or result.links:
            counters["found"] += 1

    logger.info(
        "Team lead enrichment: %s checked, %s enriched, %s skipped",
        counters["checked"], counters["found"], counters["skipped"],
    )
    return counters


def profiles_for(names: list[str]) -> dict[str, dict]:
    """Stored enrichment for a list of published names, keyed by that name.

    Used by the detail serializer, so it must never trigger a lookup: the API
    shows what has already been found and the background job fills the rest.
    """
    wanted = {name_slug(name): name for name in names if name_slug(name)}
    if not wanted:
        return {}

    found = {}
    for profile in TeamLeadProfile.objects.filter(pk__in=wanted):
        found[wanted[profile.slug]] = {
            # The id the detail page is addressed by. Sent from here so the
            # client never has to reproduce the name fold — it exists only
            # because the same person is spelled differently across projects,
            # and two implementations of that would drift.
            "id": profile.slug.replace(" ", "-"),
            "title": profile.title,
            "unit": profile.unit,
            "country_office": profile.country_office,
            "work_email": profile.work_email,
            "email_source": profile.email_source,
            "email_confidence": profile.email_confidence,
            "email_confirmed": profile.is_email_confirmed,
            "profile_url": profile.profile_url,
            "links": profile.links,
            "summary": profile.summary,
            "checked_at": profile.checked_at,
        }
    return found
