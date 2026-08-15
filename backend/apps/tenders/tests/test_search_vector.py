"""The stored ``search_vector``: who fills it, and that both fillers agree.

The column exists so the lexical arm can rank without re-parsing a notice body
per matching row (D63). That is a performance argument and it is made
elsewhere; what has to be *tested* is the correctness the optimisation rests
on, which is narrower and more interesting:

* the trigger fills the column on every write path, including the bulk one the
  sync actually uses and no ``save()`` ever sees;
* the backfill writes the same value the trigger would have, so a row's rank
  does not depend on which of the two last touched it;
* a search reading the stored column finds the same notices as one recomputing
  the vector.

PostgreSQL only, and skipped rather than failed elsewhere: the column, the
trigger and ``to_tsvector`` are all things SQLite does not have.
"""

from __future__ import annotations

import unittest

from django.conf import settings
from django.core.management import call_command
from django.db import connection
from django.test import TestCase, override_settings

from apps.rag_indexer.services.search import SearchService
from apps.tenders.models import TenderNotice

#: The query terms have to appear here *literally*, not merely stem to the
#: same lexeme. Postgres finds the notice by stem, but `_notice_hits` then
#: picks the passage to return by plain word overlap — so a body saying
#: "audited" while the query says "audit" matches in SQL and yields no hit,
#: and the test reads as a search failure when it is a fixture that does not
#: exercise what it means to.
BODY = (
    "The Consultant shall demonstrate an average annual turnover of "
    "US$ 4,000,000 over the last three years. A financial audit of the "
    "three most recent years shall be submitted with the proposal."
)

postgres_only = unittest.skipUnless(
    connection.vendor == "postgresql",
    "search_vector is a tsvector column maintained by a PL/pgSQL trigger",
)


def make_notice(notice_id: str, **overrides) -> TenderNotice:
    fields = {
        "notice_id": notice_id,
        "notice_type": "Request for Expression of Interest",
        "country": "Uzbekistan",
        "bid_description": "Consulting services for financial audit",
        "notice_text_sanitized": BODY,
    }
    fields.update(overrides)
    return TenderNotice.objects.create(**fields)


def stored_vector(notice_id: str) -> str | None:
    """The raw column, read past Django so nothing can be re-derived on the way."""
    with connection.cursor() as cursor:
        cursor.execute(
            "SELECT search_vector::text FROM tenders_tendernotice WHERE notice_id = %s",
            [notice_id],
        )
        row = cursor.fetchone()
    return row[0] if row else None


@postgres_only
class TheTriggerFillsTheColumn(TestCase):
    def test_an_inserted_notice_arrives_with_a_vector(self):
        make_notice("SV-INSERT-1")

        vector = stored_vector("SV-INSERT-1")

        self.assertTrue(vector)
        # Stemmed, so the assertion is about lexemes rather than words.
        self.assertIn("turnov", vector)
        self.assertIn("audit", vector)

    def test_the_bulk_path_fills_it_too(self):
        """The path that matters: the sync writes with bulk_create, not save().

        `TenderNotice.save` says so in its own docstring, which is why this
        invariant lives in a trigger and not in a model hook.
        """
        TenderNotice.objects.bulk_create([
            TenderNotice(
                notice_id="SV-BULK-1",
                notice_type="Invitation for Bids",
                country="Uzbekistan",
                bid_description="Rehabilitation of rural roads",
                notice_text_sanitized="Bidders shall have completed similar works.",
            )
        ])

        self.assertIn("rehabilit", stored_vector("SV-BULK-1") or "")

    def test_editing_the_body_moves_the_vector(self):
        make_notice("SV-UPDATE-1", bid_description="Supply contract")

        notice = TenderNotice.objects.get(notice_id="SV-UPDATE-1")
        notice.notice_text_sanitized = "Supply of laboratory microscopes."
        notice.save()

        vector = stored_vector("SV-UPDATE-1") or ""
        self.assertIn("microscop", vector)
        self.assertNotIn("turnov", vector)

    def test_an_unrelated_write_leaves_it_alone(self):
        """`UPDATE OF` is what keeps a re-sync from re-parsing every body."""
        make_notice("SV-UNRELATED-1")
        before = stored_vector("SV-UNRELATED-1")

        TenderNotice.objects.filter(notice_id="SV-UNRELATED-1").update(country="Kenya")

        self.assertEqual(stored_vector("SV-UNRELATED-1"), before)


@postgres_only
class TheBackfillAgreesWithTheTrigger(TestCase):
    """The invariant three copies of one expression are kept honest by."""

    def test_a_backfilled_row_matches_a_trigger_filled_one(self):
        make_notice("SV-TRIGGER-1")
        make_notice("SV-BACKFILL-1")

        # Blank one of them past the trigger — a plain UPDATE of the vector
        # column does not touch the two columns the trigger watches, so it
        # stays NULL and looks exactly like a pre-migration row.
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE tenders_tendernotice SET search_vector = NULL WHERE notice_id = %s",
                ["SV-BACKFILL-1"],
            )
        self.assertIsNone(stored_vector("SV-BACKFILL-1"))

        call_command("backfill_search_vector", verbosity=0)

        self.assertEqual(
            stored_vector("SV-BACKFILL-1"),
            stored_vector("SV-TRIGGER-1"),
        )

    def test_it_only_touches_null_rows_so_it_can_be_resumed(self):
        make_notice("SV-RESUME-1")
        before = stored_vector("SV-RESUME-1")

        call_command("backfill_search_vector", verbosity=0)
        call_command("backfill_search_vector", verbosity=0)

        self.assertEqual(stored_vector("SV-RESUME-1"), before)


@postgres_only
class TheTwoSearchPathsAgree(TestCase):
    """The stored column is an optimisation, so it must not change the answer."""

    #: Two constraints on any body used here, both learned the hard way and
    #: neither of them about tsvectors at all:
    #:
    #: * it must be at least ``RAG_MIN_CHUNK_CHARS`` (80) long, or the
    #:   extraction produces no chunks and the notice matches in SQL while
    #:   returning nothing;
    #: * it must contain the query's words *literally*, because the passage is
    #:   chosen by word overlap after Postgres has matched by stem.
    #:
    #: Only ``notice_text_sanitized`` is chunked — ``bid_description`` becomes
    #: the title and is never searched for a passage.
    OTHER_BODY = (
        "Delivery of hospital beds, patient monitors and related ward "
        "furniture to three regional clinics, including installation and "
        "twelve months of on-site maintenance."
    )

    def setUp(self):
        make_notice("SV-SEARCH-1")
        make_notice(
            "SV-SEARCH-2",
            bid_description="Supply of medical equipment",
            notice_text_sanitized=self.OTHER_BODY,
        )

    def _ids(self, query: str) -> set[str]:
        hits = SearchService()._fts_search(
            query, limit=10, notice_id="", category="", subcategory=""
        )
        return {hit.payload.get("notice_id", "") for hit in hits}

    def test_the_stored_column_finds_what_recomputing_finds(self):
        with override_settings(RAG={**settings.RAG, "LEXICAL_STORED_VECTOR": False}):
            recomputed = self._ids("financial audit")
        with override_settings(RAG={**settings.RAG, "LEXICAL_STORED_VECTOR": True}):
            stored = self._ids("financial audit")

        self.assertEqual(stored, recomputed)
        self.assertIn("SV-SEARCH-1", stored)

    def test_the_stored_column_still_separates_the_two_notices(self):
        with override_settings(RAG={**settings.RAG, "LEXICAL_STORED_VECTOR": True}):
            self.assertEqual(self._ids("hospital beds"), {"SV-SEARCH-2"})

    def test_an_unfilled_row_is_missed_which_is_why_the_flag_exists(self):
        """The failure the cutover flag guards, asserted rather than described.

        A row whose vector has not been backfilled does not rank low under the
        stored path — it does not match at all. That is invisible in a result
        list, which is the whole reason the flag stays off until
        `backfill_search_vector --status` reports nothing remaining.
        """
        with connection.cursor() as cursor:
            cursor.execute(
                "UPDATE tenders_tendernotice SET search_vector = NULL WHERE notice_id = %s",
                ["SV-SEARCH-1"],
            )

        with override_settings(RAG={**settings.RAG, "LEXICAL_STORED_VECTOR": True}):
            self.assertNotIn("SV-SEARCH-1", self._ids("financial audit"))
        with override_settings(RAG={**settings.RAG, "LEXICAL_STORED_VECTOR": False}):
            self.assertIn("SV-SEARCH-1", self._ids("financial audit"))
