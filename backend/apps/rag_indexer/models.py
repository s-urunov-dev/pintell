"""What has been embedded, so that embedding it again costs nothing.

Qdrant holds the vectors; this table holds the *bookkeeping* about them, and
the split is not redundancy. A collection of eight million points can answer
"is this point here" but not the two questions the archive run actually asks:
**has this source changed since it was embedded**, and **how far through the
archive are we**. Answering either from Qdrant would mean scrolling the whole
collection.

It exists because of the size of the job. The mirror holds tens of thousands of
notices and a document corpus measured in tens of millions of characters; one
pass over it is hours of metered API calls. A run that cannot be interrupted and
resumed is a run nobody dares start, so the command commits a row per source as
it goes and the next invocation skips what is already current. ``content_hash``
is what makes "current" a fact rather than a hope: a notice whose body was
re-sanitised, or a document re-parsed by a newer harvester, hashes differently
and comes back into the queue on its own.

``PIPELINE_VERSION`` is the same idea for changes on our side, and it follows
the convention ``ContractAward.parser_version`` already set here: bump it when
chunking, the embedding model, or the payload shape changes, and every source
is stale at once without anybody having to write a migration that deletes rows.
"""

from __future__ import annotations

import uuid

from django.conf import settings
from django.db import models
from django.utils import timezone

from apps.tenders.models import TenderNotice

#: Bump on any change to chunking, the payload schema, or the embedding model
#: choice. Sources embedded under an older version are treated as stale and
#: re-embedded on the next archive run.
#:
#: 1 — first release: sentence chunks for notice bodies, line-block chunks with
#:     bounding boxes for mirrored PDFs.
PIPELINE_VERSION = 1


class IndexedSourceQuerySet(models.QuerySet):
    def stale(self, *, model: str, pipeline_version: int = PIPELINE_VERSION):
        """Rows whose vectors no longer describe what they claim to.

        A source is stale when it was embedded by an older pipeline or by a
        different embedding model. Changing ``RAG_EMBED_MODEL`` therefore
        invalidates the archive without a migration — which is the honest
        behaviour, because vectors from two models are not comparable and a
        collection holding both silently returns nonsense for whichever half
        did not match the query.
        """
        return self.exclude(
            embed_model=model, pipeline_version=pipeline_version
        )

    def indexed(self):
        return self.filter(status=IndexedSource.Status.INDEXED)


class IndexedSource(models.Model):
    """One notice body or one mirrored document, and how it was indexed.

    Keyed by ``source_key`` rather than by a foreign key to either table,
    because the two kinds of source live in different models and a nullable
    pair of FKs would make every query ask which one is set. The typed columns
    beside it (``notice``, ``document_id``) are for reporting and cascade, not
    for identity.
    """

    class Kind(models.TextChoices):
        NOTICE = "notice", "Notice body"
        DOCUMENT = "document", "Mirrored document"

    class Status(models.TextChoices):
        INDEXED = "indexed", "Embedded and upserted"
        # Parsed fine, produced nothing worth embedding: a notice body of two
        # lines, a scanned PDF with no text layer. Recorded rather than left
        # absent so the next run does not re-parse it forever, and so the
        # coverage figure can separate "not done" from "nothing there".
        EMPTY = "empty", "No chunks worth indexing"
        # The embedding call or the upsert failed. Retried on the next run;
        # the message is kept because a whole run failing the same way is the
        # difference between a rate limit and a bad key.
        FAILED = "failed", "Embedding or upsert failed"

    source_key = models.CharField(max_length=96, primary_key=True)
    kind = models.CharField(max_length=16, choices=Kind.choices, db_index=True)

    #: The notice this text belongs to. For a document source it is the notice
    #: the run attributed it to — one TOR is shared by several notices of a
    #: project, and the payload's filter keys have to come from one of them.
    notice = models.ForeignKey(
        TenderNotice,
        on_delete=models.CASCADE,
        related_name="indexed_sources",
        null=True,
        blank=True,
    )
    #: ``HarvestedDocument.url_hash``. Not an FK: a document can be pruned from
    #: the corpus while its points are still in Qdrant, and this row is what
    #: the cleanup pass needs in order to delete them.
    document_id = models.CharField(max_length=64, blank=True, db_index=True)

    #: SHA-256 of the exact canonical text that was chunked. See the module
    #: docstring — this is the whole staleness test for the source side.
    content_hash = models.CharField(max_length=64, blank=True)
    char_count = models.PositiveIntegerField(default=0)
    chunk_count = models.PositiveIntegerField(default=0)

    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.INDEXED, db_index=True
    )
    last_error = models.TextField(blank=True)

    #: Which model produced the vectors, and under which chunking. Both are
    #: part of the staleness test; see ``IndexedSourceQuerySet.stale``.
    embed_model = models.CharField(max_length=64, blank=True)
    pipeline_version = models.PositiveSmallIntegerField(default=PIPELINE_VERSION)

    indexed_at = models.DateTimeField(default=timezone.now, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = IndexedSourceQuerySet.as_manager()

    class Meta:
        verbose_name = "indexed source"
        verbose_name_plural = "indexed sources"
        ordering = ["-indexed_at"]
        indexes = [
            models.Index(fields=["kind", "status"], name="ragidx_kind_status_idx"),
            models.Index(fields=["-indexed_at"], name="ragidx_indexed_at_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.source_key} ({self.status}, {self.chunk_count} chunks)"


class ChatConversation(models.Model):
    """One saved thread of questions and answers.

    The chat used to be stateless in both directions: nothing was sent to the
    model and nothing was kept, so every question stood alone. Keeping the
    thread is what makes a follow-up possible ("va uning muddati qachon?"), and
    the reason it is a table rather than browser storage is that a conversation
    the reader can only see on one device is not a record of anything.

    **Owned by a session first, by an account second.** The chat is public and
    unauthenticated — a judge opening the site can ask a question without
    registering — so a conversation belongs to a Django session key. When a
    vendor signs in, the conversations made under that session are adopted by
    the account (`adopt_session`), which is the only moment ownership changes.
    Neither field is a security boundary the product leans on: the content is a
    reader's own questions over published notices, and every endpoint filters
    on the session or the user rather than taking an id from the client.
    """

    #: Untitled until the first question names it. Held short deliberately —
    #: this is a sidebar row, not a summary.
    TITLE_LENGTH = 80

    #: A UUID rather than a sequence, because this id travels in URLs a reader
    #: can share and in a client's local storage. Ownership is still checked on
    #: every read — the unguessable id is defence in depth, not the defence.
    id = models.UUIDField(primary_key=True, default=uuid.uuid4, editable=False)

    session_key = models.CharField(max_length=40, db_index=True, blank=True)
    user = models.ForeignKey(
        settings.AUTH_USER_MODEL,
        null=True,
        blank=True,
        on_delete=models.CASCADE,
        related_name="chat_conversations",
    )
    title = models.CharField(max_length=TITLE_LENGTH, blank=True)
    #: The notice a thread was started from, when it was started from one. Kept
    #: on the conversation rather than re-sent per question so that reopening a
    #: thread reopens its scope — a follow-up in a tender's thread is still
    #: about that tender.
    notice_id = models.CharField(max_length=64, blank=True, db_index=True)

    created_at = models.DateTimeField(auto_now_add=True)
    #: Touched on every turn, so the sidebar orders by "last spoken to" rather
    #: than by when the thread was opened.
    updated_at = models.DateTimeField(auto_now=True, db_index=True)

    class Meta:
        verbose_name = "chat conversation"
        ordering = ["-updated_at"]
        indexes = [
            models.Index(fields=["session_key", "-updated_at"], name="ragchat_sess_idx"),
            models.Index(fields=["user", "-updated_at"], name="ragchat_user_idx"),
        ]

    def __str__(self) -> str:
        return self.title or f"Conversation {self.pk}"

    def touch(self) -> None:
        self.save(update_fields=["updated_at"])


class ChatMessage(models.Model):
    """One turn. A question, or an answer with the sources it was built from.

    **The sources are stored with the answer, not looked up again.** A stored
    claim whose passages are gone is exactly the unverifiable statement this
    product refuses to ship: the citation badge has to open the same sentence
    tomorrow that it opened when the answer was written, even after the index
    is rebuilt, the notice re-sanitised, or the chunk boundaries moved. So the
    payloads travel with the message. They are a snapshot by design — if the
    source has since changed, the honest record is what the answer was actually
    based on.

    ``unsupported`` is kept per message for the same reason it is shown live:
    a model that wrote a sentence it could not back is a fact about that
    answer, and a stored answer that quietly drops the count is a nicer-looking
    history of a worse product.
    """

    class Role(models.TextChoices):
        USER = "user", "User"
        ASSISTANT = "assistant", "Assistant"

    conversation = models.ForeignKey(
        ChatConversation, on_delete=models.CASCADE, related_name="messages"
    )
    role = models.CharField(max_length=16, choices=Role.choices)

    #: The question, for a user turn. For an assistant turn this is the claims
    #: joined into prose — written once here so that history sent to the model,
    #: and any later export, do not each re-derive it differently.
    text = models.TextField(blank=True)

    #: Assistant turns only: the claims exactly as validated, and the sources
    #: they may cite. `[{"text": ..., "sources": [0, 3]}]` and the search hits.
    claims = models.JSONField(default=list, blank=True)
    sources = models.JSONField(default=list, blank=True)

    retrieval = models.CharField(max_length=16, blank=True)
    degraded_reason = models.CharField(max_length=64, blank=True)
    unsupported = models.PositiveSmallIntegerField(default=0)
    took_ms = models.PositiveIntegerField(default=0)
    prompt_version = models.CharField(max_length=16, blank=True)

    #: How this answer was produced (D57/D60). Three columns rather than a JSON
    #: blob because they exist to be *aggregated*: "what fraction of answers
    #: came from the cache", "did the fast tier's `unsupported` rate move" and
    #: "which model wrote the answers a reader complained about" are all one
    #: ``GROUP BY`` away here and a JSON extraction away otherwise.
    #:
    #: Blank on every row written before this shipped, and blank on a cache hit
    #: for ``model`` — nothing wrote it. That is a distinction worth keeping:
    #: an empty model column means no model call happened, not that the call
    #: was not recorded.
    model = models.CharField(max_length=64, blank=True)
    route_tier = models.CharField(max_length=16, blank=True)
    cache_tier = models.CharField(max_length=16, blank=True)

    created_at = models.DateTimeField(auto_now_add=True, db_index=True)

    class Meta:
        verbose_name = "chat message"
        ordering = ["created_at", "id"]
        indexes = [
            models.Index(fields=["conversation", "created_at"], name="ragmsg_conv_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.role}: {self.text[:60]}"


#: Bump when the neighbour computation changes — a different representative
#: passage, a different filter, a different number of query chunks. Rows
#: written under an older version are ignored and recomputed, which is how a
#: change to the algorithm reaches readers without a migration that deletes.
#:
#: 1 — three distinctive chunks per notice (D45), awards-only filter (D48).
SIMILARITY_VERSION = 1


class SimilarAward(models.Model):
    """Award notices closest in meaning to one notice. Computed once, kept.

    **Why this table exists.** The panel used to run the whole similarity
    search on every request: reading a notice's chunks, counting each
    candidate's duplicates against the corpus, then three filtered searches —
    about fifteen Qdrant round trips to render a block at the foot of a page.
    It was correct and it was visibly slow, and the reader saw it as a panel
    that appeared a second after everything around it.

    Nothing about the answer needs to be recomputed per view. The notice's text
    does not change, the archive of finished contracts barely does, and the
    result is a short list of ids. So it is computed once — by the archive
    command, by the scheduled task for what the sync adds, or by the first
    reader who asks for a notice nobody has asked for yet — and read from here
    afterwards.

    **It stores candidates, not the panel.** Whether a neighbour is an award
    *with a named winner* is a fact about `ContractAward` rows that a reparse
    can change (D42a), so that join stays at read time and this table holds
    more rows than the panel shows. A winner appearing does not need this
    recomputed; it only needs the join to run again, which it does on every
    request.

    **Staleness is versioned, not guessed.** `algo_version` is what makes a
    change to the similarity method reach every reader at once. What it cannot
    catch is a *new award* becoming a better neighbour of an old notice: the
    scheduled task refreshes open tenders for that reason, and the archive is
    recomputed by hand when the method changes.
    """

    notice = models.ForeignKey(
        TenderNotice,
        on_delete=models.CASCADE,
        related_name="similar_awards",
        to_field="notice_id",
        db_column="notice_id",
    )
    #: The neighbour. A plain id rather than a second FK: the join that matters
    #: is to `ContractAward`, and a notice can be a neighbour before its award
    #: has been parsed.
    award_notice_id = models.CharField(max_length=64, db_index=True)

    #: Position in the computed list. Kept so the read path can order without
    #: sorting on a float it does not otherwise use.
    rank = models.PositiveSmallIntegerField(default=0)
    score = models.FloatField(default=0.0)
    #: The sentence the match was made on. Stored with the row because a client
    #: rendering a score without it puts back exactly the unaccountable ranking
    #: D42 removed — see `award_feed.similar_awards`.
    match_passage = models.TextField(blank=True)

    algo_version = models.PositiveSmallIntegerField(default=SIMILARITY_VERSION)
    computed_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        verbose_name = "similar award"
        ordering = ["rank"]
        constraints = [
            models.UniqueConstraint(
                fields=["notice", "award_notice_id"], name="ragsim_unique_pair"
            )
        ]
        indexes = [
            models.Index(fields=["notice", "rank"], name="ragsim_notice_rank_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.notice_id} ~ {self.award_notice_id} (#{self.rank})"
