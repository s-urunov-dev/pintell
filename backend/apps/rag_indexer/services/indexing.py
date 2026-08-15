"""Walking the archive once, without holding it in memory or redoing it.

The archive command is a thin shell over this class; a Celery task for newly
synced notices would be another. Both need the same three properties and none
of them is free:

**Resumable.** Rows are committed per *flush* — a few hundred passages — so a
run killed at hour three resumes at hour three, having lost at most the buffer
it was holding. The alternative, one transaction over the whole archive, would
make every interruption cost the entire run, and an import nobody dares start
is an import that never happens.

**Requests go out full.** Sources are buffered until there are enough passages
to fill the provider's batch. Measured on the deployed archive a source carries
**4.5 chunks** against a hundred-text request, so embedding per source spent
twenty-two requests' worth of quota on one request's worth of work — a first
pass projected to twenty-eight hours, against under two for the same tokens and
the same bill.

**Idempotent.** A source is skipped when its fingerprint, the embedding model
and the pipeline version all match what is already recorded. The fingerprint is
of the source's *input* — the sanitised notice body, the document's stored
bytes — not of the parsed output, because the whole point is to skip before
parsing: re-parsing sixteen thousand PDFs to discover none of them changed is
most of the cost of the run. Changes on our side of the parse are caught by
``PIPELINE_VERSION`` instead, which is exactly what it is for.

**Bounded in memory.** Notices stream through ``.iterator()`` with only the
columns needed, and the vectors of one batch are dropped before the next is
requested. The one thing held whole is the map of already-indexed fingerprints
— two strings per source, tens of thousands of sources — which is a few
megabytes and saves a query per notice.

Ordering is oldest-first and deliberate. A run that is stopped halfway has then
covered a contiguous, describable slice of the archive rather than a scatter,
and "everything before March 2024 is indexed" is a sentence an operator can act
on.
"""

from __future__ import annotations

import hashlib
import logging
from dataclasses import dataclass, field
from typing import Callable, Iterable, Iterator, Sequence

from django.conf import settings
from django.db.models import Prefetch, QuerySet
from django.utils import timezone

from apps.tenders.models import ContractAward, HarvestedDocument, TenderNotice

from ..models import PIPELINE_VERSION, IndexedSource
from .embedding import EmbeddingService, EmbeddingUnavailable, get_embedding_service
from .extraction import Extraction, ExtractionService
from .qdrant import QdrantService, QdrantUnavailable, get_qdrant_service

logger = logging.getLogger(__name__)

#: Rows pulled from Postgres per round trip while streaming. Small enough that
#: a notice body's worth of text times this fits comfortably in memory; large
#: enough that the query is not the bottleneck of an hours-long run.
DB_CHUNK_SIZE = 200


@dataclass
class RunStats:
    """What one pass did. Every field is a count the operator can check."""

    seen: int = 0
    indexed: int = 0
    skipped: int = 0
    empty: int = 0
    failed: int = 0
    chunks: int = 0
    chars: int = 0
    errors: list[str] = field(default_factory=list)

    def note_error(self, message: str) -> None:
        # Bounded: a run against a corpus with a systematically broken parser
        # would otherwise accumulate sixteen thousand copies of one sentence
        # and the summary would be unreadable at exactly the moment it mattered.
        if len(self.errors) < 20:
            self.errors.append(message)

    def as_dict(self) -> dict[str, int | list[str]]:
        return {
            "seen": self.seen,
            "indexed": self.indexed,
            "skipped": self.skipped,
            "empty": self.empty,
            "failed": self.failed,
            "chunks": self.chunks,
            "chars": self.chars,
            "errors": self.errors,
        }


@dataclass(frozen=True)
class Candidate:
    """One unit of work: a source, and the fingerprint that decides staleness.

    Carries the model rows rather than ids so the stream stays a stream — going
    back to the database per candidate would turn one sequential scan into tens
    of thousands of point lookups.
    """

    source_key: str
    kind: str
    fingerprint: str
    notice: TenderNotice
    document: HarvestedDocument | None = None


class IndexingService:
    """Extract, embed and upsert the archive, one bounded batch at a time."""

    def __init__(
        self,
        *,
        extraction: ExtractionService | None = None,
        embedding: EmbeddingService | None = None,
        store: QdrantService | None = None,
    ):
        self.extraction = extraction or ExtractionService()
        self.embedding = embedding or get_embedding_service()
        self.store = store or get_qdrant_service()
        #: Source keys already present in the bookkeeping table, so
        #: :meth:`index` knows whether a delete-before-write is needed.
        #: Populated by :meth:`run`; empty for a direct :meth:`index` call,
        #: which then skips a delete that would have been a no-op anyway.
        #: Per-instance rather than per-class — a shared set would let one
        #: run's history decide another run's deletes.
        self._known_keys: set[str] = set()
        #: Chunks buffered before a flush: exactly one full request's worth.
        #:
        #: It was ten times this, to keep the partly-filled tail request of a
        #: flush down to a tenth of the overhead. That reasoning assumed the
        #: quota counts HTTP calls. It counts texts (see ``EmbeddingService``),
        #: so a half-empty request costs nothing but the round trip — and the
        #: larger buffer only made an interruption expensive and the progress
        #: line coarse. One request per flush commits rows about once a minute
        #: on a paced run, which is what an operator watching a long import
        #: actually needs.
        self.flush_chunks: int = settings.RAG["EMBED_BATCH"]
        #: A second bound, on sources rather than passages. One mirrored
        #: bidding document can carry hundreds of chunks and several hundred
        #: kilobytes of text; without this a buffer of ordinary short notices
        #: would hold thousands of parses waiting to reach the chunk bound.
        self.flush_sources: int = 250

    # -- selecting work -----------------------------------------------------
    def notice_queryset(self, *, focus_only: bool) -> QuerySet[TenderNotice]:
        """Notices with a body worth indexing.

        ``.only()`` is not decoration here: the body column is the large one
        and this query walks the whole mirror. Pulling every column would drag
        the raw HTML — a second copy of the same text — through the same
        iterator for no reader.
        """
        queryset = TenderNotice.objects.all()
        if focus_only:
            queryset = queryset.in_country_group()
        return (
            queryset.exclude(notice_text_sanitized="")
            .only(
                "notice_id",
                "category",
                "subcategory",
                "bid_description",
                "project_name",
                "notice_text_sanitized",
            )
            .order_by("notice_id")
        )

    def document_queryset(self, *, focus_only: bool) -> QuerySet[HarvestedDocument]:
        """Mirrored documents that actually yielded text, with one notice each.

        ``usable()`` is the harvester's own definition of "worth reading" and
        is reused rather than restated — a second threshold for the same
        question is how the coverage figures in two screens come to disagree.

        The prefetch is bounded to one notice per document because that is all
        the payload needs (the filter keys), and a TOR shared by forty notices
        of one project would otherwise pull forty rows to read one category.
        """
        notices = TenderNotice.objects.only(
            "notice_id", "category", "subcategory", "bid_description",
            "project_name",
        )
        if focus_only:
            notices = notices.in_country_group()
        return (
            HarvestedDocument.objects.usable()
            .prefetch_related(Prefetch("notices", queryset=notices.order_by("notice_id")))
            .only(
                "url_hash", "url", "link_context", "content_type", "parser",
                "stored_path", "text", "sha256",
            )
            .order_by("created_at")
        )

    def candidates(
        self, *, kinds: Iterable[str], focus_only: bool, limit: int | None
    ) -> Iterator[Candidate]:
        """Stream the work, already filtered against what is current.

        The "already current" map is read once per kind. It is the only thing
        this method holds, and it is what turns a re-run over an unchanged
        archive from tens of thousands of parses into one indexed query.
        """
        wanted = set(kinds)
        produced = 0

        if IndexedSource.Kind.NOTICE in wanted:
            current = self._current_fingerprints(IndexedSource.Kind.NOTICE)
            for notice in self.notice_queryset(focus_only=focus_only).iterator(
                chunk_size=DB_CHUNK_SIZE
            ):
                key = f"notice:{notice.pk}"
                fingerprint = _digest(notice.notice_text_sanitized)
                if current.get(key) == fingerprint:
                    continue
                yield Candidate(key, IndexedSource.Kind.NOTICE, fingerprint, notice)
                produced += 1
                if limit and produced >= limit:
                    return

        if IndexedSource.Kind.DOCUMENT in wanted:
            current = self._current_fingerprints(IndexedSource.Kind.DOCUMENT)
            for document in self.document_queryset(focus_only=focus_only).iterator(
                chunk_size=DB_CHUNK_SIZE
            ):
                notice = next(iter(document.notices.all()), None)
                if notice is None:
                    # A document with no notice in scope has no direction and
                    # no tender to be found under. Skipped rather than indexed
                    # with blank filter keys, which would make it unreachable
                    # through any filter and reachable through every one.
                    continue
                key = f"document:{document.pk}"
                # The stored bytes' own hash — already computed by the
                # harvester, so the staleness test costs nothing here.
                fingerprint = document.sha256 or _digest(document.text)
                if current.get(key) == fingerprint:
                    continue
                yield Candidate(
                    key, IndexedSource.Kind.DOCUMENT, fingerprint, notice, document
                )
                produced += 1
                if limit and produced >= limit:
                    return

    def _current_fingerprints(self, kind: str) -> dict[str, str]:
        """``source_key -> content_hash`` for rows this pipeline still trusts.

        Failed rows are excluded, so a source that errored last run comes back
        into the queue on its own — a transient rate limit should not exile a
        document from the index until somebody notices.
        """
        rows = (
            IndexedSource.objects.filter(
                kind=kind,
                embed_model=self.embedding.model,
                pipeline_version=PIPELINE_VERSION,
            )
            .exclude(status=IndexedSource.Status.FAILED)
            .values_list("source_key", "content_hash")
        )
        return dict(rows)

    def pending_count(self, *, focus_only: bool) -> dict[str, int]:
        """Rough totals for the console: how much of the archive is indexed.

        Counted from Postgres rather than from Qdrant, and by source rather
        than by chunk, because "how many documents are searchable" is the
        question an operator has and "how many vectors exist" is not. Cheap
        enough to poll: two counts and two aggregates over indexed columns.
        """
        notices = self.notice_queryset(focus_only=focus_only).count()
        documents = self.document_queryset(focus_only=focus_only).count()
        done = IndexedSource.objects.filter(
            embed_model=self.embedding.model, pipeline_version=PIPELINE_VERSION
        ).exclude(status=IndexedSource.Status.FAILED)
        return {
            "notices_total": notices,
            "documents_total": documents,
            "sources_total": notices + documents,
            "notices_indexed": done.filter(kind=IndexedSource.Kind.NOTICE).count(),
            "documents_indexed": done.filter(kind=IndexedSource.Kind.DOCUMENT).count(),
            "failed": IndexedSource.objects.filter(
                status=IndexedSource.Status.FAILED
            ).count(),
        }

    # -- doing the work -----------------------------------------------------
    def prepare(self, candidate: Candidate) -> Extraction:
        """Parse one source, and clear whatever it left in the store before.

        Split out from the embedding half so a batch can hold the parses of
        twenty sources before spending a single request on them. Parsing is
        local and cheap; a request is neither.

        The delete happens here, **before the empty check** and before any
        vector is written. A source whose new parse yields fewer chunks leaves
        the surplus behind otherwise, and those points keep matching searches
        while pointing at offsets that no longer exist. The worst shape of it
        is a notice whose body was replaced by a one-line cancellation: it
        would keep serving every passage of the tender it no longer describes.
        Skipped for a source never indexed, which is every source on run one.
        """
        if candidate.document is not None:
            extraction = self.extraction.from_document(candidate.document, candidate.notice)
        else:
            extraction = self.extraction.from_notice(candidate.notice)

        if candidate.source_key in self._known_keys:
            self.store.delete_source(candidate.source_key)
        return extraction

    def index(self, candidate: Candidate) -> IndexedSource:
        """Extract, embed and upsert one source on its own.

        The single-source path, kept because it is the one a future Celery task
        for a newly synced notice wants — there, one notice *is* the batch.
        :meth:`run` does not use it: over an archive it would spend a request
        per source on an average of four and a half passages, when the provider
        takes a hundred in one.
        """
        extraction = self.prepare(candidate)
        if not extraction.chunks:
            return self._record(candidate, extraction, IndexedSource.Status.EMPTY, 0)
        written = self._embed_and_upsert([(candidate, extraction)])
        return self._record(
            candidate, extraction, IndexedSource.Status.INDEXED,
            written.get(candidate.source_key, 0),
        )

    def _embed_and_upsert(
        self, pending: Sequence[tuple[Candidate, Extraction]]
    ) -> dict[str, int]:
        """Embed every chunk of every buffered source in as few calls as it takes.

        This is the whole reason the buffer exists. Measured on the deployed
        archive, a source averages **4.5 chunks** while the provider accepts a
        hundred texts per request — so embedding per source spent twenty-two
        requests' worth of quota on one request's worth of work, and a first
        pass over 25,000 sources projected to roughly twenty-eight hours.
        Filling the requests instead brings it under two, at identical cost:
        the bill is per token, and the tokens are the same either way.

        Order is the contract, exactly as it is inside ``embed_documents``: the
        texts go out flattened and the vectors come back aligned, so the walk
        back over ``pending`` has to rebuild the same order it flattened. That
        is why the offsets are computed here rather than by zipping twice.
        """
        texts: list[str] = []
        for _candidate, extraction in pending:
            texts.extend(chunk.content for chunk in extraction.chunks)
        if not texts:
            return {}

        vectors = self.embedding.embed_documents(texts)

        written: dict[str, int] = {}
        cursor = 0
        points: list[tuple[str, list[float], dict]] = []
        for candidate, extraction in pending:
            source = extraction.source
            for chunk in extraction.chunks:
                points.append(
                    (
                        source.point_id(chunk.position_id),
                        vectors[cursor],
                        chunk.payload(source),
                    )
                )
                cursor += 1
            written[candidate.source_key] = len(extraction.chunks)

        # One upsert for the whole buffer. `QdrantService.upsert` splits it by
        # `UPSERT_BATCH` itself, so the wire batching stays that module's
        # business rather than being decided twice.
        self.store.upsert(points)
        return written

    def _record(
        self,
        candidate: Candidate,
        extraction: Extraction,
        status: str,
        chunk_count: int,
        error: str = "",
    ) -> IndexedSource:
        row, _ = IndexedSource.objects.update_or_create(
            source_key=candidate.source_key,
            defaults={
                "kind": candidate.kind,
                "notice": candidate.notice,
                "document_id": candidate.document.pk if candidate.document else "",
                "content_hash": candidate.fingerprint,
                "char_count": extraction.char_count,
                "chunk_count": chunk_count,
                "status": status,
                "last_error": error[:2000],
                "embed_model": self.embedding.model,
                "pipeline_version": PIPELINE_VERSION,
                "indexed_at": timezone.now(),
            },
        )
        self._known_keys.add(candidate.source_key)
        return row

    def _mark_failed(self, candidate: Candidate, error: str) -> None:
        IndexedSource.objects.update_or_create(
            source_key=candidate.source_key,
            defaults={
                "kind": candidate.kind,
                "notice": candidate.notice,
                "document_id": candidate.document.pk if candidate.document else "",
                "content_hash": candidate.fingerprint,
                "status": IndexedSource.Status.FAILED,
                "last_error": error[:2000],
                "embed_model": self.embedding.model,
                "pipeline_version": PIPELINE_VERSION,
                "indexed_at": timezone.now(),
            },
        )

    def run(
        self,
        *,
        kinds: Iterable[str] = (IndexedSource.Kind.NOTICE, IndexedSource.Kind.DOCUMENT),
        focus_only: bool = False,
        limit: int | None = None,
        on_progress: Callable[[Candidate, RunStats], None] | None = None,
    ) -> RunStats:
        """One pass, buffering sources so the requests go out full.

        **The error policy is the whole shape of this method.** A PDF that will
        not parse, a notice whose body is boilerplate — those are states of the
        archive, recorded and stepped over, because an archive of thirty
        thousand documents always contains some. A missing API key, an
        exhausted quota or a Qdrant that stopped answering is a state of the
        *deployment*, and continuing past it would burn the rest of the run
        marking every remaining source failed.

        **What buffering costs.** Rows are written after a flush, so an
        interrupted run redoes at most one buffer — a few hundred passages,
        seconds of work, and free of charge in the sense that matters: the
        point ids are derived, so redoing them overwrites rather than
        duplicates. That is the trade for cutting the request count by an
        order of magnitude, and it is the right way round.
        """
        stats = RunStats()
        self.store.ensure_collection()
        self._known_keys = set(
            IndexedSource.objects.values_list("source_key", flat=True)
        )

        pending: list[tuple[Candidate, Extraction]] = []
        buffered_chunks = 0

        def flush() -> bool:
            """Embed and record the buffer. False means stop the run."""
            nonlocal pending, buffered_chunks
            if not pending:
                return True
            try:
                written = self._embed_and_upsert(pending)
            except (EmbeddingUnavailable, QdrantUnavailable) as exc:
                stats.failed += len(pending)
                stats.note_error(str(exc))
                logger.error("Stopping the run: %s", exc)
                return False
            except Exception as exc:  # noqa: BLE001 - the buffer, not the run
                # One bad source poisons its whole buffer here, where the
                # per-source path would have isolated it. Accepted: the rows
                # are marked failed, a failed row is queued again next run, and
                # the next run's buffers will be cut differently — so a genuine
                # bad source is isolated by the retry rather than by a request
                # per source for all 25,000 of them.
                logger.warning("A buffer of %d sources failed: %s", len(pending), exc)
                for candidate, _extraction in pending:
                    stats.failed += 1
                    stats.note_error(f"{candidate.source_key}: {exc}")
                    self._mark_failed(candidate, str(exc))
                pending, buffered_chunks = [], 0
                return True

            for candidate, extraction in pending:
                count = written.get(candidate.source_key, 0)
                self._record(candidate, extraction, IndexedSource.Status.INDEXED, count)
                stats.indexed += 1
                stats.chunks += count
                stats.chars += extraction.char_count
            pending, buffered_chunks = [], 0
            return True

        for candidate in self.candidates(
            kinds=kinds, focus_only=focus_only, limit=limit
        ):
            stats.seen += 1
            try:
                extraction = self.prepare(candidate)
            except QdrantUnavailable as exc:
                stats.failed += 1
                stats.note_error(str(exc))
                logger.error("Stopping the run: %s", exc)
                break
            except Exception as exc:  # noqa: BLE001 - one bad source, not a run
                stats.failed += 1
                stats.note_error(f"{candidate.source_key}: {exc}")
                logger.warning("Could not read %s: %s", candidate.source_key, exc)
                self._mark_failed(candidate, str(exc))
            else:
                if extraction.chunks:
                    pending.append((candidate, extraction))
                    buffered_chunks += len(extraction.chunks)
                else:
                    # Nothing to embed, so nothing to wait for a batch with.
                    self._record(candidate, extraction, IndexedSource.Status.EMPTY, 0)
                    stats.empty += 1
                    stats.chars += extraction.char_count

            if buffered_chunks >= self.flush_chunks or len(pending) >= self.flush_sources:
                if not flush():
                    return stats

            if on_progress:
                on_progress(candidate, stats)

        flush()
        return stats


def _digest(text: str | None) -> str:
    return hashlib.sha256((text or "").encode("utf-8", "replace")).hexdigest()
