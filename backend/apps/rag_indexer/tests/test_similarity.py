"""Choosing the passage that actually distinguishes a notice.

The centroid this replaced was not returning wrong neighbours so much as
uninformative reasons: a procurement notice is mostly shared template text, so
the mean of its chunks sits in the middle of the boilerplate, scores cluster,
and the sentence offered as the reason two tenders matched is the one about
where to collect the documents.

These tests fix the selection *rule*, not the quality of any particular pick —
quality is what `manage.py eval_representative` measures against the corpus,
because a hand-written list of "correct" passages would be one person's opinion
in a fixture and a list of boilerplate phrases would be an unsourced fact.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.rag_indexer.services.qdrant import SearchHit
from apps.rag_indexer.services.similarity import DUPLICATE_SCAN, SimilarityService


class FakeStore:
    """A store where each chunk has a decided number of near-duplicates."""

    collection = "test"

    def __init__(self, chunks, title=""):
        # (content, how many *other notices* carry a near-duplicate)
        self._chunks = chunks
        self._title = title
        self.searches = 0
        self.filters: list[dict] = []

    def points_for_source(self, source_key, *, limit=24):
        return [
            {
                "vector": [float(index)],
                "payload": {"content": content, "title": self._title},
            }
            for index, (content, _duplicates) in enumerate(self._chunks[:limit])
        ]

    def build_filter(self, **kwargs):
        self.filters.append({k: v for k, v in kwargs.items()})
        return kwargs or None

    def search(self, vector, *, limit, query_filter=None, score_threshold=None):
        self.searches += 1
        _content, duplicates = self._chunks[int(vector[0])]
        return [
            SearchHit(score=0.99, payload={"notice_id": f"OP{n:05d}"})
            for n in range(duplicates)
        ]


class RarityFiltersAndTheTitleRanks(SimpleTestCase):
    def test_the_distinctive_sentence_is_chosen_over_the_boilerplate(self):
        store = FakeStore([
            ("Interested eligible bidders may obtain further information.", 38),
            ("Supply and installation of a 220 kV substation at Nurek.", 1),
            ("Bids must be delivered to the address below.", 35),
        ])
        best = SimilarityService(store=store).representative("notice:OP1")

        self.assertIn("220 kV", best.content)
        self.assertEqual(best.duplicates, 1)

    def test_the_title_decides_between_two_equally_rare_passages(self):
        """Rarity alone chose figures and project codes — unique, and not a
        description of the work. The title says what the tender is for."""
        store = FakeStore(
            [
                ("Lot 1: US$1,250,000 net of the Bidders other commitments.", 0),
                ("Rehabilitation of the Meghri gravity irrigation scheme.", 1),
            ],
            title="Meghri gravity irrigation scheme rehabilitation",
        )
        best = SimilarityService(store=store).representative("notice:OP1")

        self.assertIn("irrigation", best.content)

    def test_boilerplate_is_excluded_even_when_it_repeats_a_title_word(self):
        """Otherwise a template paragraph outranks a specific sentence on a
        coincidence."""
        store = FakeStore(
            [
                ("Irrigation works are governed by the Procurement Regulations.", 30),
                ("Construction of a pumping station at Nurek.", 0),
            ],
            title="Irrigation pumping station",
        )
        best = SimilarityService(store=store).representative("notice:OP1")

        self.assertIn("pumping station at Nurek", best.content)

    def test_a_notice_that_is_boilerplate_throughout_still_answers(self):
        """If every candidate is template text there is nothing better to
        offer, and the least common of them is the honest answer."""
        store = FakeStore([("Standard A.", 30), ("Standard B.", 12)], title="Audit")
        best = SimilarityService(store=store).representative("notice:OP1")

        self.assertEqual(best.content, "Standard B.")

    def test_every_candidate_is_scored_because_they_have_to_be_ranked(self):
        """An earlier version stopped at the first chunk shared with nobody.

        That was fine while one chunk was wanted and wrong as soon as three
        were: the top three cannot be known without scoring all of them. The
        cost is bounded by `CANDIDATE_CHUNKS` searches per page view, which is
        a handful of single-digit-millisecond Qdrant calls.
        """
        store = FakeStore([("Unique.", 0), ("Boilerplate.", 40), ("Also rare.", 1)])
        SimilarityService(store=store).representatives("notice:OP1", count=3)

        self.assertEqual(store.searches, 3)

    def test_rarity_orders_what_the_title_cannot_separate(self):
        """A title of "Lot 2" says nothing, and then rarity decides alone."""
        store = FakeStore([("Boilerplate.", 40), ("Rare.", 1), ("Unique.", 0)], title="Lot 2")
        chosen = SimilarityService(store=store).representatives("notice:OP1", count=2)

        self.assertEqual([item.content for item in chosen], ["Unique.", "Rare."])

    def test_a_notice_that_is_boilerplate_throughout_says_so(self):
        """A two-line cancellation has no distinguishing sentence, and the
        caller is told rather than handed a shared paragraph as if it were one."""
        store = FakeStore([
            ("Standard text.", DUPLICATE_SCAN),
            ("More standard text.", DUPLICATE_SCAN),
        ])
        best = SimilarityService(store=store).representative("notice:OP1")

        self.assertTrue(best.is_boilerplate)

    def test_the_title_is_read_off_the_payload_when_none_is_passed(self):
        """Every chunk carries it, so a direct call still gets topicality."""
        store = FakeStore(
            [("Boring.", 0), ("Supply of water meters.", 0)],
            title="Supply of water meters",
        )
        best = SimilarityService(store=store).representative("notice:OP1")

        self.assertIn("water meters", best.content)

    def test_a_notice_with_nothing_indexed_has_no_representative(self):
        service = SimilarityService(store=FakeStore([]))
        self.assertIsNone(service.representative("notice:OP1"))

    def test_duplicates_are_counted_by_notice_not_by_point(self):
        """A long document repeating its own header would otherwise look like
        the most common text in the archive rather than the most repetitive
        document in it."""
        store = FakeStore([("Repeated header.", 3)])
        best = SimilarityService(store=store).representative("notice:OP1")

        self.assertEqual(best.duplicates, 3)


class AwardsAreFilteredInsideTheStore(SimpleTestCase):
    """The empty-panel bug, and why a post-filter could not have fixed it.

    An award notice is a table of bid prices; an open tender is prose about the
    work. Embedding similarity is dominated by that difference, so a search
    from a consulting REOI returns other REOIs — measured on OP00460945, 426
    nearest neighbours and **zero** awards, out of an archive holding 13,255.
    Fetching deeper does not help when the whole neighbourhood is the wrong
    genre; the filter has to be applied where the ranking happens.
    """

    def test_the_award_search_asks_the_store_for_awards(self):
        store = FakeStore([("Construction supervision of the Nurek road.", 0)])
        SimilarityService(store=store).similar_award_notices(
            "notice:OP1", limit=5, title="Construction supervision"
        )

        award_filters = [f for f in store.filters if f.get("is_award")]
        self.assertTrue(award_filters, "the search did not restrict to awards")
        self.assertEqual(award_filters[0]["exclude"], {"source_key": "notice:OP1"})

    def test_choosing_the_representative_does_not_restrict_to_awards(self):
        """Distinctiveness is measured against the whole corpus. Counting a
        passage's duplicates among awards alone would call a boilerplate
        paragraph rare because it happens to be rare in one genre."""
        store = FakeStore([("Some passage.", 0)])
        SimilarityService(store=store).representative("notice:OP1")

        self.assertFalse([f for f in store.filters if f.get("is_award")])
