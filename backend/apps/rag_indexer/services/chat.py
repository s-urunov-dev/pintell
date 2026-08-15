"""Answering a question in prose, with every sentence tied to a passage.

This is the one place in the product where a model writes text a user reads as
an answer, and the whole design is about making that safe in the same way the
rest of the system is safe: **no claim without a source**.

Three mechanisms, and none of them is the prompt asking nicely.

**The model is given a closed set and can only point into it.** Retrieval runs
first and produces N passages, numbered. The response schema makes every claim
carry ``sources``, and a source is an *index into that list* — so the model
cannot cite a document it was not shown, cannot invent a notice id, and cannot
produce a URL. Anything outside the range is not a bad citation to be judged
later; it is an index this module discards.

**A claim with no surviving source does not reach the reader.** It is counted
instead (``unsupported``), which turns "the model said something it could not
back" from an invisible event into a number on the response. That is the same
move the grounding verifier makes for extraction (D4), for the same reason:
the failure has to be measurable or it is not managed.

**The model never decides anything.** It writes; it does not evaluate a vendor,
compute a verdict or classify a notice. Compliance verdicts stay in
``apps.compliance.expressions``, arithmetic and reproducible by hand. A chat
answer is a reading aid over published text, and the citation is what makes it
checkable — which is why the front end renders each claim with a badge that
opens the passage in the document it came from.

The retrieval half is not reimplemented here: ``SearchService`` is called, so a
question asked in chat is answered out of the same index, with the same
filters, the same score floor and the same Postgres fallback as the search box.
When the fallback answers, the chat says so — a keyword-matched passage is a
weaker warrant than a semantic one and the reader is entitled to know.

**The pipeline in front of the model, in order** (D57–D60). Each stage can be
switched off by configuration, and none of them may change what a claim is
allowed to rest on:

1. **The semantic cache.** The question is embedded once; that vector looks for
   an answer to the same question, in the same scope, in its own collection. A
   hit returns the stored claims *with their stored sources* and makes no model
   call at all. The citation contract is untouched — the indices still point
   into the list the claims were written against, because the pair is stored
   and served as one value.
2. **Hybrid retrieval.** The same vector goes to ``SearchService`` with
   ``hybrid=True``, so a reference code the dense arm cannot see is found by
   the lexical arm and the two are fused by rank.
3. **Selection**, unchanged: near-verbatim template text and a notice's fourth
   passage are dropped, because those spend a slot rather than fill one.
4. **Reranking.** A cross-encoder reads each ``(question, passage)`` pair
   together and keeps the best few. Off by default — ``services/rerank.py``
   says what turning it on costs.
5. **Routing.** A lookup and an analysis are not the same job, and the model
   that answers them need not be the same model. Rules, not a classifier.
6. **Compression.** Markup and whitespace come out of what the model reads.
   The reader's copy is untouched, because the highlight is measured against it.

The model still writes; it still decides nothing.
"""

from __future__ import annotations

import json
import logging
import re
import time
from dataclasses import dataclass, field
from typing import Any

from django.conf import settings

from apps.tenders.services.ai.client import AIUnavailable, ai_enabled, get_client

from . import routing
from .cache import SemanticCache, get_semantic_cache
from .embedding import EmbeddingUnavailable, get_embedding_service
from .qdrant import SearchHit
from .rerank import RerankService, get_rerank_service
from .search import SearchService, get_search_service

logger = logging.getLogger(__name__)

#: Bumped whenever the prompt or the schema changes, so an answer can be traced
#: to the instructions that produced it. Same convention as
#: ``compliance.llm.PROMPT_VERSION``.
CHAT_PROMPT_VERSION = "v6"

#: How much wider retrieval is asked than the model will be shown. The extra
#: is what ``_select`` spends on the two filters below: without it, discarding
#: a near-duplicate would mean answering from fewer passages rather than from
#: a different one.
SELECTION_OVERFETCH = 2

#: Passages one notice may contribute to an unscoped answer.
#:
#: Measured, not chosen: on "IT tenderlarda qanday aylanma talab qilinadi?" the
#: top sixteen passages came from thirteen notices, one of them holding three
#: slots. A question about tenders in general is answered from the range across
#: them, so a notice that says the same thing three times is spending context
#: on one data point. **Not applied when the question is scoped to one tender**
#: — there the cap would be a cap on the answer itself.
MAX_PER_NOTICE = 3

#: Word overlap above which two passages are treated as the same passage.
#:
#: The archive is full of near-verbatim template paragraphs: three notices
#: opened the same answer above with a byte-identical "The bidder shall
#: confirm: - availability of certified specialists…", and two more repeated a
#: national-standards paragraph word for word. They read to the model as five
#: independent sources and to the reader as one. Deliberately high — this
#: catches copied text, not two tenders that happen to both be about roads.
DUPLICATE_OVERLAP = 0.9

#: Distinct words a passage needs before it can be called a copy of another.
#:
#: Overlap on a short passage says almost nothing: "Requirement 1 of this
#: tender" and "Requirement 7 of this tender" share every word they have, and
#: the figures that distinguish them are exactly what the comparison drops.
#: Below this the passage is kept — a duplicate slipping through costs one
#: slot, and a wrongly discarded passage costs a source the reader could have
#: been given.
MIN_WORDS_FOR_DUPLICATE = 8

_WORD_RE = re.compile(r"[^\W\d_]{3,}", re.UNICODE)

#: Language names, keyed by the UI's own codes.
#:
#: **The question's language decides, and the interface language only breaks a
#: tie.** This is the reverse of how it started. The interface was chosen first
#: on the argument that detection on a three-word query is a coin toss — true,
#: but the coin is not this module's to flip: the model is already reading the
#: question and is far better at recognising Uzbek than any heuristic here
#: would be. What it needed was to be *told* to mirror, which v3 did not say.
LANGUAGE_NAMES = {
    "uz": "Uzbek (latin script)",
    "ru": "Russian",
    "en": "English",
}

SYSTEM_PROMPT = """You answer questions about World Bank procurement notices \
using the numbered passages provided, and the tender facts given as context.

Rules, in order of importance:

1. Every claim you make must be supported by at least one passage, cited by \
its number. Never state a requirement, threshold, date, amount or eligibility \
rule that is not written in a passage. Do not infer one from general knowledge \
of World Bank procurement — the passages are the only source.
2. Answer in the language the QUESTION is written in — Uzbek, Russian or \
English. This is absolute and it overrides everything else about language: if \
the question is in Uzbek, every word of every claim is in Uzbek, whatever \
language the sources are written in. If the question's language is genuinely \
unclear, use the interface language named at the end of the request. Names of \
organisations, figures and currency codes stay as the source writes them.
3. Do not evaluate whether any company qualifies. You report what a tender \
says; whether a vendor meets it is decided elsewhere, by arithmetic.
4. Be useful rather than literal. If the question is broader than the \
document passages — "show me today's open tenders", "what is available in \
Uzbekistan" — do not answer that you have no information. Some of the \
numbered sources are database records rather than document passages; use \
them, cite them by number exactly as you would a passage, and say plainly \
that a full list is on the search page.
5. Sources are marked "open" or "closed". A CLOSED tender is history: you may \
use it to establish what such tenders typically require, and you must never \
present it as something the reader can bid on. If the reader is asking what is \
available, answer from the open ones and from the database records.
6. **Tell a general question from a specific one, and answer accordingly.**
   - A question about ONE tender ("what does this tender require?") is \
answered with that tender's own figures.
   - A question about tenders IN GENERAL ("what turnover do IT tenders \
want?") must NOT be answered by listing several notices' individual \
requirements as though they were a rule. Say that the requirement is set per \
notice and scales with the contract, give the range the sources actually show, \
and say where a reader checks a specific one — the notice's own qualification \
criteria or Terms of Reference. Cite the sources the range came from.
7. Write each claim as one self-contained sentence. Short and specific beats \
complete. Quote figures exactly as the source writes them, with the currency.
8. Earlier turns of the conversation may be shown before the sources. They are \
there so you can tell what the question refers to — "and when does it close?" \
means the tender the reader was just asking about. They are NOT sources: every \
claim you write must still cite a numbered source from THIS request. Never \
cite a number that appeared in an earlier answer, and never repeat a figure \
from an earlier answer unless a numbered source in front of you says it."""


@dataclass
class ChatAnswer:
    """What one question produced: claims, their sources, and what was dropped."""

    claims: list[dict[str, Any]] = field(default_factory=list)
    sources: list[SearchHit] = field(default_factory=list)
    retrieval: str = "none"
    degraded_reason: str = ""
    #: Claims the model produced that cited nothing valid, and were removed.
    #: Reported rather than hidden — see the module docstring.
    unsupported: int = 0
    took_ms: int = 0
    #: How this answer was produced, when it was not produced the usual way.
    #: ``cache`` carries the tier and the similarity of the question that was
    #: matched; ``route`` carries the model and the rule that chose it. Both
    #: are on the response for the same reason ``unsupported`` is: an
    #: optimisation nobody can see is an optimisation nobody can measure, and
    #: the whole claim of these two is that they are cheaper without being
    #: worse.
    cache: dict[str, Any] | None = None
    route: dict[str, Any] | None = None
    #: Passages a reranker discarded. Zero when no reranker ran, which is not
    #: the same as zero discarded and is why the field sits beside ``route``
    #: rather than being inferred from the source count.
    reranked_out: int = 0

    def as_dict(self) -> dict[str, Any]:
        return {
            "claims": self.claims,
            "sources": [hit.as_dict() for hit in self.sources],
            "retrieval": self.retrieval,
            "degraded_reason": self.degraded_reason,
            "unsupported": self.unsupported,
            "took_ms": self.took_ms,
            "prompt_version": CHAT_PROMPT_VERSION,
            "cache": self.cache,
            "route": self.route,
            "reranked_out": self.reranked_out,
        }


#: The answer's shape, enforced by the API rather than requested in prose.
#: ``sources`` has ``minItems: 1`` so a claim without a citation is not
#: something the model can return at all.
ANSWER_SCHEMA: dict[str, Any] = {
    "type": "object",
    "properties": {
        "claims": {
            "type": "array",
            "items": {
                "type": "object",
                "properties": {
                    "text": {"type": "string"},
                    "sources": {
                        "type": "array",
                        "items": {"type": "integer"},
                        "minItems": 1,
                    },
                },
                "required": ["text", "sources"],
                "additionalProperties": False,
            },
        }
    },
    "required": ["claims"],
    "additionalProperties": False,
}


class ChatService:
    """Retrieve, then ask a model to write from what was retrieved."""

    def __init__(
        self,
        search: SearchService | None = None,
        client: Any = None,
        *,
        cache: SemanticCache | None = None,
        reranker: RerankService | None = None,
        embedding: Any = None,
    ):
        self.search = search or get_search_service()
        self._client = client
        # Injected rather than reached for, so a test can hand in a cache that
        # never hits and a reranker that never runs without patching module
        # globals — and so the pipeline's stages stay swappable one at a time.
        self.cache = cache or get_semantic_cache()
        self.reranker = reranker or get_rerank_service()
        self.embedding = embedding or get_embedding_service()

    @staticmethod
    def enabled() -> bool:
        """Whether a model can answer at all. Retrieval works without one."""
        return ai_enabled()

    def ask(
        self,
        question: str,
        *,
        notice_id: str = "",
        category: str = "",
        language: str = "en",
        limit: int | None = None,
        history: list[dict[str, str]] | None = None,
        query: str = "",
        on_stage: Any = None,
    ) -> ChatAnswer:
        """Answer ``question`` from the archive, or explain why there is none.

        Retrieval failing and the model failing are different outcomes and the
        response distinguishes them. With no passages there is nothing to
        answer from and no call is made — asking a model to write anyway is
        precisely how a system starts inventing procurement facts. With
        passages but no API key the passages are still returned: the reader
        gets the sentences without the summary, which is the honest
        degradation and is more useful than an error.
        """
        limit = limit or settings.RAG["CHAT_PASSAGES"]
        started = time.monotonic()

        # **The question is embedded once, here.** The cache looks itself up
        # with that vector and, on a miss, retrieval searches with the same
        # one. Embedding it twice would double the only cost a miss cannot
        # avoid — and it is a *query* embedding, which is the only kind either
        # consumer may use (see `EmbeddingService`).
        searched = query or question
        vector = self._embed(searched)

        # A follow-up is not cacheable in either direction. Its own words do
        # not identify it — "va uning muddati qachon?" matches every deadline
        # question in the archive — so it must not be looked up by them, and
        # the answer to it is about a subject that lives in the thread rather
        # than in the question. `conversations.retrieval_query` already made
        # that call; rewriting the query is how it says so.
        standalone = searched == question
        scope = SemanticCache.scope_key(
            language=language, notice_id=notice_id, category=category
        )

        if standalone:
            cached = self.cache.lookup(question, scope=scope, vector=vector)
            if cached is not None:
                _stage(on_stage, "cached", **cached.as_dict())
                answer = ChatAnswer(
                    claims=cached.claims,
                    sources=cached.sources,
                    retrieval="cache",
                    took_ms=_elapsed(started),
                    cache=cached.as_dict(),
                )
                # The sources go out before the claims here too, so the client
                # draws a cached answer through exactly the path it draws a
                # fresh one through.
                _stage(on_stage, "sources", sources=[hit.as_dict() for hit in answer.sources])
                for index, claim in enumerate(answer.claims):
                    _stage(on_stage, "claim", index=index, claim=claim)
                return answer

        # `on_stage` is how the streaming endpoint reports what is happening
        # while it happens. It is called with a name and a fact, never with a
        # fabricated delay: the stages a reader watches are the stages this
        # method actually goes through, which is why the hook lives here rather
        # than in the view timing them from outside.
        _stage(on_stage, "retrieving")
        found = self.search.search(
            # The embedded text, which is the question unless the caller found
            # it to be a fragment of the previous one — see
            # `conversations.retrieval_query`. The *question* below is still
            # what the model is asked; only what was searched for differs.
            query or question,
            # Wider than the answer needs, because `_select` throws some of it
            # away. Retrieval's own contract is "the top N by score"; deciding
            # that a fourth passage from one notice is worth less than a first
            # from another is a judgement about *this* context window, and it
            # is made here, where the tokens are paid for.
            limit=limit * SELECTION_OVERFETCH,
            notice_id=notice_id,
            category=category,
            # Scoped to one tender, the reader means *that* tender and it may
            # well be closed — an award notice reached from the feed is the
            # ordinary case. Unscoped, the question is about what can be bid
            # on, and the archive is overwhelmingly history: without this a
            # question about IT turnover is answered out of contracts that
            # closed in 2011, which reads as current guidance.
            active_only=not notice_id,
            # Both arms, fused by rank (D58). The chat is where this matters
            # most: a reader who types `TRIP-CS-01` means that notice, and no
            # amount of semantic similarity finds an identifier.
            hybrid=True,
            vector=vector,
        )
        passages = _select(found.hits, limit=limit, cap_per_notice=not notice_id)

        # A second, sharper read of what survived selection (D59). Off unless a
        # backend is configured, in which case this is where the candidate list
        # becomes the short one the model is actually shown.
        before_rerank = len(passages)
        passages = self.reranker.rerank(question, passages)
        reranked_out = max(before_rerank - len(passages), 0)
        if reranked_out:
            _stage(on_stage, "reranked", kept=len(passages), dropped=reranked_out)

        # Records are appended *after* the passages so the indices the model
        # sees are stable across both kinds, and validated by the same range
        # check. A claim about which tenders are open now can therefore cite
        # the row it came from instead of borrowing a passage that does not
        # say it — which is what prompt v2 did, and it made the badge open a
        # sentence that supported nothing.
        #
        # They are appended *after the rerank* as well, and that is not an
        # ordering detail: a database row is not a passage, it was not
        # retrieved by similarity, and handing it to a cross-encoder to be
        # scored against the question would let a relevance model discard the
        # only source that can answer "what is open right now".
        sources = passages + record_sources(notice_id)
        answer = ChatAnswer(
            sources=sources,
            retrieval=found.retrieval,
            degraded_reason=found.degraded_reason,
            took_ms=found.took_ms,
            reranked_out=reranked_out,
        )
        if not sources:
            _stage(on_stage, "empty")
            return answer

        if not self.enabled():
            answer.degraded_reason = answer.degraded_reason or "model_unavailable"
            _stage(on_stage, "degraded", reason=answer.degraded_reason)
            return answer

        _stage(on_stage, "reading", sources=len(sources))
        # The passages themselves, before the model has written a word. Sent
        # early on purpose: a claim that arrives while the reader has no source
        # list can only be drawn without its citation badges, and the badges
        # appearing afterwards is the answer visibly re-rendering under them.
        # With the list already in hand, a streamed sentence is drawn once.
        _stage(on_stage, "sources", sources=[hit.as_dict() for hit in sources])

        # Which model, decided from the question's own words (D60). Reported on
        # the answer whether or not a fast tier is configured, so "everything
        # went deep" is a reading of the data rather than an absence in it.
        chosen = routing.route(
            question, notice_id=notice_id, history=len(history or [])
        )
        answer.route = chosen.as_dict()
        _stage(on_stage, "routing", **chosen.as_dict())

        try:
            claims = self._write(
                question,
                sources,
                language=language,
                history=history or [],
                on_stage=on_stage,
                route=chosen,
            )
        except AIUnavailable as exc:
            logger.info("Chat model unavailable: %s", exc)
            answer.degraded_reason = "model_unavailable"
            _stage(on_stage, "degraded", reason=answer.degraded_reason)
            return answer
        except Exception as exc:  # noqa: BLE001 - an answer is never a 500
            logger.warning("Chat model failed: %s", exc)
            answer.degraded_reason = "model_failed"
            _stage(on_stage, "degraded", reason=answer.degraded_reason)
            return answer

        answer.claims, answer.unsupported = self._validate(claims, len(sources))

        # Kept only if it is worth keeping. A degraded answer records the state
        # of the deployment at one instant and would be served for hours after
        # that state cleared; an answer resting on a database row is about
        # *now* and `SemanticCache` refuses it on its own.
        if standalone and answer.claims and not answer.degraded_reason:
            self.cache.store(
                question,
                scope=scope,
                vector=vector,
                claims=answer.claims,
                sources=answer.sources,
            )
        return answer

    def _embed(self, text: str) -> list[float] | None:
        """The query's vector, or ``None`` when there is no embedder.

        ``None`` rather than an exception, because both consumers degrade
        rather than fail: the cache falls back to its exact tier and retrieval
        raises its own ``EmbeddingUnavailable`` on the way to the Postgres
        path. Swallowing it here would hide that from *retrieval*, so the call
        below re-raises nothing — it simply passes no vector, and
        `SearchService` embeds and fails exactly as it did before.
        """
        try:
            return self.embedding.embed_query(text)
        except EmbeddingUnavailable as exc:
            logger.info("No query embedding for this question: %s", exc)
            return None
        except Exception as exc:  # noqa: BLE001 - never fatal
            logger.warning("Query embedding failed: %s", exc)
            return None

    # -- the model ----------------------------------------------------------
    def _write(
        self,
        question: str,
        hits: list[SearchHit],
        *,
        language: str = "en",
        history: list[dict[str, str]] | None = None,
        on_stage: Any = None,
        route: routing.Route | None = None,
    ) -> list[dict[str, Any]]:
        config = settings.ANTHROPIC
        client = self._client or get_client()
        target = LANGUAGE_NAMES.get(language, LANGUAGE_NAMES["en"])
        # A caller that passed no route gets what the chat always used. The
        # router is the only thing that decides otherwise, and it is called
        # from `ask` — never from here, so this method stays one thing.
        chosen = route or routing.Route(
            tier="deep",
            model=config["CHAT_MODEL"] or config["MODEL"],
            effort=config["CHAT_EFFORT"],
            reason="unrouted",
        )

        # Emitted here rather than in `ask` so it marks the moment the request
        # actually goes out. This is where the seconds are: everything above is
        # milliseconds of prompt assembly, and a reader watching "writing…" is
        # watching the model write.
        _stage(on_stage, "writing")

        request = dict(
            # **The chat's model is set apart from extraction's** (`CHAT_MODEL`
            # defaults to `MODEL`, so a deployment that sets neither is
            # unchanged). The two jobs are not the same job: extraction fills a
            # schema from an English document and runs offline over thousands
            # of notices, where the cheapest tier that passes the gold set is
            # the right answer; the chat writes prose a vendor reads, in Uzbek
            # or Russian, from passages written in English. The deployed server
            # ran both on Haiku and the Uzbek came back ungrammatical —
            # "zarorat", "savdo-sotuvchi shartlari" — which is a quality the
            # citation schema cannot check and no prompt can fix.
            model=chosen.model,
            # Room for one claim per source and then some. Raised with the
            # passage count: sixteen sources can support more claims than eight,
            # and a truncated answer loses whichever claim was being written.
            max_tokens=4000,
            system=[
                {
                    "type": "text",
                    "text": SYSTEM_PROMPT,
                    # The prompt is byte-identical across every question, so
                    # the breakpoint means each call after the first reads the
                    # prefix instead of paying for it.
                    "cache_control": {"type": "ephemeral"},
                }
            ],
            output_config={
                "format": {"type": "json_schema", "schema": ANSWER_SCHEMA},
                # Set explicitly rather than left to the default. A reader is
                # waiting on this call, and the default spends its time on
                # reasoning this task does not have: the passages are chosen,
                # the schema is fixed, and the job is to read and report.
                #
                # Per tier now (D60): a question that asks for a contact does
                # not need the budget a question that asks for a range across
                # notices does, and effort is the cheaper of the two levers.
                "effort": chosen.effort,
            },
            messages=[
                # The thread so far, so a fragment of a question has a subject.
                # Replayed as plain turns and **without their citation
                # numbers**: an index from turn three names a passage in turn
                # three's list, and putting it beside turn five's list is an
                # invitation to reuse a number that now means something else.
                *_prior_turns(history or []),
                {
                    "role": "user",
                    # Order matters: context first so the model reads what the
                    # reader is looking at before the retrieved material, then
                    # the passages, then the question and the language. The
                    # language instruction sits last because it is the one the
                    # model most often drops, and last is where it is read.
                    "content": (
                        f"{_passages(hits)}\n\n"
                        f"Question: {question}\n\n"
                        f"Answer in the language of the question above. "
                        f"If that is unclear, answer in {target}."
                    ),
                }
            ],
        )

        text, refused = self._collect(client, request, on_stage, len(hits))
        if refused or not text.strip():
            # A refusal is an answer — an empty one — not something to retry.
            return []
        try:
            return json.loads(text).get("claims", [])
        except json.JSONDecodeError:
            # A truncated body is a failed answer, not a crash. The caller
            # records `model_failed` and the reader still gets the passages.
            logger.warning("Chat model returned unparseable JSON (%d chars)", len(text))
            return []

    @staticmethod
    def _collect(
        client: Any, request: dict[str, Any], on_stage: Any, available: int
    ) -> tuple[str, bool]:
        """The model's JSON body, reported claim by claim while it is written.

        **Why stream a response that is validated whole.** The answer is one
        JSON object, so nothing can be *trusted* until it closes — but a reader
        waiting forty seconds for a dozen sentences has no way to tell a slow
        answer from a broken one. So each claim is announced the moment the
        model finishes writing it, having passed the same index check the final
        answer passes: what appears early is never something that would later
        be withdrawn.

        The stream is used only if the client has one. The plain call remains
        the fallback, and the parsed result is identical either way — which is
        what keeps the tests that use a simple client honest about the real
        thing.
        """
        stream = getattr(client.messages, "stream", None)
        if stream is None or on_stage is None:
            response = client.messages.create(**request)
            refused = getattr(response, "stop_reason", "") == "refusal"
            text = "".join(
                block.text
                for block in getattr(response, "content", [])
                if getattr(block, "type", None) == "text"
            )
            return text, refused

        buffer = ""
        announced = 0
        drafted = ""
        with stream(**request) as live:
            for chunk in live.text_stream:
                buffer += chunk

                # The sentence currently being written, as far as it has been
                # written. This is what makes the answer arrive word by word
                # rather than a paragraph at a time: the model emits tokens,
                # and the text of the claim in progress grows with them.
                #
                # It is a *draft* and it is labelled as one all the way to the
                # screen — it carries no citation, because the numbers are not
                # written yet, and it is replaced by the checked claim the
                # moment the object closes. Nothing uncited is ever presented
                # as finished.
                partial = _drafting(buffer, announced)
                if partial and partial != drafted:
                    drafted = partial
                    _stage(on_stage, "draft", index=announced, text=partial)

                for claim in _complete_claims(buffer)[announced:]:
                    kept, _dropped = ChatService._validate([claim], available)
                    announced += 1
                    drafted = ""
                    if kept:
                        _stage(on_stage, "claim", index=announced - 1, claim=kept[0])
            final = live.get_final_message()

        refused = getattr(final, "stop_reason", "") == "refusal"
        return buffer, refused

    @staticmethod
    def _validate(
        claims: list[dict[str, Any]], available: int
    ) -> tuple[list[dict[str, Any]], int]:
        """Keep the claims whose citations point at passages that exist.

        Indices are checked rather than trusted, and out-of-range ones are
        dropped from the claim instead of clamped: a citation clamped to the
        nearest valid passage would point at a sentence that does not support
        the claim, which is worse than no citation because it looks like one.
        A claim left with nothing is removed and counted.
        """
        kept: list[dict[str, Any]] = []
        unsupported = 0
        for claim in claims:
            text = str(claim.get("text", "")).strip()
            sources = sorted(
                {
                    index
                    for index in claim.get("sources", [])
                    if isinstance(index, int) and 0 <= index < available
                }
            )
            if not text:
                continue
            if not sources:
                unsupported += 1
                continue
            kept.append({"text": text, "sources": sources})
        return kept, unsupported


#: One decoder, reused. `raw_decode` is what makes partial parsing possible:
#: it reads one value and reports where it stopped, so a half-written object at
#: the end of the buffer ends the scan instead of failing the whole parse.
_DECODER = json.JSONDecoder()


def _complete_claims(buffer: str) -> list[dict[str, Any]]:
    """The claims that are fully written in a partially received JSON body.

    Deliberately conservative: the scan stops at the first object it cannot
    decode, so a claim is only ever reported once its closing brace has
    arrived. A sentence shown to a reader and then rewritten would be worse
    than a slower answer — this is a progress report, not a draft.
    """
    marker = buffer.find('"claims"')
    if marker == -1:
        return []
    start = buffer.find("[", marker)
    if start == -1:
        return []

    claims: list[dict[str, Any]] = []
    index = start + 1
    length = len(buffer)
    while index < length:
        while index < length and buffer[index] in " \t\r\n,":
            index += 1
        if index >= length or buffer[index] != "{":
            break
        try:
            value, index = _DECODER.raw_decode(buffer, index)
        except json.JSONDecodeError:
            break
        if isinstance(value, dict):
            claims.append(value)
    return claims


#: Where a claim object's text lives, for the scanner below.
_TEXT_KEY = '"text"'


def _drafting(buffer: str, finished: int) -> str:
    """The text of the claim being written, as far as it has arrived.

    Reads the *last* `"text"` value in the buffer and returns it only while its
    closing quote is still missing — once the quote lands the claim is about to
    close and `_complete_claims` will report it properly, with its citations.

    Escapes are handled by decoding the fragment as a JSON string with a quote
    appended, and a fragment that ends mid-escape (`\\u00e` on a character
    boundary) simply fails to decode and waits for the next chunk. Guessing at
    a half-written escape would put a stray backslash in front of a reader.
    """
    start = buffer.rfind(_TEXT_KEY)
    if start == -1:
        return ""
    quote = buffer.find('"', start + len(_TEXT_KEY) + 1)
    if quote == -1:
        return ""

    index = quote + 1
    length = len(buffer)
    while index < length:
        char = buffer[index]
        if char == "\\":
            index += 2
            continue
        if char == '"':
            # Closed: this claim is finished and is not a draft any more.
            return ""
        index += 1

    fragment = buffer[quote + 1 :]
    try:
        return json.loads(f'"{fragment}"')
    except json.JSONDecodeError:
        return ""


def _prior_turns(history: list[dict[str, str]]) -> list[dict[str, str]]:
    """Earlier turns, in the API's message shape.

    Roles other than user/assistant are dropped rather than mapped: this list
    comes from a table, and a row with an unexpected role is a bug to notice,
    not a turn to guess the meaning of.
    """
    turns: list[dict[str, str]] = []
    for turn in history:
        role = turn.get("role")
        text = (turn.get("text") or "").strip()
        if role in {"user", "assistant"} and text:
            turns.append({"role": role, "content": text})
    return turns


def _elapsed(started: float) -> int:
    """Milliseconds since ``started``. The cache path has no search to time."""
    return int((time.monotonic() - started) * 1000)


def _stage(hook: Any, name: str, **facts: Any) -> None:
    """Report a pipeline stage to whoever is watching, if anyone is.

    Never fatal: a client that disconnected mid-answer must not take the answer
    down with it — the turn is still recorded, and the reader finds it in the
    thread when they come back.
    """
    if hook is None:
        return
    try:
        hook({"stage": name, **facts})
    except Exception as exc:  # noqa: BLE001
        logger.debug("Stage listener failed on %s: %s", name, exc)


def _select(
    hits: list[SearchHit], *, limit: int, cap_per_notice: bool
) -> list[SearchHit]:
    """The passages worth spending the context window on, best first.

    Retrieval returns the top matches by score, and on this corpus the top
    matches are not the same thing as the most material. Two effects were
    measured on the deployed archive and both waste slots that a reader is
    paying for:

    * **Near-verbatim template text ranks together.** A question about IT
      qualification returned the same "The bidder shall confirm: - availability
      of certified specialists…" paragraph from three different notices, in
      consecutive positions. Three sources, one sentence.
    * **One notice can take several slots.** A tender that says "turnover" in
      four paragraphs answers a question about the *range* across tenders once,
      not four times.

    So this keeps the relevance order and drops what repeats: a passage whose
    words are already covered, and a notice's fourth passage. Nothing is
    re-ranked — a score this module invented would be exactly the unaccountable
    ordering the rest of the product refuses (D42/D45).

    **Answering short is allowed.** If the filters leave fewer than ``limit``,
    the model is shown fewer rather than topped up with a passage that was
    discarded for a reason. Sources are what a claim can cite, and a padded
    list is a wider licence to cite, not a better answer.
    """
    kept: list[SearchHit] = []
    kept_words: list[set[str]] = []
    per_notice: dict[str, int] = {}

    for candidate in hits:
        if len(kept) >= limit:
            break
        notice_id = candidate.payload.get("notice_id", "")
        if cap_per_notice and notice_id:
            if per_notice.get(notice_id, 0) >= MAX_PER_NOTICE:
                continue
        words = _words(candidate.payload.get("content", ""))
        if len(words) >= MIN_WORDS_FOR_DUPLICATE and any(
            _overlap(words, seen) >= DUPLICATE_OVERLAP for seen in kept_words
        ):
            continue
        kept.append(candidate)
        kept_words.append(words)
        if notice_id:
            per_notice[notice_id] = per_notice.get(notice_id, 0) + 1

    return kept


def _words(text: str) -> set[str]:
    """The words a passage is compared on: folded, no digits, no short tokens.

    Digits are excluded deliberately. Two copies of one template paragraph
    differ exactly in the figures filled into it, and those are the characters
    that would make them look like different passages.
    """
    return set(_WORD_RE.findall((text or "").casefold()))


def _overlap(left: set[str], right: set[str]) -> float:
    """Jaccard similarity of two word sets — 1.0 is the same wording.

    Symmetric on purpose: containment would call a two-line passage a duplicate
    of the long one that happens to include its words, and a short passage is
    often the one that says the thing plainly.
    """
    if not left or not right:
        return 0.0
    return len(left & right) / len(left | right)


#: Open tenders listed in the context block. Enough that "what is open right
#: now" has a real answer; short enough that the list does not become the
#: answer to every question.
OPEN_TENDER_SAMPLE = 8


def record_sources(notice_id: str = "") -> list[SearchHit]:
    """Facts from our own tables, as sources a claim may cite.

    Retrieval alone cannot answer "show me today's open tenders": that is a
    question about *rows*, not about passages, and a semantic search for it
    returns eight paragraphs about submission deadlines. The first version of
    this put the rows in an uncitable context block, and the result was worse
    than useless — the model answered correctly and then cited whichever
    passage was nearest, so the badge opened a sentence that supported nothing.
    In a product whose entire claim is that every statement is traceable, that
    is the one failure mode not worth trading anything for.

    So the rows are sources. They carry ``source_type: "record"``, which the
    front end renders as a link to the tender rather than a passage viewer,
    because there is no document to open: **the citation is the row**. The
    content is columns copied verbatim — titles, countries, deadlines — so
    nothing here can be a fabricated procurement fact either.

    Bounded by ``in_country_group().bidding_open()``, the same two predicates
    the public feed uses, so the count in a chat reply and the count on the
    tender list cannot drift apart.
    """
    from apps.tenders.models import TenderNotice  # noqa: PLC0415 - avoids a cycle

    records: list[SearchHit] = []

    # Both lookups are guarded, not just the second. Records are optional by
    # contract — "never fatal" — and the notice read is exactly as able to fail
    # as the open-tender count beneath it.
    try:
        notice = TenderNotice.objects.filter(pk=notice_id).first() if notice_id else None
    except Exception as exc:  # noqa: BLE001
        logger.info("Could not read the current notice for chat: %s", exc)
        notice = None

    if notice is not None:
        records.append(
            _record(
                notice.notice_id,
                "The tender the reader currently has open",
                f"{notice.bid_description or notice.project_name}. "
                f"Country: {notice.country}. Type: {notice.notice_type}. "
                f"Deadline: {notice.deadline_at or notice.deadline_date or 'not published'}. "
                f"Still open for bids: {'yes' if notice.is_open else 'no'}.",
            )
        )

    try:
        live = TenderNotice.objects.in_country_group().bidding_open()
        total = live.count()
        rows = list(
            live.order_by("deadline_at").values_list(
                "notice_id", "bid_description", "country", "deadline_at"
            )[:OPEN_TENDER_SAMPLE]
        )
    except Exception as exc:  # noqa: BLE001 - records are optional, never fatal
        logger.info("Could not read open tenders for chat: %s", exc)
        return records

    if total:
        listed = "\n".join(
            f"- {row[0]}: {(row[1] or '')[:90]} ({row[2]}, deadline {row[3]})"
            for row in rows
        )
        records.append(
            _record(
                "",
                "Tenders open for bids right now",
                f"{total} tenders are open for bids. The {len(rows)} closing "
                f"soonest:\n{listed}\n"
                "A full, filterable list is on the site's search page (/search).",
            )
        )
    return records


def _record(notice_id: str, title: str, content: str) -> SearchHit:
    """One database fact in the shape a citation reads.

    ``score`` is 1.0 and means nothing — a row is not retrieved by similarity,
    it is looked up — but the field exists on every source and a null would
    have every consumer branching on it. ``retrieval`` says ``record`` so no
    caller mistakes this for something the index found.
    """
    return SearchHit(
        score=1.0,
        retrieval="record",
        payload={
            "content": content,
            "notice_id": notice_id,
            "title": title,
            "source_key": f"record:{notice_id or 'open-tenders'}",
            "source_type": "record",
            "position_id": "",
            "category": "",
            "subcategory": "",
            "document_id": "",
        },
    )


def _passages(hits: list[SearchHit]) -> str:
    """The retrieved passages, numbered, as the model's only material.

    The index is the citation token, so it is printed exactly as the schema
    expects it to come back: zero-based, one passage per block. The tender is
    named beside each one because a question about "this tender" needs the
    model to be able to tell two of them apart — but the id is context, never
    something to quote, and the prompt says the passages are the only source.

    **The content is compressed here and nowhere else** (D60). Markup, control
    characters and runs of whitespace come out; words, figures and currency
    codes do not. This is the last point before the request goes out, which is
    exactly where it belongs: the stored payload is what the front end renders
    and what the highlight's offsets are measured against, so a compressor
    reaching either would move a yellow box off its sentence.
    """
    blocks = []
    for index, hit in enumerate(hits):
        payload = hit.payload
        label = payload.get("title") or payload.get("notice_id", "")
        # Records are labelled as such so the model can tell a paragraph of a
        # borrower's document from a row of ours, and weigh them accordingly.
        kind = "record" if payload.get("source_type") == "record" else "passage"
        where = payload.get("notice_id") or "the tender database"
        # Open or closed, stated per source rather than left to the model to
        # infer from a date it may not have been shown. A closed tender is
        # legitimate material for a pattern and never an opportunity.
        state = ""
        if "tender_open" in payload:
            state = " — OPEN for bids" if payload["tender_open"] else " — CLOSED, archive"
        content = routing.compress(
            str(payload.get("content", "")),
            # The title is printed on the header line above, so a passage that
            # opens by repeating it is printed twice. Handing it here is what
            # lets the compressor drop the second copy.
            title=str(payload.get("title") or ""),
        )
        blocks.append(f"[{index}] ({kind} — {where}{state} — {label})\n{content}")
    return "<sources>\n" + "\n\n".join(blocks) + "\n</sources>"


_service: ChatService | None = None


def get_chat_service() -> ChatService:
    global _service
    if _service is None:
        _service = ChatService()
    return _service


def reset_chat_service() -> None:
    global _service
    _service = None
