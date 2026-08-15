"""The World Bank staff named in a project's Environmental and Social Review Summary.

Tier 3 of :mod:`apps.tenders.contacts` — the Bank side of a project — was until
now a list of bare names. The projects API publishes ``teamleadname`` as a
comma-separated string and nothing else: no job title, no address, no way to
write to any of them. The module docstring there says as much, and calls the
tier "useful as context and escalation, never as the first door to knock on".

The ESRS closes exactly that gap, and it is the only mirrored source that does.
Every appraisal-stage ESRS ends with a section headed ``III. CONTACT POINT`` and
a ``World Bank`` block under it, listing the same people the feed names — with
their titles and their work addresses:

    CONTACT POINT
    World Bank
    Task Team Leader: Marina Novikova Title: Senior Social Protection Economist
    Email: mnovikova@worldbank.org
    TTL Contact: Solene Marie Paule Rougeaux Job Title: Senior Social Protection Specialist
    Email: srougeaux@worldbank.org

So this module reads that block, and nothing else in the document. Four
decisions worth stating, because each one had an easier alternative.

**Only the World Bank block of that section.** What follows it is ``FOR MORE
INFORMATION CONTACT`` — the Bank's switchboard in Washington, an address every
project shares, which tells a bidder in Tashkent nothing — and then
``APPROVAL``, which names the environmental and social specialists who cleared
the document **without addresses**. The older template also continues into
``Borrower/Client/Recipient`` and the implementing agencies, which is the
borrower's side and is not this module's to speak for. Reading any of them
would add rows that look like contacts and cannot be contacted, which is the
failure this module exists to end rather than to repeat.

**Two templates, and both are in the mirror.** The current one heads the
section ``III. CONTACT POINT`` and labels people ``Task Team Leader`` and ``TTL
Contact``; the older one heads it ``CONTACT POINTS``, numbers it ``V.``, labels
everybody ``Contact``, and prints a phone number the current one omits. Ten of
the nineteen ESRS files mirrored on 2026-08-14 are the older shape — a parser
that knew only the current one would have quietly halved the tier and left no
sign that it had.

**An address is only claimed for the person it follows.** The block is
line-oriented and pypdf keeps its line breaks, so ``Email:`` on the line after a
name belongs to that name (the older template puts it after the phone on that
same line). Where the layout breaks down — a page footer landing between them,
which happens — the person is still returned, with an empty address. A name
with a title is worth more than the feed's name alone, and a guessed address is
worth less than none.

**Header and footer noise is skipped, not stripped globally.** The parse runs
over the block between "CONTACT POINT" and the next roman-numbered heading, and
the Bank's disclosure furniture ("For Official Use Only", "Page 16 of 16") lands
inside it. A line is either a labelled contact line or it is ignored, so the
noise never has to be enumerated — which matters, because the furniture differs
between templates and a list of it would be a fact about the World Bank's
document generator that nobody here can source.

**Nothing is inferred about who is senior.** The template's own labels — one
``Task Team Leader`` and any number of ``TTL Contact`` — are carried through as
written. Whether the first is the person to write to is a judgement about the
Bank's internal division of work, and this module does not have it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

#: Where the block starts. Two templates are in the mirror and the heading
#: differs by a letter: the current one writes "III. CONTACT POINT", the older
#: one "CONTACT POINTS". The roman numeral is not required and is not matched —
#: it is III in the current template and V in the older one, and the phrase
#: appears nowhere else in an ESRS.
_BLOCK_START_RE = re.compile(r"\bCONTACT\s+POINTS?\b", re.IGNORECASE)

#: Where it ends. Two ways, whichever comes first: the next roman-numbered
#: heading (``IV. FOR MORE INFORMATION CONTACT``, or ``V.`` in the older
#: template — keyed on the numbering rather than the phrase so a renumbering
#: still terminates), or ``Borrower/Client/Recipient``, which is where the
#: older template ends its *World Bank* sub-block. Stopping at the sub-block
#: matters: everything past it is the borrower's side, and this module speaks
#: only for the Bank's.
_BLOCK_END_RE = re.compile(
    r"\n\s*[IVX]{1,5}\.\s+[A-Z]|\n\s*Borrower\s*/\s*Client\s*/\s*Recipient\b"
)

#: A person, in either template:
#:
#:   Task Team Leader: Marina Novikova Title: Senior Social Protection Economist
#:   TTL Contact: Zhihua Zeng Job Title: Senior Economist
#:   Contact: Maddalena Honorati Title: Senior Economist
#:
#: The title label is "Title" or "Job Title" depending on the row, which is the
#: template's own inconsistency. The name stops at that label, so it never
#: swallows the title.
#:
#: The bare ``Contact`` is safe here only because the block ends before
#: ``Borrower/Client/Recipient``: past that point the same shape introduces the
#: borrower's ministry, which is not a person and is not ours to publish.
_PERSON_RE = re.compile(
    r"^\s*(?P<label>Task\s+Team\s+Leader|TTL\s+Contact|Contact)\s*:\s*"
    r"(?P<name>.+?)"
    r"(?:\s+(?:Job\s+)?Title\s*:\s*(?P<title>.+?))?\s*$",
    re.IGNORECASE,
)

#: The address, which the two templates put in different places: on a line of
#: its own in the current one, and after the phone number on a shared line in
#: the older one ("Telephone No: 1-202-468103 Email: mhonorati@worldbank.org").
#: So the label is anchored to a line start *or* to the phone that precedes it,
#: never to a bare "Email:" mid-sentence.
_EMAIL_LINE_RE = re.compile(
    r"(?:^\s*|Telephone\s+No\s*:\s*\S[^\n]*?\s)E-?mail\s*:\s*(?P<email>\S+@\S+?)\s*$",
    re.IGNORECASE,
)

#: The phone, older template only. The current one publishes none, which is why
#: an empty string here is an ordinary answer rather than a parse failure.
_PHONE_LINE_RE = re.compile(
    r"^\s*Telephone\s+No\s*:\s*(?P<phone>.+?)(?:\s+E-?mail\s*:.*)?$", re.IGNORECASE
)

#: How many lines after a name to keep looking for that person's details.
#: Three, because a page break lands a footer line or two between them in the
#: longer documents; more than that and the next person's block is in range.
_EMAIL_WINDOW = 3

#: The label the current template gives the first person listed. The older one
#: labels everybody ``Contact`` and designates no lead — which is why nothing
#: here infers one.
ROLE_TASK_TEAM_LEADER = "Task Team Leader"


@dataclass(frozen=True, slots=True)
class EsrsContact:
    """One World Bank person as the ESRS publishes them."""

    name: str
    #: The job title as written — "Senior Social Protection Economist".
    title: str
    #: Their work address, or "" when the layout put it out of reach.
    email: str
    #: The template's own label, so the caller can tell the accountable lead
    #: from the rest without deciding what that means.
    label: str
    #: Older template only; "" everywhere else, because the current one
    #: publishes no phone number at all.
    phone: str = ""

    @property
    def is_lead(self) -> bool:
        return self.label.casefold() == ROLE_TASK_TEAM_LEADER.casefold()


def contact_block(text: str) -> str:
    """The text between the CONTACT POINT heading and the next roman heading.

    Returns "" when the document has no such section — a document that is not
    an ESRS, or an ESRS whose parse lost its headings. Both are ordinary states
    of the mirror and neither is an error.
    """
    if not text:
        return ""

    start = _BLOCK_START_RE.search(text)
    if start is None:
        return ""

    rest = text[start.end() :]
    end = _BLOCK_END_RE.search(rest)
    return rest[: end.start()] if end else rest


def extract_contacts(text: str) -> list[EsrsContact]:
    """Every person section III names, in the order the document lists them.

    Deduplicated on the folded name: a document that repeats a person under two
    labels is naming one human, and the first label — the more specific one, by
    the template's own ordering — is the one kept.
    """
    block = contact_block(text)
    if not block:
        return []

    lines = block.splitlines()
    contacts: list[EsrsContact] = []
    seen: set[str] = set()

    for index, line in enumerate(lines):
        match = _PERSON_RE.match(line)
        if match is None:
            continue

        name = _tidy(match.group("name"))
        # "Task Team Leader: " with nothing after it, or a name the label ate.
        if not _looks_like_a_name(name):
            continue
        key = name.casefold()
        if key in seen:
            continue
        seen.add(key)

        email, phone = _details_after(lines, index)
        contacts.append(
            EsrsContact(
                name=name,
                title=_tidy(match.group("title") or ""),
                email=email,
                phone=phone,
                label=_tidy(match.group("label")),
            )
        )

    return contacts


def _details_after(lines: list[str], index: int) -> tuple[str, str]:
    """The address and phone belonging to the person named on ``lines[index]``.

    Stops at the next person rather than running to the window's end: two names
    with one address between them is a layout this parser cannot resolve, and
    handing the second person's address to the first would publish a real
    address under the wrong name.
    """
    email = phone = ""
    for line in lines[index + 1 : index + 1 + _EMAIL_WINDOW]:
        if _PERSON_RE.match(line):
            break
        if not phone:
            found = _PHONE_LINE_RE.match(line)
            if found:
                phone = _tidy(found.group("phone"))
        if not email:
            found = _EMAIL_LINE_RE.search(line)
            if found:
                email = found.group("email").strip().rstrip(".,;")
        if email and phone:
            break
    return email, phone


def _looks_like_a_name(value: str) -> bool:
    """Whether what followed the label is a person rather than more furniture."""
    if not value or len(value) > 120:
        return False
    # At least one run of letters. Guards against a line where the parse left
    # only punctuation between the label and the title.
    return bool(re.search(r"[^\W\d_]{2,}", value, re.UNICODE))


def _tidy(fragment: str) -> str:
    return " ".join(fragment.split()).strip(" :.-")


def contacts_for(profile) -> list[EsrsContact]:
    """The ESRS contacts for one mirrored project, or an empty list.

    Empty is the ordinary answer and covers three different states, none of
    which is an error: the project publishes no ESRS, the ESRS is published but
    not mirrored yet (the harvester registers it and fetches on its own
    schedule), or the file is a scan with no text layer. A contact panel that
    shows the feed's bare names is what this deployment had before, and it is
    what it falls back to.

    The document is matched on the profile's own ``esrs_pdf_url`` rather than by
    searching the mirror for something ESRS-shaped: the projects API told us
    which file it is, and guessing would put the wrong document's staff under
    this project's name.
    """
    from .models import HarvestedDocument  # noqa: PLC0415 - keeps the parser pure
    from .services.harvest import url_key  # noqa: PLC0415 - same

    url = getattr(profile, "esrs_pdf_url", "") or ""
    if not url:
        return []

    document = HarvestedDocument.objects.filter(
        url_hash=url_key(url), status=HarvestedDocument.Status.FETCHED
    ).first()
    if document is None:
        return []
    return extract_contacts(document.text or "")
