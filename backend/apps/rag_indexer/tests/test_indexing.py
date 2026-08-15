"""The two properties that make an hours-long import safe to start.

Idempotent — running it again over an unchanged archive embeds nothing — and
resumable, which here means a source that failed comes back into the queue on
its own rather than being marked done.

Both are cheap to get wrong in a way no error ever reveals: a broken skip test
just spends the quota again, and a run that recorded a failure as success
leaves a hole in the index that only shows up as a search quietly missing a
tender. Hence the tests.
"""

from __future__ import annotations

from django.test import TestCase

from apps.rag_indexer.models import PIPELINE_VERSION, IndexedSource
from apps.rag_indexer.services.indexing import IndexingService
from apps.tenders.models import TenderNotice

BODY = (
    "<p>The bidder shall demonstrate an average annual turnover of USD 22.4 "
    "million over the last three financial years.</p>"
    "<p>At least three contracts of a similar nature must be evidenced.</p>"
)


class FakeEmbedding:
    """Counts what it was asked to embed, so a skip can be proved."""

    model = "fake-embed"
    dimensions = 4
    batch_size = 100

    def __init__(self):
        self.embedded: list[str] = []
        #: One entry per request, holding how many texts it carried. The
        #: batching test reads this: what matters is not that the texts were
        #: embedded but how many calls it took to embed them.
        self.calls: list[int] = []

    def batched(self, items, size=None):
        yield list(items)

    def embed_documents(self, texts):
        self.calls.append(len(texts))
        self.embedded.extend(texts)
        return [[0.1, 0.2, 0.3, 0.4] for _ in texts]

    @staticmethod
    def enabled() -> bool:
        return True


class FakeStore:
    """Records upserts and deletes without a container behind it."""

    collection = "test_chunks"

    def __init__(self):
        self.points: dict[str, dict] = {}
        self.deleted: list[str] = []

    def ensure_collection(self) -> bool:
        return True

    def upsert(self, points):
        for point_id, _vector, payload in points:
            self.points[point_id] = payload
        return len(points)

    def delete_source(self, source_key: str) -> None:
        self.deleted.append(source_key)


class ArchiveRun(TestCase):
    @classmethod
    def setUpTestData(cls):
        cls.notice = TenderNotice.objects.create(
            notice_id="OP0001",
            source="worldbank",
            bid_description="Construction supervision consultancy",
            notice_text_sanitized=BODY,
            category="consulting",
        )

    def _service(self) -> IndexingService:
        return IndexingService(embedding=FakeEmbedding(), store=FakeStore())

    def test_a_first_pass_indexes_the_notice_and_records_it(self):
        service = self._service()
        stats = service.run()

        self.assertEqual(stats.indexed, 1)
        self.assertTrue(service.store.points)

        row = IndexedSource.objects.get(source_key=f"notice:{self.notice.pk}")
        self.assertEqual(row.status, IndexedSource.Status.INDEXED)
        self.assertEqual(row.embed_model, "fake-embed")
        self.assertEqual(row.pipeline_version, PIPELINE_VERSION)
        self.assertGreater(row.chunk_count, 0)

    def test_a_second_pass_over_an_unchanged_archive_embeds_nothing(self):
        self._service().run()

        second = self._service()
        stats = second.run()

        self.assertEqual(stats.seen, 0)
        self.assertEqual(second.embedding.embedded, [])

    def test_an_edited_body_comes_back_into_the_queue(self):
        self._service().run()

        self.notice.notice_text_sanitized = BODY + "<p>Bids remain valid 120 days.</p>"
        self.notice.save(update_fields=["notice_text_sanitized"])

        second = self._service()
        stats = second.run()

        self.assertEqual(stats.indexed, 1)
        self.assertTrue(second.embedding.embedded)

    def test_a_re_index_clears_the_old_points_first(self):
        """Otherwise chunks the new parse no longer produces keep matching."""
        self._service().run()
        self.notice.notice_text_sanitized = (
            "<p>The scope of this assignment has been revised in full and now "
            "covers a single financial audit of the implementing agency.</p>"
        )
        self.notice.save(update_fields=["notice_text_sanitized"])

        second = self._service()
        second.run()

        self.assertEqual(second.store.deleted, [f"notice:{self.notice.pk}"])

    def test_a_source_that_became_empty_is_cleared_too(self):
        """The worst shape of the same bug.

        A notice whose body is replaced by a one-line cancellation would
        otherwise keep serving every passage of the tender it no longer
        describes — the delete has to happen before the "nothing to index"
        return, not after it.
        """
        self._service().run()
        self.notice.notice_text_sanitized = "<p>Cancelled.</p>"
        self.notice.save(update_fields=["notice_text_sanitized"])

        second = self._service()
        stats = second.run()

        self.assertEqual(stats.empty, 1)
        self.assertEqual(second.store.deleted, [f"notice:{self.notice.pk}"])

    def test_a_new_pipeline_version_makes_everything_stale(self):
        self._service().run()
        IndexedSource.objects.update(pipeline_version=PIPELINE_VERSION - 1)

        stats = self._service().run()
        self.assertEqual(stats.indexed, 1)

    def test_switching_the_embedding_model_makes_everything_stale(self):
        """Vectors from two models are not comparable — see IndexedSource."""
        self._service().run()

        service = self._service()
        service.embedding.model = "another-embed"
        self.assertEqual(service.run().indexed, 1)

    def test_a_body_with_nothing_in_it_is_recorded_rather_than_retried_forever(self):
        TenderNotice.objects.all().delete()
        TenderNotice.objects.create(
            notice_id="OP0002", source="worldbank", notice_text_sanitized="<p>A.</p>"
        )
        stats = self._service().run()

        self.assertEqual(stats.empty, 1)
        self.assertEqual(
            IndexedSource.objects.get().status, IndexedSource.Status.EMPTY
        )

    def test_a_failed_source_is_queued_again_next_run(self):
        """A rate limit must not exile a document from the index for good."""
        self._service().run()
        IndexedSource.objects.update(status=IndexedSource.Status.FAILED)

        self.assertEqual(self._service().run().indexed, 1)


class RequestsGoOutFull(TestCase):
    """The buffering that turned a 28-hour pass into a 2-hour one.

    A source in this archive carries about four and a half passages and the
    provider takes a hundred texts per request, so embedding source by source
    spends twenty-two requests' worth of quota on one request's worth of work.
    The test is on the *call count*, because the passages were always going to
    be embedded correctly — it was the number of round trips that made the
    first real run project to more than a day.
    """

    @classmethod
    def setUpTestData(cls):
        for index in range(30):
            TenderNotice.objects.create(
                notice_id=f"OP{index:05d}",
                source="worldbank",
                bid_description=f"Consultancy assignment number {index}",
                notice_text_sanitized=BODY,
                category="consulting",
            )

    def test_many_sources_are_embedded_in_one_request(self):
        service = IndexingService(embedding=FakeEmbedding(), store=FakeStore())
        stats = service.run()

        self.assertEqual(stats.indexed, 30)
        # Thirty sources, well under the flush threshold, so exactly one call.
        self.assertEqual(len(service.embedding.calls), 1)
        self.assertEqual(service.embedding.calls[0], stats.chunks)

    def test_the_buffer_flushes_once_it_is_full(self):
        """Otherwise a long run holds the whole archive before writing a row."""
        service = IndexingService(embedding=FakeEmbedding(), store=FakeStore())
        service.flush_chunks = 10
        service.run()

        self.assertGreater(len(service.embedding.calls), 1)
        self.assertTrue(all(count >= 10 for count in service.embedding.calls[:-1]))

    def test_every_source_is_still_recorded_with_its_own_chunk_count(self):
        """Batching must not smear one source's passages onto another's row."""
        service = IndexingService(embedding=FakeEmbedding(), store=FakeStore())
        service.run()

        rows = IndexedSource.objects.all()
        self.assertEqual(rows.count(), 30)
        for row in rows:
            self.assertGreater(row.chunk_count, 0)
        self.assertEqual(
            sum(row.chunk_count for row in rows), len(service.embedding.embedded)
        )
