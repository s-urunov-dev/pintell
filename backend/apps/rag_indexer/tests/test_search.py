"""What the search endpoint does when the vector half is not there.

The fallback is the half most likely to be running on any given day — a
deployment that has not run the archive import has no vectors at all — and it
is the half nobody watches. So it gets the tests: that it engages, that it says
it engaged, that it still returns something a viewer can highlight, and that it
never quietly merges its scores with the vector path's.

Fakes rather than mocks of the client libraries. What is being tested is this
app's behaviour when a dependency is unavailable, and a fake that raises the
declared exception exercises exactly that without pinning the tests to the
shape of somebody else's SDK.
"""

from __future__ import annotations

from django.conf import settings
from django.test import TestCase, override_settings

from apps.rag_indexer.services.embedding import EmbeddingUnavailable
from apps.rag_indexer.services.qdrant import QdrantUnavailable, SearchHit
from apps.rag_indexer.services.search import SearchService
from apps.tenders.models import TenderNotice

BODY = (
    "<p>The bidder shall demonstrate an average annual construction turnover "
    "of USD 22.4 million over the last three years.</p>"
    "<p>Bids must remain valid for a period of 120 days after the deadline.</p>"
)


class FakeEmbedding:
    """An embedding service that is present, absent, or broken — on demand."""

    model = "fake-embed"
    dimensions = 4

    def __init__(self, *, fails: Exception | None = None):
        self.fails = fails
        self.calls: list[str] = []

    def embed_query(self, text: str) -> list[float]:
        self.calls.append(text)
        if self.fails:
            raise self.fails
        return [0.1, 0.2, 0.3, 0.4]

    @staticmethod
    def enabled() -> bool:
        return True


class FakeStore:
    """A Qdrant that answers, answers nothing, or is down."""

    collection = "test_chunks"

    def __init__(self, *, hits: list[SearchHit] | None = None, fails: Exception | None = None):
        self.hits = hits or []
        self.fails = fails
        self.filters: list[object] = []
        self.limits: list[int] = []

    def build_filter(self, **conditions):
        kept = {k: v for k, v in conditions.items() if v not in ("", None, [])}
        self.filters.append(kept)
        return kept or None

    def search(self, vector, *, limit, query_filter=None, score_threshold=None):
        self.limits.append(limit)
        if self.fails:
            raise self.fails
        return self.hits[:limit]


class FallbackEngages(TestCase):
    """Every way the vector path can fail ends in a full-text answer."""

    @classmethod
    def setUpTestData(cls):
        cls.notice = TenderNotice.objects.create(
            notice_id="OP0001",
            source="worldbank",
            bid_description="Construction supervision consultancy",
            notice_text_sanitized=BODY,
            category="consulting",
        )

    def _service(self, **kwargs) -> SearchService:
        return SearchService(**kwargs)

    def test_a_missing_api_key_falls_back_and_says_so(self):
        service = self._service(
            embedding=FakeEmbedding(fails=EmbeddingUnavailable("no key")),
            store=FakeStore(),
        )
        response = service.search("annual turnover")

        self.assertEqual(response.retrieval, "fts")
        self.assertEqual(response.degraded_reason, "embeddings_unavailable")
        self.assertTrue(response.hits)

    def test_a_dead_vector_store_falls_back_with_its_own_reason(self):
        service = self._service(
            embedding=FakeEmbedding(),
            store=FakeStore(fails=QdrantUnavailable("connection refused")),
        )
        response = service.search("annual turnover")

        self.assertEqual(response.retrieval, "fts")
        self.assertEqual(response.degraded_reason, "vector_store_unavailable")

    def test_an_empty_collection_falls_back_rather_than_returning_nothing(self):
        """A deployment that has not run the import is the common case."""
        service = self._service(embedding=FakeEmbedding(), store=FakeStore(hits=[]))
        response = service.search("annual turnover")

        self.assertEqual(response.retrieval, "fts")
        self.assertEqual(response.degraded_reason, "no_vector_match")

    def test_a_fallback_hit_carries_the_offsets_a_viewer_needs(self):
        """Without them the citation opens a document and highlights nothing."""
        service = self._service(
            embedding=FakeEmbedding(fails=EmbeddingUnavailable("no key")),
            store=FakeStore(),
        )
        hit = service.search("annual turnover").hits[0]

        self.assertEqual(hit.retrieval, "fts")
        self.assertEqual(hit.payload["source_type"], "text")
        self.assertIsNotNone(hit.payload["char_start"])
        self.assertIsNotNone(hit.payload["char_end"])
        self.assertIn("turnover", hit.payload["content"].casefold())

    def test_one_passage_per_notice_rather_than_every_match(self):
        """Otherwise one wordy tender fills the whole result list."""
        TenderNotice.objects.create(
            notice_id="OP0002",
            source="worldbank",
            bid_description="Road rehabilitation works",
            notice_text_sanitized=BODY,
            category="construction",
        )
        service = self._service(
            embedding=FakeEmbedding(fails=EmbeddingUnavailable("no key")),
            store=FakeStore(),
        )
        hits = service.search("annual turnover").hits

        keys = [hit.payload["source_key"] for hit in hits]
        self.assertEqual(len(keys), len(set(keys)))

    def test_the_fallback_honours_a_category_filter(self):
        service = self._service(
            embedding=FakeEmbedding(fails=EmbeddingUnavailable("no key")),
            store=FakeStore(),
        )
        response = service.search("annual turnover", category="construction")
        self.assertEqual(response.hits, [])

    def test_nothing_is_spent_on_a_blank_query(self):
        embedding = FakeEmbedding()
        response = self._service(embedding=embedding, store=FakeStore()).search("   ")

        self.assertEqual(response.retrieval, "none")
        self.assertEqual(response.degraded_reason, "empty_query")
        self.assertEqual(embedding.calls, [])


class VectorPathIsPreferred(TestCase):
    """When it can answer, it answers alone — the two are never merged."""

    def test_a_vector_answer_is_not_topped_up_from_full_text(self):
        hit = SearchHit(score=0.83, payload={"content": "…", "source_key": "notice:1"})
        service = SearchService(
            embedding=FakeEmbedding(), store=FakeStore(hits=[hit])
        )
        response = service.search("annual turnover", limit=5)

        self.assertEqual(response.retrieval, "vector")
        self.assertEqual(len(response.hits), 1)
        self.assertEqual(response.degraded_reason, "")

    def test_filters_are_pushed_into_the_store_not_applied_afterwards(self):
        """A post-filter returns the global top-K minus the wrong tenders."""
        store = FakeStore(hits=[SearchHit(0.9, {"content": "…"})])
        service = SearchService(embedding=FakeEmbedding(), store=store)
        service.search("turnover", notice_id="OP0001", category="consulting")

        self.assertEqual(
            store.filters[0], {"notice_id": "OP0001", "category": "consulting"}
        )


class OnlyOpenTendersWhenAsked(TestCase):
    """`active_only` is a post-filter against Postgres, not a payload field.

    A deadline baked into the vector store goes stale the moment it passes, so
    the index would have to be rewritten daily to keep saying something true.
    Whether a tender is open is a fact about *now*, so it is asked of the rows.
    """

    @classmethod
    def setUpTestData(cls):
        from datetime import timedelta

        from django.utils import timezone

        cls.open_notice = TenderNotice.objects.create(
            notice_id="OPEN",
            source="worldbank",
            notice_type=TenderNotice.OPPORTUNITY_TYPES[0],
            bid_description="Open tender",
            notice_text_sanitized=BODY,
            deadline_date=timezone.now() + timedelta(days=10),
        )
        cls.closed_notice = TenderNotice.objects.create(
            notice_id="CLOSED",
            source="worldbank",
            notice_type=TenderNotice.OPPORTUNITY_TYPES[0],
            bid_description="Closed tender",
            notice_text_sanitized=BODY,
            deadline_date=timezone.now() - timedelta(days=400),
        )

    def _hits(self):
        return [
            SearchHit(0.9, {"content": "…", "notice_id": "CLOSED", "source_key": "notice:CLOSED"}),
            SearchHit(0.8, {"content": "…", "notice_id": "OPEN", "source_key": "notice:OPEN"}),
        ]

    def test_the_open_tender_leads_even_when_a_closed_one_ranks_higher(self):
        store = FakeStore(hits=self._hits())
        service = SearchService(embedding=FakeEmbedding(), store=store)
        response = service.search("turnover", limit=5, active_only=True)

        self.assertEqual(
            [hit.payload["notice_id"] for hit in response.hits], ["OPEN", "CLOSED"]
        )

    def test_history_is_labelled_rather_than_discarded(self):
        """Dropping it was the first attempt and it broke a different answer:
        "what do these tenders typically require" is a question about a
        pattern, and the pattern lives in the archive."""
        store = FakeStore(hits=self._hits())
        response = SearchService(embedding=FakeEmbedding(), store=store).search(
            "turnover", limit=5, active_only=True
        )

        marks = {h.payload["notice_id"]: h.payload["tender_open"] for h in response.hits}
        self.assertEqual(marks, {"OPEN": True, "CLOSED": False})

    def test_the_store_is_over_fetched_because_it_cannot_know_the_date(self):
        store = FakeStore(hits=self._hits())
        SearchService(embedding=FakeEmbedding(), store=store).search(
            "turnover", limit=5, active_only=True
        )

        self.assertGreater(store.limits[0], 5)

    def test_without_the_flag_the_archive_is_still_searchable(self):
        """The search box is also an archive browser; only the chat filters."""
        store = FakeStore(hits=self._hits())
        response = SearchService(embedding=FakeEmbedding(), store=store).search(
            "turnover", limit=5
        )

        self.assertEqual(len(response.hits), 2)


class TheSourcePaneReadsLikeTheNotice(TestCase):
    """Paragraph spans, so a citation is not one wall of characters.

    Canonicalising a notice turns every paragraph break into a full stop —
    right for quoting, unreadable as a page. The spans put the breaks back
    where the borrower had them, and they are *located* in the same string the
    offsets index rather than reconstructed, so a block boundary and a
    highlight cannot disagree.
    """

    def setUp(self):
        self.notice = TenderNotice.objects.create(
            notice_id="OPBLOCK",
            country="Uzbekistan",
            notice_type="Request for Bids",
            bid_description="Substation works",
            notice_text_sanitized=(
                "<p>The Employer invites sealed bids.</p>"
                "<p>Bidders must hold a valid licence.</p>"
                "<li>Audited accounts for three years.</li>"
            ),
        )

    def body(self):
        return self.client.get(
            "/api/v1/search/source/", {"source_key": "notice:OPBLOCK"}
        ).json()

    def test_each_paragraph_is_reported_with_its_span(self):
        blocks = self.body()["blocks"]
        self.assertEqual(len(blocks), 3)
        self.assertEqual([block["tag"] for block in blocks], ["p", "p", "li"])

    def test_a_span_indexes_the_same_string_the_quote_does(self):
        payload = self.body()
        first = payload["blocks"][0]
        self.assertEqual(
            payload["text"][first["start"] : first["end"]],
            "The Employer invites sealed bids.",
        )

    def test_the_spans_run_in_order_and_do_not_overlap(self):
        blocks = self.body()["blocks"]
        for earlier, later in zip(blocks, blocks[1:]):
            self.assertLessEqual(earlier["end"], later["start"])

    def test_a_source_with_no_markup_reports_no_paragraphs(self):
        """An extracted PDF has no breaks to find, and inventing them would
        draw lines the document does not have."""
        self.notice.notice_text_sanitized = "One line of plain extracted text."
        self.notice.save(update_fields=["notice_text_sanitized"])

        self.assertEqual(len(self.body()["blocks"]), 1)


class HybridRetrieval(TestCase):
    """Both arms, fused by rank — and what that does not change (D58).

    The case worth a test is the one that motivated the whole thing: a reader
    types a tender reference, and the dense arm structurally cannot find it
    because an identifier has no semantic neighbourhood. Everything else here
    guards the contract around that — hybrid is opt-in, the label says what
    actually produced the results, and the setting is a ceiling.
    """

    @classmethod
    def setUpTestData(cls):
        cls.named = TenderNotice.objects.create(
            notice_id="TRIP-CS-01",
            source="worldbank",
            bid_description="Supervision consultancy for the TRIP programme",
            notice_text_sanitized=(
                "<p>The Employer invites expressions of interest for supervision "
                "services under the programme.</p>"
            ),
            category="consulting",
        )
        cls.other = TenderNotice.objects.create(
            notice_id="OP0009",
            source="worldbank",
            bid_description="Road rehabilitation works",
            notice_text_sanitized=BODY,
            category="construction",
        )

    def test_a_reference_code_reaches_the_answer_the_dense_arm_missed(self):
        """The dense arm returns something else entirely; fusion keeps both."""
        elsewhere = SearchHit(
            score=0.88,
            payload={
                "content": "Consultants shall be selected under QCBS.",
                "source_key": "notice:OP0009",
                "position_id": "s0",
                "notice_id": "OP0009",
            },
        )
        service = SearchService(
            embedding=FakeEmbedding(), store=FakeStore(hits=[elsewhere])
        )

        response = service.search("TRIP-CS-01", limit=5, hybrid=True)

        self.assertEqual(response.retrieval, "hybrid")
        keys = [hit.payload["source_key"] for hit in response.hits]
        self.assertIn("notice:TRIP-CS-01", keys)

    def test_a_fused_hit_reports_the_ranks_it_was_fused_from(self):
        shared = SearchHit(
            score=0.9,
            payload={
                "content": "The Employer invites expressions of interest.",
                "source_key": "notice:TRIP-CS-01",
                "position_id": "s0",
                "notice_id": "TRIP-CS-01",
            },
        )
        service = SearchService(
            embedding=FakeEmbedding(), store=FakeStore(hits=[shared])
        )

        response = service.search("TRIP-CS-01 supervision", limit=5, hybrid=True)

        top = response.hits[0].payload
        self.assertEqual(top["rank_dense"], 1)
        self.assertIn("rank_lexical", top)

    def test_hybrid_is_not_what_a_caller_gets_without_asking(self):
        """The old contract is untouched for anyone who does not opt in."""
        hit = SearchHit(
            score=0.83,
            payload={"content": "…", "source_key": "notice:OP0009", "position_id": "s0"},
        )
        service = SearchService(embedding=FakeEmbedding(), store=FakeStore(hits=[hit]))

        self.assertEqual(service.search("turnover", limit=5).retrieval, "vector")

    def test_asking_for_hybrid_does_not_make_a_one_armed_answer_hybrid(self):
        """A label is for what produced the results, not for what was requested."""
        hit = SearchHit(
            score=0.83,
            payload={"content": "…", "source_key": "notice:OP0009", "position_id": "s0"},
        )
        service = SearchService(embedding=FakeEmbedding(), store=FakeStore(hits=[hit]))

        response = service.search("zzzzzz nothing matches this", limit=5, hybrid=True)

        self.assertEqual(response.retrieval, "vector")

    def test_the_setting_is_a_ceiling_and_not_a_default(self):
        hit = SearchHit(
            score=0.83,
            payload={"content": "…", "source_key": "notice:OP0009", "position_id": "s0"},
        )
        service = SearchService(embedding=FakeEmbedding(), store=FakeStore(hits=[hit]))

        with override_settings(RAG={**settings.RAG, "HYBRID": False}):
            response = service.search("turnover", limit=5, hybrid=True)

        self.assertEqual(response.retrieval, "vector")

    def test_a_precomputed_vector_is_not_embedded_twice(self):
        """The cache paid for this embedding; retrieval reuses it."""
        embedding = FakeEmbedding()
        service = SearchService(embedding=embedding, store=FakeStore(hits=[]))

        service.search("annual turnover", limit=5, vector=[0.4, 0.4, 0.4, 0.4])

        self.assertEqual(embedding.calls, [])
