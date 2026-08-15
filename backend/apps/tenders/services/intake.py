"""Documents a vendor obtained and gave us, rather than ones we found.

Most notices in the focus corpus state no qualification criteria at all — three
in four, measured (D12) — because the criteria live in a Terms of Reference the
notice does not link. What the notice *does* publish is a contact, and a vendor
who writes to that contact is usually sent the document. This module is the
other end of that loop: it takes what the vendor was sent and puts it in the
same corpus, so the same L3 reads it.

**Nothing here is a second harvester.** The fetch path, the SSRF guard, the
Google share-link rewriting, the content-addressed storage and every parser are
imported from ``harvest`` and called unchanged. Two implementations of "fetch a
URL a stranger gave us" would mean two places to get the SSRF guard right, and
the one that drifted would be the one facing a URL typed by a user — a strictly
more hostile input than a notice body, since the attacker here chooses it
directly rather than having to get it published by the World Bank first.

**The identity of an uploaded file is its content.** There is no URL to key on,
so the row is keyed on the SHA-256 of the bytes, which makes re-uploading the
same file idempotent and makes two vendors who obtained the same TOR share one
document rather than two copies of it. Storage was already content-addressed;
this only extends the same idea to the row.

**Provenance is recorded, not inferred.** ``origin=client_supplied`` marks these
rows for the rest of the system: the harvest queue skips them (they have nothing
to fetch), and a coverage figure that counted them as documents the mirror could
reach on its own would overstate what the mirror does.

One thing this module deliberately does not decide: whether a document one
vendor supplies may inform what another vendor is shown. That is a question
about a document obtained privately from a borrower's contact, and it is legal
before it is technical — logged as docs/OPEN-QUESTIONS.md Q13.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass
from typing import Any

import requests
from django.conf import settings
from django.db import transaction
from django.utils import timezone

from ..models import HarvestedDocument, TenderNotice
from . import harvest

logger = logging.getLogger(__name__)

#: What a vendor may hand us — exactly the set the harvester's own parsers read,
#: no wider. Accepting a format we cannot parse would store the bytes and then
#: report a document with no text, which to the vendor reads as "we lost your
#: file". Legacy ``.doc`` and ``.rtf`` are absent for that reason: neither has a
#: parser here, and adding them to the list would not add one.
ACCEPTED_SUFFIXES = (".pdf", ".docx", ".txt", ".md")


class IntakeRejected(ValueError):
    """The submission cannot be accepted, with a reason fit to show a user."""


@dataclass
class IntakeResult:
    """What happened to one submission."""

    document: HarvestedDocument | None = None
    created: bool = False
    #: Set when the document arrived but could not be read. Not an exception:
    #: a scanned TOR is a real outcome the vendor should be told about, not a
    #: failure of the request.
    problem: str = ""

    @property
    def readable(self) -> bool:
        return bool(self.document and self.document.is_usable)


def accept_upload(
    notice: TenderNotice,
    *,
    payload: bytes,
    filename: str,
    kind: str = HarvestedDocument.Kind.TOR,
    submitted_by: str = "",
) -> IntakeResult:
    """Take a file a vendor uploaded, parse it, attach it to the notice.

    Raises ``IntakeRejected`` only for submissions that are wrong on their face
    — empty, oversized, or a format no parser reads. Everything after that is
    reported on the result, because a document that arrives and turns out to be
    a scan is information rather than an error.
    """
    _assert_acceptable(payload, filename)

    digest = hashlib.sha256(payload).hexdigest()
    document, created = _register_upload(
        digest, filename=filename, kind=kind, submitted_by=submitted_by
    )

    if created or not document.is_usable:
        _parse_into(document, payload, filename)

    _attach(document, notice)
    problem = "" if document.is_usable else (document.last_error or "no text could be read")
    return IntakeResult(document=document, created=created, problem=problem)


def accept_url(
    notice: TenderNotice,
    *,
    url: str,
    kind: str = HarvestedDocument.Kind.TOR,
    submitted_by: str = "",
    session: requests.Session | None = None,
) -> IntakeResult:
    """Take a link a vendor pasted — a share link, a page, a direct file.

    The whole fetch is ``harvest.harvest_document``: the same rewriting of a
    Drive or Docs share link into something that serves bytes, the same refusal
    of any host that resolves to a private address, the same storage and the
    same parsers. What this adds is only provenance and the link to the notice.
    """
    normalised = harvest.normalise_url(url)
    if not normalised:
        raise IntakeRejected("no URL given")

    document, created = HarvestedDocument.objects.get_or_create(
        url_hash=harvest.url_key(normalised),
        defaults={
            "url": normalised,
            "kind": kind,
            "origin": HarvestedDocument.Origin.CLIENT_SUPPLIED,
            "link_context": _context(submitted_by, "link supplied by a vendor"),
        },
    )

    # A URL already in the mirror keeps whatever origin it had: it was publicly
    # linked, and a vendor pasting the same address does not make it private.
    if created or not document.is_usable:
        harvest.harvest_document(document, session=session)
        document.refresh_from_db()

    _attach(document, notice)
    problem = "" if document.is_usable else (document.last_error or "nothing readable at that link")
    return IntakeResult(document=document, created=created, problem=problem)


def supplied_for(notice: TenderNotice):
    """Readable documents vendors supplied for this notice."""
    return notice.harvested_documents.usable().filter(
        origin=HarvestedDocument.Origin.CLIENT_SUPPLIED
    )


def needs_a_document(notice: TenderNotice) -> bool:
    """Whether asking the notice's contact is the only way forward.

    True when nothing readable is attached at all. This is what the UI needs to
    decide between showing requirements and showing the contact to write to, and
    it is deliberately not "L1 found nothing": L1 finding nothing while a
    readable TOR is attached means L3 has work to do, not that the vendor should
    go and ask for a document we already hold.
    """
    return not notice.harvested_documents.usable().exists()


# ---------------------------------------------------------------------------
# Internals
# ---------------------------------------------------------------------------
def _assert_acceptable(payload: bytes, filename: str) -> None:
    if not payload:
        raise IntakeRejected("the file is empty")

    cap = settings.HARVEST["MAX_BYTES"]
    if len(payload) > cap:
        raise IntakeRejected(f"the file exceeds the {cap // (1024 * 1024)} MB limit")

    lowered = (filename or "").lower()
    if not lowered.endswith(ACCEPTED_SUFFIXES):
        raise IntakeRejected(
            "unsupported file type — accepted: " + ", ".join(ACCEPTED_SUFFIXES)
        )


def _register_upload(
    digest: str, *, filename: str, kind: str, submitted_by: str
) -> tuple[HarvestedDocument, bool]:
    """The row for an uploaded file, keyed on the bytes rather than a URL.

    ``url`` carries an ``upload:`` address that is not fetchable and is not
    meant to be: it exists because the column is the human-readable identity of
    a row, and leaving it blank would make a document nobody can name.
    """
    return HarvestedDocument.objects.get_or_create(
        url_hash=digest,
        defaults={
            "url": f"upload:{digest}",
            "kind": kind,
            "origin": HarvestedDocument.Origin.CLIENT_SUPPLIED,
            "link_context": _context(submitted_by, f"uploaded file: {filename[:120]}"),
        },
    )


def _parse_into(document: HarvestedDocument, payload: bytes, filename: str) -> None:
    """Store the bytes and extract text, using the harvester's own parsers."""
    document.attempts += 1
    document.byte_size = len(payload)
    document.fetched_at = timezone.now()
    # There was no HTTP request, so there is no status to report. Left null
    # rather than faked as 200: a synthetic success would make the corpus report
    # claim a fetch that never happened.
    document.http_status = None

    extraction = harvest.extract_text(payload, _content_type_for(filename))
    document.parser = extraction.get("parser", "")
    document.page_count = extraction.get("page_count")
    document.has_text_layer = extraction.get("has_text_layer")
    document.parse_error = (extraction.get("error") or "")[:500]

    text = (extraction.get("text") or "")[: settings.HARVEST["MAX_TEXT_CHARS"]]
    document.text = text
    document.text_chars = len(text)

    digest, path = harvest._store(payload, harvest._suffix_for(document.parser, payload))
    document.sha256 = digest
    document.stored_path = path

    if document.text_chars >= HarvestedDocument.MIN_USEFUL_CHARS:
        document.status = HarvestedDocument.Status.FETCHED
        document.last_error = ""
    else:
        # Same distinction the harvester draws: bytes arrived, nothing readable
        # came out. Almost always a scan. The file is kept — OCR can revisit it
        # without asking the vendor for it again.
        document.status = HarvestedDocument.Status.NO_TEXT
        document.last_error = document.parse_error or "no text layer"

    document.next_retry_at = None
    document.save()


def _content_type_for(filename: str) -> str:
    """A content type for the parser dispatcher, derived from the name.

    An upload has no ``Content-Type`` header worth trusting — browsers send
    ``application/octet-stream`` for anything they do not recognise. The
    extension is a better signal, and ``harvest.extract_text`` sniffs the magic
    bytes anyway, so this only has to be close enough not to mislead it.
    """
    lowered = (filename or "").lower()
    if lowered.endswith(".pdf"):
        return "application/pdf"
    if lowered.endswith((".docx", ".doc")):
        return "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
    return "text/plain"


def _attach(document: HarvestedDocument, notice: TenderNotice) -> None:
    with transaction.atomic():
        document.notices.add(notice)


def _context(submitted_by: str, what: str) -> str:
    who = f" by {submitted_by}" if submitted_by else ""
    return f"{what}{who}"[:500]
