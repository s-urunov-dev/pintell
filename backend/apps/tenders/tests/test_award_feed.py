"""The finished-contract feed and the same-line-of-work block."""

from __future__ import annotations

from contextlib import contextmanager
from datetime import date, timedelta
from decimal import Decimal

from django.test import TestCase
from django.utils import timezone

from apps.tenders.award_feed import (
    ROLE_AWARDEE,
    ROLE_EVALUATED,
    award_rows,
    participants,
    similar_awards,
)
from apps.tenders.models import ContractAward, TenderNotice


def _award(notice_id: str, **overrides) -> ContractAward:
    notice_fields = {
        "notice_type": "Contract Award",
        "country": "Uzbekistan",
        "category": "consulting",
        "subcategory": "audit",
        "procurement_group": "CS",
        "bid_description": "Audit of the project financial statements",
        "notice_date": date(2026, 6, 1),
    }
    for key in list(overrides):
        if key in notice_fields or key == "project_name":
            notice_fields[key] = overrides.pop(key)

    notice = TenderNotice.objects.create(notice_id=notice_id, **notice_fields)
    defaults = {
        "supplier_name": "WINNER LLC",
        "supplier_country": "Uzbekistan",
        "currency": "USD",
        "contract_price": Decimal("100000.00"),
        "award_date": date(2026, 6, 15),
        "awarded_bidders": [{"name": "WINNER LLC", "country": "Uzbekistan"}],
    }
    return ContractAward.objects.create(notice=notice, **{**defaults, **overrides})


class AwardRowsTests(TestCase):
    def test_the_role_filter_narrows_to_awards_that_name_that_role(self):
        _award("OP1")
        _award("OP2", evaluated_bidders=[{"name": "RUNNER UP LLC"}])

        self.assertEqual(award_rows().count(), 2)
        ids = [a.notice_id for a in award_rows(role=ROLE_EVALUATED)]
        self.assertEqual(ids, ["OP2"])

    def test_search_reaches_companies_that_lost_not_only_the_winner(self):
        """A vendor looking for a competitor wants the contracts it lost too."""
        _award("OP1", evaluated_bidders=[{"name": "RIVAL ENGINEERING"}])
        _award("OP2")

        ids = [a.notice_id for a in award_rows(search="RIVAL")]
        self.assertEqual(ids, ["OP1"])

    def test_an_unknown_country_group_returns_nothing_rather_than_everything(self):
        _award("OP1")
        self.assertEqual(award_rows(country_group="atlantis").count(), 0)

    def test_subcategory_narrows_within_consulting(self):
        _award("OP1", subcategory="audit")
        _award("OP2", subcategory="engineering")
        ids = [a.notice_id for a in award_rows(subcategory="engineering")]
        self.assertEqual(ids, ["OP2"])


class ParticipantsTests(TestCase):
    def test_every_company_is_listed_with_the_role_it_held(self):
        award = _award(
            "OP1",
            awarded_bidders=[
                {"name": "WINNER LLC", "country": "Uzbekistan"},
                {"name": "JV PARTNER LLC", "country": "Turkiye"},
            ],
            evaluated_bidders=[{"name": "RUNNER UP LLC", "country": "Kazakhstan"}],
            rejected_bidders=[
                {"name": "THROWN OUT LLC", "rejection_reason": "Non Responsive"}
            ],
        )

        rows = participants(award)

        self.assertEqual(
            [(r["name"], r["role"]) for r in rows],
            [
                ("WINNER LLC", "awardee"),
                ("JV PARTNER LLC", "awardee"),
                ("RUNNER UP LLC", "evaluated"),
                ("THROWN OUT LLC", "rejected"),
            ],
        )

    def test_a_rejection_reason_rides_with_the_company_that_got_it(self):
        award = _award(
            "OP1",
            rejected_bidders=[{"name": "THROWN OUT LLC", "rejection_reason": "Late bid"}],
        )
        rejected = [r for r in participants(award) if r["role"] == "rejected"]
        self.assertEqual(rejected[0]["reason"], "Late bid")

    def test_the_winner_carries_the_website_and_the_co_members_do_not(self):
        """Enrichment writes to the flat columns only, so nothing else can claim one."""
        award = _award(
            "OP1",
            supplier_website="https://winner.uz",
            supplier_website_source="ai_search",
            awarded_bidders=[{"name": "WINNER LLC"}, {"name": "JV PARTNER LLC"}],
        )
        rows = participants(award)
        self.assertEqual(rows[0]["website"], "https://winner.uz")
        self.assertEqual(rows[1]["website"], "")


class _FakeSimilarity:
    """Stands in for the vector store, returning a decided neighbour order.

    The tests below are about the *join* — which neighbours turn out to be
    awards with a named winner, in what order, deduplicated how — and none of
    them is about whether Qdrant returns good neighbours. Stubbing the store is
    what keeps them from being an integration test of a container.
    """

    def __init__(self, ranked=(), fails=False):
        self.ranked = list(ranked)
        self.fails = fails
        self.asked: list[str] = []
        self.titles: list[str] = []

    def similar_award_notices(self, source_key, *, limit, scan=200, title=""):
        # The title is passed by `similar_awards` and is what keeps the query
        # on the trade rather than on whichever figure happened to be unique.
        # Recorded so a test can assert it was handed over at all.
        self.asked.append(source_key)
        self.titles.append(title)
        if self.fails:
            raise RuntimeError("the store is down")
        return [
            (notice_id, 0.9 - index / 100, f"passage for {notice_id}")
            for index, notice_id in enumerate(self.ranked)
        ]


@contextmanager
def _similarity(fake):
    """Swap the similarity service for the duration of a test.

    `similar_awards` imports it inside the function — deliberately, so a broken
    or absent index costs that panel and nothing else — so the patch target is
    the module attribute rather than a name bound at import time.
    """
    from apps.rag_indexer import services as rag_services

    original = rag_services.get_similarity_service
    rag_services.get_similarity_service = lambda: fake
    try:
        yield fake
    finally:
        rag_services.get_similarity_service = original


class SimilarAwardsTests(TestCase):
    """Awarded contracts retrieved by meaning (D45), joined against the rows.

    What changed from D42 is *membership*: a category filter chose these
    before, and the semantic index chooses them now. What did not change is
    everything the join guarantees — a named winner, one row per winner, the
    tender never its own comparison, and empty rather than widened.
    """

    def setUp(self):
        self.notice = TenderNotice.objects.create(
            notice_id="OPEN1",
            notice_type="Request for Expression of Interest",
            country="Uzbekistan",
            category="consulting",
            subcategory="audit",
            procurement_group="CS",
            bid_description="External audit of project accounts",
            deadline_date=timezone.now() + timedelta(days=30),
        )

    def test_the_nearest_award_comes_back_with_the_passage_that_matched(self):
        _award("AUDIT1", supplier_name="AUDIT LLC")

        with _similarity(_FakeSimilarity(["AUDIT1"])):
            rows = similar_awards(self.notice)

        self.assertEqual([award.notice_id for award in rows], ["AUDIT1"])
        # The passage is not decoration: it is the only part of the match a
        # reader can check, and the score is only allowed on the row because
        # the passage travels with it.
        self.assertEqual(rows[0].match_passage, "passage for AUDIT1")
        self.assertGreater(rows[0].match_score, 0)

    def test_the_order_is_the_index_order_not_the_award_date(self):
        """Relevance decides which rows exist now; the date is still on the
        row and no longer chooses it."""
        _award("NEAR", supplier_name="A LLC", award_date=date.today() - timedelta(days=400))
        _award("FAR", supplier_name="B LLC", award_date=date.today())

        with _similarity(_FakeSimilarity(["NEAR", "FAR"])):
            rows = similar_awards(self.notice)

        self.assertEqual([award.notice_id for award in rows], ["NEAR", "FAR"])

    def test_a_neighbour_that_is_not_an_award_is_dropped(self):
        """Neighbours are notices; only some of them are finished contracts."""
        TenderNotice.objects.create(
            notice_id="JUSTANOTICE",
            notice_type="Request for Expression of Interest",
            country="Uzbekistan",
            category="consulting",
        )
        _award("AUDIT1", supplier_name="AUDIT LLC")

        with _similarity(_FakeSimilarity(["JUSTANOTICE", "AUDIT1"])):
            rows = similar_awards(self.notice)

        self.assertEqual([award.notice_id for award in rows], ["AUDIT1"])

    def test_an_award_with_no_named_winner_is_not_shown(self):
        """The panel's question is *who* won, and an anonymised individual
        consultant answers it with a placeholder."""
        _award("ANON", supplier_name="")

        with _similarity(_FakeSimilarity(["ANON"])):
            self.assertEqual(similar_awards(self.notice), [])

    def test_the_tender_is_never_its_own_comparison(self):
        """A vendor can open an award notice from the feed, and its own
        contract is the nearest thing in the index to itself."""
        ContractAward.objects.create(
            notice=self.notice,
            supplier_name="ITSELF LLC",
            supplier_country="Uzbekistan",
            currency="USD",
            contract_price=Decimal("1.00"),
            award_date=date(2026, 6, 15),
        )

        with _similarity(_FakeSimilarity(["OPEN1"])):
            self.assertEqual(similar_awards(self.notice), [])

    def test_a_notice_that_was_never_indexed_gets_no_panel(self):
        """Empty rather than widened — the same answer D42 gave a tender whose
        line of work could not be placed."""
        _award("AUDIT1", supplier_name="AUDIT LLC")

        with _similarity(_FakeSimilarity([])):
            self.assertEqual(similar_awards(self.notice), [])

    def test_a_store_that_is_down_costs_this_panel_and_nothing_else(self):
        """`apps.tenders` now depends on the index, so the failure path is the
        whole justification for that dependency being acceptable."""
        _award("AUDIT1", supplier_name="AUDIT LLC")

        with _similarity(_FakeSimilarity(["AUDIT1"], fails=True)):
            self.assertEqual(similar_awards(self.notice), [])

    def test_the_tenders_own_title_is_handed_to_the_index(self):
        """Without it the query anchors on the rarest sentence, which for a
        procurement notice is a figure or a project code."""
        _award("AUDIT1", supplier_name="AUDIT LLC")
        fake = _FakeSimilarity(["AUDIT1"])

        with _similarity(fake):
            similar_awards(self.notice)

        self.assertEqual(fake.titles, ["External audit of project accounts"])

    def test_an_unknown_notice_is_a_404(self):
        response = self.client.get("/api/tenders/NOPE/similar-awards/")
        self.assertEqual(response.status_code, 404)

    def test_the_endpoint_carries_the_score_and_the_passage_together(self):
        _award("AUDIT1", supplier_name="AUDIT LLC")

        with _similarity(_FakeSimilarity(["AUDIT1"])):
            response = self.client.get("/api/tenders/OPEN1/similar-awards/")

        row = response.json()["results"][0]
        self.assertIn("match_score", row)
        self.assertIn("match_passage", row)

    def test_the_awards_feed_carries_neither(self):
        """A list that was never ranked must not render a relevance chip; the
        fields are absent there rather than null."""
        _award("AUDIT1", supplier_name="AUDIT LLC")

        row = self.client.get("/api/awards/").json()["results"][0]

        self.assertNotIn("match_score", row)
        self.assertNotIn("match_passage", row)


class OneRowPerWinnerTests(TestCase):
    """A firm that won three lots does not get three of the five rows."""

    def setUp(self):
        self.notice = TenderNotice.objects.create(
            notice_id="OPEN1",
            notice_type="Request for Expression of Interest",
            country="Uzbekistan",
            category="consulting",
            subcategory="audit",
            deadline_date=timezone.now() + timedelta(days=30),
        )

    def test_the_same_company_is_shown_once_with_its_best_match(self):
        """Observed on OP00460945 before this rule: one firm, three rows,
        three near-identical descriptions."""
        for lot in ("LOT1", "LOT2", "LOT3"):
            _award(lot, supplier_name="ELITE STROY")
        _award("OTHER", supplier_name="RIVAL AUDIT")

        with _similarity(_FakeSimilarity(["LOT1", "LOT2", "LOT3", "OTHER"])):
            rows = similar_awards(self.notice)

        self.assertEqual([award.notice_id for award in rows], ["LOT1", "OTHER"])

    def test_the_cut_happens_after_deduplication_not_before(self):
        """Otherwise the repeated winner spends the slots on its own way out,
        and the panel comes back shorter than it should be."""
        for index in range(6):
            _award(f"SAME{index}", supplier_name="ELITE STROY")
        _award("OTHER", supplier_name="RIVAL AUDIT")

        with _similarity(_FakeSimilarity([f"SAME{i}" for i in range(6)] + ["OTHER"])):
            rows = similar_awards(self.notice)

        self.assertEqual(len(rows), 2)
        self.assertIn("OTHER", [award.notice_id for award in rows])



class AwardListEndpointTests(TestCase):
    def test_the_feed_lists_finished_contracts_with_their_source(self):
        _award("OP1")

        response = self.client.get("/api/awards/")

        self.assertEqual(response.status_code, 200)
        body = response.json()
        self.assertEqual(body["count"], 1)
        row = body["results"][0]
        self.assertEqual(row["notice_id"], "OP1")
        self.assertIn("OP1", row["source_url"])
        self.assertEqual(row["participants"][0]["role"], ROLE_AWARDEE)
