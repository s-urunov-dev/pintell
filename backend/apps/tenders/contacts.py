"""Every person a bidder can reach about a notice, ranked by usefulness.

A World Bank notice carries people in three different places, and only the
first of them is a real field:

1. **The notice's own contact fields** (``contactname``, ``contactemail``, …).
   The borrower's named contact — filled on 99.9% of opportunity notices, and
   on none of the award notices. Complete: name, organisation, phone, address.

2. **The address block inside the notice body.** Every notice ends with some
   variant of "The address referred to above is: … Attn: … E-mail: …", and it
   is *not* a copy of the fields above. Measured over the live focus feed, 28
   of 29 notices carry an e-mail in the body and 17 of them carry one the
   structured field does not have. The difference matters: on OP00459323 the
   field holds the unit's general address (``aedpmu@gmail.com``) while the body
   names the one bids are actually delivered to (``procurement.srasp@…``).
   Dropping tier 2 therefore loses the submission address on more than half the
   notices.

3. **The project's World Bank task team leaders** — the Bank staff accountable
   for the project. They come from a different API and apply to the project
   rather than to this notice, so they are still the last door to knock on; but
   they are no longer a list of bare names. The projects feed publishes
   ``teamleadname`` and nothing else, and this tier used to end there. The
   project's **ESRS** names the same people with their job titles and their work
   addresses (:mod:`apps.tenders.esrs`), so those are merged in by name: on
   P504420 the feed's "Marina Novikova, Solene Marie Paule Rougeaux, Vlad
   Alexandru Grigoras" become three reachable people with titles. Where the ESRS
   is not mirrored, the tier degrades to exactly what it was.

That order is the priority order the UI renders, and it is deliberately by
*source strength* rather than by job title: the borrower publishes tier 1 as
fact, tier 2 is read out of prose by this module and can be wrong, and tier 3
cannot be written to without enrichment. Roles ("who is the state's
responsible officer" versus "who published the notice") are not separable in
the data — on OP00456662 the NASP official and the PIU publisher are the same
human, reachable at two addresses — so the tiers say where a detail came from
and let the reader judge, instead of inventing a distinction upstream does not
make.

Tier 2 is deduplicated against tier 1 by address, and entries whose name
matches the primary contact are flagged rather than dropped: a second address
for the same person is worth showing, a second *card* for them is not.
"""

from __future__ import annotations

import html
import re
from dataclasses import dataclass, field, replace

from .notice_links import _EMAIL_RE, _TOR_RE, _clean_email

# --- tiers -----------------------------------------------------------------

TIER_NOTICE = "notice"
TIER_BODY = "body"
TIER_BANK = "bank"

#: Ranking used for both ordering and the UI's tier badge. Lower is stronger.
TIER_PRIORITY = {TIER_NOTICE: 1, TIER_BODY: 2, TIER_BANK: 3}

# Where a value came from, so the UI can separate published fact from parsed
# prose from derived guess. Never collapse these into one flag.
SOURCE_NOTICE_FIELDS = "notice_fields"
SOURCE_NOTICE_BODY = "notice_body"
SOURCE_PROJECT_FEED = "project_feed"
#: The same tier, but the detail came out of the project's published ESRS
#: rather than out of a name list or a web search. Kept apart from
#: ``SOURCE_PROJECT_FEED`` because the address on such a row was published by
#: the World Bank, and the UI must be able to say so.
SOURCE_PROJECT_ESRS = "project_esrs"

# --- what an address is for ------------------------------------------------

PURPOSE_SUBMISSION = "submission"
PURPOSE_ENQUIRY = "enquiry"
PURPOSE_TOR = "tor"

#: Read from the sentence *before* an address. Ordered: a sentence that both
#: invites questions and names a delivery address is about delivery, because
#: getting the submission address wrong costs the bid.
_PURPOSE_PATTERNS = (
    (
        PURPOSE_SUBMISSION,
        re.compile(
            r"must\s+be\s+(?:delivered|submitted|sent|received)|"
            r"shall\s+be\s+(?:delivered|submitted|sent)|"
            r"expressions?\s+of\s+interest.{0,80}?(?:to|at)\b|"
            r"(?:bids?|quotations?|proposals?|applications?)\b.{0,60}?"
            r"(?:delivered|submitted|sent)|"
            r"deliver(?:ed)?\s+(?:electronically\s+)?to|"
            # "For the purpose of inquiries, receipt and submission of tender
            # documents:" — names both jobs, and delivery is the costly one.
            r"(?:receipt\s+and\s+)?submission\s+of",
            re.IGNORECASE | re.DOTALL,
        ),
    ),
    (
        PURPOSE_TOR,
        _TOR_RE,
    ),
    (
        PURPOSE_ENQUIRY,
        re.compile(
            r"further\s+information|more\s+information|additional\s+information|"
            r"clarification|inquir|enquir|questions?\s+(?:may|should|can)|"
            r"obtained?\s+(?:at|from|through)",
            re.IGNORECASE,
        ),
    ),
)

# --- reading the address block --------------------------------------------

#: The labels borrowers put in an address block. They matter twice: they end a
#: job title, and they mark where a phone number starts. `html_to_text`
#: collapses every newline to a space, so these labels are the only structure
#: left in the block.
_LABELS = (
    r"Attn|Attention|Address|Street|City|Country|Postal|Zip|"
    r"Tel(?:ephone)?|Phone(?:\s+number)?|Mobile|Cell|Fax|E-?mail|Web\s*site|URL"
)

#: "Attn: Mr. Mirodil Khusanov - Project Coordinator", "To: Satori D.A. - PMU
#: Director". The honorific is optional and dropped; initials ("D.A.") are
#: accepted as a name token because several borrowers write surname-first.
#:
#: The job title deliberately admits no digits: the block runs straight on into
#: the street address ("Project Coordinator 27, Nazim Hikmet str."), and the
#: house number is the only reliable mark of where the title stopped.
_ATTN_RE = re.compile(
    r"\b(?:Attn|Attention|To|Contact\s+person|Contact)\s*[:.]\s*"
    r"(?:(?:Mr|Mrs|Ms|Miss|Dr|Eng|Ing|Prof|Sr|Sra)\.?\s+)?"
    r"(?P<name>(?:[A-Z][\w'’\-]+|[A-Z]\.(?:[A-Z]\.)*)"
    r"(?:\s+(?:[A-Z][\w'’\-]+|[A-Z]\.(?:[A-Z]\.)*)){0,3})"
    r"(?:\s*[,\-–—]\s*(?P<role>[A-Za-z][A-Za-z\s/&'’-]{2,58}?))?"
    rf"(?=\s*(?:{_LABELS})\s*[:.]|[.;,]|\s+\d|\s*$)",
    re.UNICODE,
)

#: The country code is as often bracketed as prefixed — "Tel: (99412) 5253449"
#: alongside "Phone number: +998712395976" — so a leading paren opens a number.
_PHONE_RE = re.compile(
    rf"(?:{_LABELS})\s*[:.]?\s*(?P<phone>[+(]?\d[\d\s()\-–./]{{6,24}}\d)",
    re.IGNORECASE,
)

#: How far after a name to keep collecting that person's details. Long enough
#: to span "Attn … Address … Phone … E-mail", short enough not to swallow the
#: next person's block.
_PERSON_WINDOW = 420

#: How far back to read to decide what an address is for.
_PURPOSE_CONTEXT = 200

#: Addresses that are a mailbox, not a person. Kept — they are usually *the*
#: submission address — but never given a guessed name.
_ROLE_ACCOUNT_HINTS = (
    "procurement", "tender", "bids", "bid", "info", "office", "piu", "pmu",
    "pcu", "admin", "secretariat", "contact", "mail", "eoi",
)


@dataclass(frozen=True, slots=True)
class Contact:
    """One reachable person (or role mailbox) attached to a notice."""

    tier: str
    source: str
    name: str = ""
    role: str = ""
    organization: str = ""
    email: str = ""
    #: Further addresses for the same person, in the order they were published.
    alternate_emails: tuple[str, ...] = ()
    phone: str = ""
    address: str = ""
    country: str = ""
    website: str = ""
    #: What the surrounding sentence said this address is for.
    purpose: str = ""
    #: The sentence fragment that introduced it, so the UI can show the
    #: borrower's own wording instead of asking the reader to trust a label.
    context: str = ""
    #: True when this tier-2 entry is the tier-1 contact reached another way.
    same_as_primary: bool = False

    # --- tier 3 only ------------------------------------------------------
    #: Whether ``email`` was seen published or merely derived from the Bank's
    #: staff address pattern. The UI must not present the two the same way.
    email_confirmed: bool = False
    #: Public professional links: [{"url": …, "kind": …}].
    links: tuple[dict, ...] = ()
    summary: str = ""
    #: Set when this person has a stored profile page; "" when nobody has
    #: looked them up, in which case there is nothing to link to.
    profile_id: str = ""

    @property
    def priority(self) -> int:
        return TIER_PRIORITY[self.tier]

    @property
    def is_reachable(self) -> bool:
        return bool(self.email or self.phone)


def _normalise_name(raw: str) -> str:
    """Fold a name to a comparable key.

    Latin transliterations of the same Central Asian surname differ by
    convention rather than by person — the notice that names "Mirodil Khusanov"
    also carries ``m.xusanov@ihma.uz``. Folding ``kh``→``x`` and dropping
    apostrophes catches that pair and the common ``o'``/``o`` split, which is
    enough to stop the same human appearing twice. It is a heuristic and only
    ever used to *merge* entries, never to invent an address.
    """
    text = raw.lower().replace("’", "").replace("'", "").replace("`", "")
    text = re.sub(r"[^a-z\s]", " ", text)
    text = text.replace("kh", "x").replace("ph", "f")
    return " ".join(text.split())


def name_key(raw: str) -> str:
    """The fold above, for callers that need to key a person across sources."""
    return _normalise_name(raw)


def _name_tokens(raw: str) -> set[str]:
    """Name parts long enough to identify someone — initials are not."""
    return {token for token in _normalise_name(raw).split() if len(token) > 2}


def same_person(left: str, right: str) -> bool:
    """Whether two published names plausibly denote one person."""
    first, second = _name_tokens(left), _name_tokens(right)
    if not first or not second:
        return False
    return bool(first & second)


def _looks_like_role_account(email: str) -> bool:
    local = email.split("@", 1)[0].lower()
    return any(hint in local for hint in _ROLE_ACCOUNT_HINTS)


def _email_belongs_to(email: str, name: str) -> bool:
    """Whether an address in someone's block is actually theirs.

    A block routinely lists several addresses under one "Attn:" line, and they
    are not all the same person — OP00456662 signs off with the coordinator's
    unit mailbox, his own agency address, and a colleague's. Treating the whole
    run as his would publish one person under another's name, so an address is
    only kept when its local part names him (``m.xusanov`` for Mirodil
    Khusanov, after the transliteration fold) or when it is a unit mailbox that
    belongs to no individual. Everything else becomes its own entry — nameless,
    because the notice never named it.
    """
    if _looks_like_role_account(email):
        return True
    local = re.sub(r"[._\-+\d]+", " ", email.split("@", 1)[0])
    return same_person(local, name)


def _classify_purpose(context: str) -> str:
    for purpose, pattern in _PURPOSE_PATTERNS:
        if pattern.search(context):
            return purpose
    return ""


def _tidy(fragment: str) -> str:
    return " ".join(fragment.split())


def _clean_role(raw: str | None) -> str:
    """Trim a captured job title down to something worth printing."""
    if not raw:
        return ""
    role = _tidy(raw).strip(" -–—,.")
    # A title is a couple of words; anything longer is a sentence the regex
    # ran into, and printing it as a job title would be worse than nothing.
    if not role or len(role) > 60 or len(role.split()) > 7:
        return ""
    return role


@dataclass(slots=True)
class _Anchor:
    """A name found in the body, with the span that belongs to it."""

    name: str
    role: str
    start: int
    end: int
    emails: list[str] = field(default_factory=list)
    phone: str = ""


def _find_anchors(text: str) -> list[_Anchor]:
    anchors: list[_Anchor] = []
    for match in _ATTN_RE.finditer(text):
        name = _tidy(match.group("name"))
        # "To: The Address" and friends — a label, not a person.
        if len(_name_tokens(name)) == 0:
            continue
        anchors.append(
            _Anchor(
                name=name,
                role=_clean_role(match.group("role")),
                start=match.start(),
                end=match.end(),
            )
        )

    # Each anchor owns the text up to the next one, capped so a notice that
    # names someone early does not claim addresses from the far end.
    for index, anchor in enumerate(anchors):
        limit = anchors[index + 1].start if index + 1 < len(anchors) else len(text)
        anchor.end = min(limit, anchor.end + _PERSON_WINDOW)

    return anchors


def extract_body_contacts(text: str) -> list[Contact]:
    """Read the notice body's address block into contacts.

    Addresses are attached to the nearest preceding name when there is one, so
    "Attn: Mr. X … E-mail: a@b; c@d" yields one person with two addresses
    rather than two anonymous rows.
    """
    if not text:
        return []

    text = html.unescape(text)
    anchors = _find_anchors(text)

    for match in _PHONE_RE.finditer(text):
        for anchor in anchors:
            if anchor.start <= match.start() < anchor.end and not anchor.phone:
                anchor.phone = _tidy(match.group("phone"))
                break

    contacts: list[Contact] = []
    seen: set[str] = set()

    for match in _EMAIL_RE.finditer(text):
        email = _clean_email(match.group(0))
        if not email or email.lower() in seen:
            continue
        seen.add(email.lower())

        context = _tidy(text[max(0, match.start() - _PURPOSE_CONTEXT) : match.start()])
        purpose = _classify_purpose(context)

        owner = next(
            (a for a in anchors if a.start <= match.start() < a.end),
            None,
        )
        if owner is not None and _email_belongs_to(email, owner.name):
            owner.emails.append(email)
            continue

        contacts.append(
            Contact(
                tier=TIER_BODY,
                source=SOURCE_NOTICE_BODY,
                email=email,
                purpose=purpose,
                context=context[-160:],
            )
        )

    # Named people first: they are the ones a reader can address by name.
    named = [
        Contact(
            tier=TIER_BODY,
            source=SOURCE_NOTICE_BODY,
            name=anchor.name,
            role=anchor.role,
            # A block that names someone and gives only a phone is common
            # enough (and still reachable), so the address list may be empty.
            email=anchor.emails[0] if anchor.emails else "",
            alternate_emails=tuple(anchor.emails[1:]),
            phone=anchor.phone,
            purpose=_classify_purpose(
                _tidy(text[max(0, anchor.start - _PURPOSE_CONTEXT) : anchor.start])
            ),
        )
        for anchor in anchors
        if anchor.emails or anchor.phone
    ]

    return named + contacts


def _primary_contact(notice) -> Contact | None:
    """Tier 1 — the notice's own contact fields, as published."""
    fields = (
        notice.contact_name, notice.contact_email,
        notice.contact_phone_no, notice.contact_organization,
    )
    if not any(value.strip() for value in fields):
        return None

    return Contact(
        tier=TIER_NOTICE,
        source=SOURCE_NOTICE_FIELDS,
        name=notice.contact_name.strip(),
        organization=notice.contact_organization.strip(),
        email=notice.contact_email.strip(),
        phone=notice.contact_phone_no.strip(),
        address=_tidy(notice.contact_address),
        country=notice.contact_country.strip(),
        website=notice.contact_web_url.strip(),
    )


def _dedupe_against_primary(
    found: list[Contact], primary: Contact | None
) -> list[Contact]:
    """Drop what tier 1 already says; flag what merely repeats its person."""
    if primary is None:
        return found

    known = {primary.email.lower()} if primary.email else set()
    digits = lambda value: re.sub(r"\D", "", value or "")  # noqa: E731
    primary_phone = digits(primary.phone)
    kept: list[Contact] = []

    for contact in found:
        addresses = [contact.email, *contact.alternate_emails]
        fresh = [a for a in addresses if a and a.lower() not in known]
        # The block usually repeats the published number; a repeat is not a
        # second way to reach anyone, so it only counts when it differs.
        phone = "" if digits(contact.phone) == primary_phone else contact.phone
        if not fresh and not phone:
            continue
        known.update(a.lower() for a in fresh)

        kept.append(
            replace(
                contact,
                email=fresh[0] if fresh else "",
                alternate_emails=tuple(fresh[1:]),
                phone=phone,
                # A different address for a person tier 1 already named. Shown,
                # but as a second address rather than a second person.
                same_as_primary=bool(contact.name)
                and same_person(contact.name, primary.name),
            )
        )

    return kept


def _bank_contacts(
    profile, enrichment: dict | None = None, esrs_contacts=()
) -> list[Contact]:
    """Tier 3 — the project's task team leaders, as three sources agree on them.

    The feed decides *who* is in this tier and in what order; it is the only
    source that says "these are the people accountable for this project". The
    other two only fill the row in:

    ``esrs_contacts`` are :class:`apps.tenders.esrs.EsrsContact` rows read out of
    the project's own Environmental and Social Review Summary — a published
    World Bank document, so its address is a fact rather than a derivation, and
    ``email_confirmed`` is set accordingly. This is the difference between a
    tier a bidder can act on and a tier that only names people at them.

    ``enrichment`` maps a team lead's name to whatever
    :mod:`apps.tenders.services.ai.people` has found for them. It is searched
    rather than published, so where the two disagree the ESRS wins on the job
    title and on the address, and enrichment keeps what it alone carries — the
    country office, the profile page, the links, the summary.

    A person the ESRS names and the feed does not is still added, at the end.
    The two lists agree on every project measured, but they are separate
    publications: a lead who joined after the feed row was last mirrored would
    otherwise be dropped by the source that is more current.
    """
    if profile is None:
        return []

    enrichment = enrichment or {}
    from_esrs = {name_key(row.name): row for row in esrs_contacts}
    used: set[str] = set()

    contacts = []
    for raw in (profile.team_lead or "").split(","):
        name = _tidy(raw)
        if not name:
            continue
        found = enrichment.get(name) or {}
        key = name_key(name)
        published = from_esrs.get(key)
        if published is not None:
            used.add(key)
        contacts.append(
            _bank_contact(name=name, published=published, found=found)
        )

    contacts.extend(
        _bank_contact(name=row.name, published=row, found={})
        for key, row in from_esrs.items()
        if key not in used
    )
    return contacts


def _bank_contact(*, name: str, published, found: dict) -> Contact:
    """One tier-3 row, preferring what the ESRS published over what was found."""
    email = (published.email if published else "") or found.get("work_email", "")
    return Contact(
        tier=TIER_BANK,
        source=SOURCE_PROJECT_ESRS if published is not None else SOURCE_PROJECT_FEED,
        name=name,
        role=(published.title if published else "") or found.get("title", ""),
        organization=found.get("unit", "") or "World Bank",
        country=found.get("country_office", ""),
        email=email,
        # Only the older ESRS template carries one, so most rows have none.
        # It is the first phone number this tier has ever been able to show.
        phone=published.phone if published else "",
        # A published address is confirmed by the fact of its publication. A
        # found one is confirmed only where the finder said so, and the UI is
        # required to present the two differently.
        email_confirmed=bool(published and published.email)
        or bool(found.get("email_confirmed")),
        website=found.get("profile_url", ""),
        links=tuple(found.get("links") or ()),
        summary=found.get("summary", ""),
        profile_id=found.get("id", ""),
    )


def build_contacts(
    notice, profile=None, *, body_text: str = "", enrichment=None, esrs_contacts=()
) -> dict:
    """The full, priority-ordered contact set for one notice.

    ``body_text`` is the already-flattened notice body; callers that have it
    (the detail serializer flattens once for the TOR panel too) should pass it
    rather than pay for a second flatten.

    ``esrs_contacts`` is read from the mirror by the caller rather than here, so
    this module keeps no database dependency — the same reason ``body_text``
    arrives as a string.
    """
    primary = _primary_contact(notice)
    body = _dedupe_against_primary(extract_body_contacts(body_text), primary)
    bank = _bank_contacts(profile, enrichment, esrs_contacts)

    groups = []
    for tier, people in ((TIER_NOTICE, [primary] if primary else []),
                         (TIER_BODY, body),
                         (TIER_BANK, bank)):
        if people:
            groups.append({
                "tier": tier,
                "priority": TIER_PRIORITY[tier],
                "contacts": people,
            })

    return {"groups": groups, "total": sum(len(g["contacts"]) for g in groups)}
