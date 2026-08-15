"""The similar-awards panel, computed once instead of on every view.

The behaviour under test is not the similarity — that is `test_similarity` —
but the cache around it. Three things have to hold, and each one was a visible
fault before the table existed or would be one if it were built carelessly:

* opening a tender twice must search once,
* the panel must not go blank while the batch catches up, and
* **the winner join must stay live**, because a reparse that gives a contract
  a name has to reach the panel without anything being recomputed (D42a).
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import TestCase
from django.utils import timezone

from apps.rag_indexer import neighbours
from apps.rag_indexer.models import SIMILARITY_VERSION, SimilarAward
from apps.tenders import award_feed
from apps.tenders.models import ContractAward, TenderNotice


def notice(notice_id: str, **fields) -> TenderNotice:
    return TenderNotice.objects.create(
        notice_id=notice_id,
        bid_description=fields.pop("title", f"Tender {notice_id}"),
        country=fields.pop("country", "Uzbekistan"),
        notice_type=fields.pop("notice_type", "Request for Bids"),
        **fields,
    )


def award(notice_id: str, supplier: str) -> ContractAward:
    return ContractAward.objects.create(
        notice=notice(notice_id, notice_type="Contract Award"),
        supplier_name=supplier,
    )


class FakeSimilarity:
    """A neighbour search that counts how often it was asked."""

    def __init__(self, rows):
        self.rows = rows
        self.calls = 0

    def similar_award_notices(self, source_key, *, limit, scan=200, title=""):
        self.calls += 1
        return self.rows


class TheSearchRunsOncePerNotice(TestCase):
    def setUp(self):
        self.reader = notice("OP0001", title="Road reconstruction supervision")
        award("OP9001", "Alpha Engineering")
        self.service = FakeSimilarity([("OP9001", 0.82, "A supervision contract.")])
        # The same target the award-feed tests use: the attribute on the
        # services module, because that is where the name is resolved.
        patcher = patch(
            "apps.rag_indexer.services.get_similarity_service", lambda: self.service
        )
        patcher.start()
        self.addCleanup(patcher.stop)

    def test_the_first_reader_computes_and_the_second_does_not(self):
        """Fifteen Qdrant round trips per page view is what this table exists
        to stop."""
        award_feed.similar_awards(self.reader)
        award_feed.similar_awards(self.reader)
        award_feed.similar_awards(self.reader)

        self.assertEqual(self.service.calls, 1)
        self.assertEqual(SimilarAward.objects.count(), 1)

    def test_the_panel_is_filled_on_the_first_view_rather_than_left_empty(self):
        """A notice synced ten minutes ago must not show an empty block while
        the batch catches up."""
        rows = award_feed.similar_awards(self.reader)

        self.assertEqual([row.notice_id for row in rows], ["OP9001"])
        self.assertEqual(rows[0].match_passage, "A supervision contract.")

    def test_a_recomputation_replaces_the_previous_answer(self):
        neighbours.compute(self.reader)
        self.service.rows = [("OP9002", 0.9, "A different contract.")]
        award("OP9002", "Beta Consult")

        neighbours.compute(self.reader)

        stored = list(SimilarAward.objects.values_list("award_notice_id", flat=True))
        self.assertEqual(stored, ["OP9002"])

    def test_nothing_is_stored_when_the_search_finds_nothing(self):
        """Caching a gap would keep a notice empty after the indexing pass that
        was about to fill it."""
        self.service.rows = []
        award_feed.similar_awards(self.reader)

        self.assertEqual(SimilarAward.objects.count(), 0)

    def test_a_dead_store_costs_the_panel_and_nothing_else(self):
        from apps.rag_indexer.services import QdrantUnavailable

        def explode(*args, **kwargs):
            raise QdrantUnavailable("connection refused")

        self.service.similar_award_notices = explode
        self.assertEqual(award_feed.similar_awards(self.reader), [])


class TheWinnerJoinStaysLive(TestCase):
    """Stored rows are candidates; the panel is decided fresh every request."""

    def setUp(self):
        self.reader = notice("OP0002", title="Substation modernisation")
        self.contract = award("OP9100", "")
        SimilarAward.objects.create(
            notice_id=self.reader.pk,
            award_notice_id="OP9100",
            rank=0,
            score=0.8,
            match_passage="A substation contract.",
            algo_version=SIMILARITY_VERSION,
            computed_at=timezone.now(),
        )

    def test_a_candidate_without_a_named_winner_is_not_a_row(self):
        self.assertEqual(award_feed.similar_awards(self.reader), [])

    def test_a_winner_arriving_in_a_reparse_needs_no_recomputation(self):
        """`PARSER_VERSION` moving is a Postgres event; the neighbour list has
        no way to notice it and does not have to."""
        self.contract.supplier_name = "Gamma Energy"
        self.contract.save(update_fields=["supplier_name"])

        rows = award_feed.similar_awards(self.reader)
        self.assertEqual([row.supplier_name for row in rows], ["Gamma Energy"])


class WhatStillNeedsComputing(TestCase):
    def test_a_notice_with_current_rows_is_not_pending(self):
        reader = notice("OP0003")
        SimilarAward.objects.create(
            notice_id=reader.pk, award_notice_id="OP9200", rank=0,
            algo_version=SIMILARITY_VERSION,
        )
        self.assertNotIn(reader, neighbours.pending())

    def test_rows_from_an_older_method_do_not_count_as_computed(self):
        """Bumping the version is how a change to the method reaches readers
        without a migration that deletes rows."""
        reader = notice("OP0004")
        SimilarAward.objects.create(
            notice_id=reader.pk, award_notice_id="OP9300", rank=0,
            algo_version=SIMILARITY_VERSION - 1,
        )
        self.assertIn(reader, neighbours.pending())
