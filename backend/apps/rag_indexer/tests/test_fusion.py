"""Fusing two ranked lists without inventing a comparison between them.

The property that matters is not "the fused order is good" — that is a question
for a gold set. It is that the fused order is a function of **positions only**,
so nothing a caller does to either arm's scores can change it. These tests pin
that, and pin the reporting that makes an ordering reproducible by hand.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.rag_indexer.services import fusion
from apps.rag_indexer.services.qdrant import SearchHit


def hit(key: str, position: str = "s0", score: float = 0.5, retrieval: str = "vector"):
    return SearchHit(
        score=score,
        retrieval=retrieval,
        payload={"source_key": key, "position_id": position, "content": key},
    )


class Fusion(SimpleTestCase):
    """What reciprocal rank fusion does, and what it refuses to read."""

    def test_agreement_between_the_arms_outranks_one_arm_s_confidence(self):
        """A passage both arms found beats one only the leader found."""
        dense = [hit("a"), hit("b")]
        lexical = [hit("c", retrieval="fts"), hit("b", retrieval="fts")]

        fused = fusion.reciprocal_rank_fusion(
            [("dense", dense), ("lexical", lexical)], k=60, limit=3
        )

        self.assertEqual(fused[0].payload["source_key"], "b")

    def test_the_scores_are_not_read(self):
        """Identical ranks, wildly different scores — identical fused order."""
        cheap = fusion.reciprocal_rank_fusion(
            [
                ("dense", [hit("a", score=0.51), hit("b", score=0.50)]),
                ("lexical", [hit("b", score=0.01, retrieval="fts")]),
            ],
            k=60,
            limit=2,
        )
        confident = fusion.reciprocal_rank_fusion(
            [
                ("dense", [hit("a", score=0.99), hit("b", score=0.98)]),
                ("lexical", [hit("b", score=0.97, retrieval="fts")]),
            ],
            k=60,
            limit=2,
        )

        self.assertEqual(
            [h.payload["source_key"] for h in cheap],
            [h.payload["source_key"] for h in confident],
        )

    def test_a_hit_carries_the_ranks_it_was_fused_from(self):
        """An ordering nobody can reproduce is the ordering D42 removed."""
        fused = fusion.reciprocal_rank_fusion(
            [
                ("dense", [hit("a"), hit("b")]),
                ("lexical", [hit("b", retrieval="fts")]),
            ],
            k=60,
            limit=2,
        )

        top = fused[0].payload
        self.assertEqual(top["rank_dense"], 2)
        self.assertEqual(top["rank_lexical"], 1)
        # And the sum is reproducible by hand from exactly those two numbers.
        self.assertAlmostEqual(fused[0].score, 1 / 62 + 1 / 61)

    def test_only_a_passage_both_arms_found_is_called_hybrid(self):
        fused = fusion.reciprocal_rank_fusion(
            [
                ("dense", [hit("a"), hit("b")]),
                ("lexical", [hit("b", retrieval="fts")]),
            ],
            k=60,
            limit=2,
        )

        labels = {h.payload["source_key"]: h.retrieval for h in fused}
        self.assertEqual(labels["b"], "hybrid")
        self.assertEqual(labels["a"], "vector")

    def test_two_notices_quoting_one_template_stay_two_sources(self):
        """Identity is the chunk's coordinates, never its text."""
        fused = fusion.reciprocal_rank_fusion(
            [
                (
                    "dense",
                    [
                        SearchHit(
                            score=0.7,
                            payload={
                                "source_key": "notice:A",
                                "position_id": "s3",
                                "content": "The bidder shall confirm:",
                            },
                        ),
                        SearchHit(
                            score=0.7,
                            payload={
                                "source_key": "notice:B",
                                "position_id": "s3",
                                "content": "The bidder shall confirm:",
                            },
                        ),
                    ],
                )
            ],
            k=60,
            limit=5,
        )

        self.assertEqual(len(fused), 2)

    def test_a_repeated_passage_is_not_counted_twice(self):
        deduped = fusion.dedupe([hit("a"), hit("a"), hit("b")])

        self.assertEqual([h.payload["source_key"] for h in deduped], ["a", "b"])

    def test_an_empty_arm_contributes_nothing_and_penalises_nothing(self):
        fused = fusion.reciprocal_rank_fusion(
            [("dense", [hit("a"), hit("b")]), ("lexical", [])], k=60, limit=5
        )

        self.assertEqual([h.payload["source_key"] for h in fused], ["a", "b"])
