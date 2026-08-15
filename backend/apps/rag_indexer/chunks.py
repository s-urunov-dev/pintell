"""What one indexed piece of a source is, and what travels with it.

This module is the contract between the four services in this app: extraction
produces :class:`Chunk` objects, embedding turns their ``content`` into
vectors, Qdrant stores the pair, and search hands the payload straight back to
a viewer that draws a box on a page. Nothing else in the app agrees on a shape,
so a change here is a change everywhere — which is the point of it being one
small file with no Django import in it.

Three decisions are frozen into the payload, and each rejected an easier one.

**A chunk points at a position; it never restates the source.** The payload
carries ``content`` because a search result has to be readable, but the
authority is the ``position_id`` and the geometry beside it. If the mirrored
document is re-parsed and the text moves, a stale ``content`` is a display bug;
a stale *position* would draw a yellow box over the wrong sentence, which reads
as a claim about the borrower's document. So positions are derived from the
same parse the viewer renders (``apps.compliance.spans``) rather than from a
second one written here — see ``services/extraction.py``.

**The filter keys are the ones the product already has, and none of them is an
integer.** ``TenderNotice``'s primary key *is* the upstream notice id — a
64-character string, not an auto-increment column — and ``category`` /
``subcategory`` are ``TextChoices`` slugs. So the payload stores
``notice_id: "OP00012345"`` and ``category: "consulting"``, and there is
nothing here to turn into a numeric id without minting one. Minting one would
be the worse option even if a column existed: it creates a second vocabulary
that drifts from ``apps/tenders/categories.py`` the first time a direction is
added, and every filter would need a lookup table to be read by a human.
Qdrant indexes keyword payloads natively, so a slug filters exactly as fast as
an integer would.

**A point's id is derived, not allocated.** ``point_id`` is a UUIDv5 over
``source_key`` and ``position_id``, so re-indexing a document upserts over its
own points instead of appending a second copy. Running the archive command
twice must cost time and nothing else; with random ids it would silently double
the collection and every search would return each hit twice.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any, Literal

#: Namespace for the derived point ids. A fixed, arbitrary UUID — its only job
#: is to keep this app's ids from colliding with anything else that might one
#: day share the collection. **Never change it**: every point in the archive
#: would get a new id and the next run would double the collection rather than
#: overwrite it.
POINT_NAMESPACE = uuid.UUID("6f1c1f1e-6b3a-5a2c-9a70-2f0e51f4a1d2")

SourceType = Literal["pdf", "text"]

PDF = "pdf"
TEXT = "text"


@dataclass(frozen=True)
class SourceRef:
    """The row a set of chunks came out of, flattened into filter keys.

    Held separately from the chunk because it is constant across the few
    hundred chunks of one document: building it once and passing it to
    ``payload`` is the difference between one dictionary and one per chunk.

    ``source_key`` is the identity — ``notice:<pk>`` or ``document:<url_hash>``
    — and it is what :class:`~apps.rag_indexer.models.IndexedSource` is keyed
    by. Two different notices linking the same TOR share one ``document:``
    source and are indexed once, exactly as the harvester fetches it once.
    """

    source_key: str
    source_type: SourceType
    #: ``TenderNotice.pk``, which *is* ``notice_id`` — the upstream string the
    #: public routes address notices by. One key, not two, because there is no
    #: second one to be out of step with.
    notice_id: str
    #: Direction slug (``apps.tenders.categories.TenderCategory``).
    category: str
    #: Consulting sub-direction slug, empty outside Consulting.
    subcategory: str
    #: ``HarvestedDocument.url_hash``, empty when the source is a notice body.
    document_id: str = ""
    #: What to call this source in a citation badge.
    title: str = ""
    #: Whether this notice is a finished contract award with a named winner.
    #:
    #: A payload field rather than something the caller joins afterwards,
    #: because it has to be filterable *inside* the store. An award notice
    #: reads nothing like a request for expressions of interest — one is prose
    #: about the work, the other a table of bid prices — so embedding
    #: similarity is dominated by document type, and a search over the whole
    #: collection from an open tender returns four hundred open tenders and
    #: zero awards. Measured on `OP00460945`: 426 neighbours, all of them
    #: opportunity notices.
    is_award: bool = False

    def point_id(self, position_id: str) -> str:
        """The deterministic id of one chunk's point. See the module docstring."""
        return str(uuid.uuid5(POINT_NAMESPACE, f"{self.source_key}|{position_id}"))


@dataclass(frozen=True)
class Chunk:
    """One passage, with enough geometry to point at it in the original.

    The two source types carry disjoint position fields and both are optional
    on the dataclass, which is a deliberate flattening: the alternative — two
    chunk classes, or a nested ``position`` object — would push a type switch
    into every consumer including the TypeScript one. ``source_type`` is the
    discriminator, it is always present, and the viewer switches on it once.

    ``bbox`` is ``(x0, top, x1, bottom)`` in PDF points with the origin at the
    **top left**, because that is what ``apps.compliance.spans.Span`` produces
    and what a browser canvas expects. The one coordinate flip in the system
    stays inside pdfplumber, where it already was.
    """

    position_id: str
    content: str
    source_type: SourceType

    # --- pdf ---------------------------------------------------------------
    page: int | None = None
    bbox: tuple[float, float, float, float] | None = None
    page_width: float | None = None
    page_height: float | None = None

    # --- text --------------------------------------------------------------
    char_start: int | None = None
    char_end: int | None = None
    sentence_index: int | None = None

    def payload(self, source: SourceRef) -> dict[str, Any]:
        """The Qdrant payload for this chunk under ``source``.

        Position keys are only emitted for the type that has them. An absent
        key and a null one are the same thing to Qdrant, but not to the client:
        ``page: null`` on a text chunk invites a viewer to try rendering page
        ``null``, while a missing key makes the switch on ``source_type`` the
        only way to read the payload — which is the way that stays correct.
        """
        payload: dict[str, Any] = {
            "source_key": source.source_key,
            "notice_id": source.notice_id,
            "category": source.category,
            "subcategory": source.subcategory,
            "document_id": source.document_id,
            "title": source.title,
            "is_award": source.is_award,
            "source_type": self.source_type,
            "position_id": self.position_id,
            "content": self.content,
        }

        if self.source_type == PDF:
            payload["page"] = self.page
            payload["bbox"] = [round(value, 2) for value in (self.bbox or ())]
            payload["page_width"] = round(self.page_width or 0.0, 2)
            payload["page_height"] = round(self.page_height or 0.0, 2)
        else:
            payload["char_start"] = self.char_start
            payload["char_end"] = self.char_end
            payload["sentence_index"] = self.sentence_index

        return payload
