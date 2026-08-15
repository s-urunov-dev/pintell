"""What the scheduled indexing job will and will not do unattended.

The job exists because a notice synced after the last hand-run import was
invisible to search, to the chat, and to the similar-awards panel — three of
twenty-two open tenders on the deployed server, whose panels were empty for
that reason alone.

What it must not do is turn into an unwatched import. These tests pin the
three bounds that keep it honest: it stays off unless the deployment asked for
it, it never reaches for documents whose first pass has not been costed, and a
dead dependency is a recorded outcome rather than a failing worker.
"""

from __future__ import annotations

from django.conf import settings
from django.test import SimpleTestCase, override_settings

from apps.rag_indexer import tasks
from apps.rag_indexer.services import QdrantUnavailable


class FakeIndexing:
    """An indexing service that records how it was asked to run."""

    calls: list[dict] = []
    error: Exception | None = None

    def __init__(self, *args, **kwargs):
        pass

    def run(self, **kwargs):
        FakeIndexing.calls.append(kwargs)
        if FakeIndexing.error:
            raise FakeIndexing.error
        from apps.rag_indexer.services.indexing import RunStats

        return RunStats(seen=0, indexed=0)


def rag(**overrides):
    return {**settings.RAG, "ENABLED": True, "AUTO_INDEX": True, **overrides}


class TheScheduledRunIsBounded(SimpleTestCase):
    def setUp(self):
        FakeIndexing.calls = []
        FakeIndexing.error = None

    def run_task(self, **kwargs):
        import apps.rag_indexer.services.indexing as indexing

        real = indexing.IndexingService
        indexing.IndexingService = FakeIndexing
        try:
            return tasks.index_new_notices(**kwargs)
        finally:
            indexing.IndexingService = real

    @override_settings(RAG={**settings.RAG, "ENABLED": False})
    def test_a_deployment_without_the_index_does_nothing(self):
        self.assertEqual(self.run_task()["status"], "disabled")
        self.assertEqual(FakeIndexing.calls, [])

    @override_settings(RAG=rag(AUTO_INDEX=False))
    def test_the_schedule_can_be_turned_off_without_turning_off_search(self):
        self.assertEqual(self.run_task()["status"], "disabled")
        self.assertEqual(FakeIndexing.calls, [])

    @override_settings(RAG=rag(AUTO_INDEX_LIMIT=7))
    def test_a_run_is_capped_so_a_large_sync_costs_two_runs_not_a_surprise(self):
        self.run_task()
        self.assertEqual(FakeIndexing.calls[0]["limit"], 7)

    @override_settings(RAG=rag())
    def test_mirrored_documents_are_never_embedded_unattended(self):
        """609 of 619 have never been indexed at all. Starting that first pass
        on a schedule is what D43 ruled out; `archive_to_qdrant` is the way in."""
        self.run_task()
        self.assertEqual(list(FakeIndexing.calls[0]["kinds"]), ["notice"])

    @override_settings(RAG=rag())
    def test_it_stays_inside_the_countries_the_product_is_for(self):
        self.run_task()
        self.assertTrue(FakeIndexing.calls[0]["focus_only"])

    @override_settings(RAG=rag())
    def test_a_dead_store_is_an_outcome_not_a_failing_worker(self):
        FakeIndexing.error = QdrantUnavailable("connection refused")
        result = self.run_task()

        self.assertEqual(result["status"], "unavailable")
        self.assertIn("connection refused", result["reason"])

    @override_settings(RAG=rag())
    def test_an_unexpected_error_is_recorded_rather_than_raised(self):
        FakeIndexing.error = RuntimeError("something else entirely")
        self.assertEqual(self.run_task()["status"], "failed")
