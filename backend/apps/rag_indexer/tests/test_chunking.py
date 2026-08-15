"""What a chunk promises: an exact position, and an id that does not move.

These are the tests that keep a highlight honest. Everything else in this app
can be wrong and produce a worse search; if the offsets drift or the ids are
not stable, the product draws a box over a sentence the citation does not say.

No database and no network: chunking is pure, and it stays testable that way.
"""

from __future__ import annotations

from django.test import SimpleTestCase, override_settings

from apps.compliance.text import canonical
from apps.rag_indexer.chunks import PDF, TEXT, Chunk, SourceRef
from apps.rag_indexer.services.extraction import ExtractionService

SOURCE = SourceRef(
    source_key="notice:42",
    source_type=TEXT,
    notice_id="OP00012345",
    category="consulting",
    subcategory="audit_accounting",
)


class PointIdentity(SimpleTestCase):
    """A point's id is derived from its source and position, never allocated."""

    def test_the_same_position_always_gets_the_same_id(self):
        self.assertEqual(SOURCE.point_id("s3"), SOURCE.point_id("s3"))

    def test_different_positions_get_different_ids(self):
        self.assertNotEqual(SOURCE.point_id("s3"), SOURCE.point_id("s4"))

    def test_the_same_position_in_another_source_is_another_point(self):
        other = SourceRef(**{**SOURCE.__dict__, "source_key": "notice:43"})
        self.assertNotEqual(SOURCE.point_id("s3"), other.point_id("s3"))


class PayloadShape(SimpleTestCase):
    """Position keys belong to one source type and are absent from the other."""

    def test_a_text_chunk_carries_offsets_and_no_page(self):
        payload = Chunk(
            position_id="s7", content="…", source_type=TEXT,
            char_start=100, char_end=180, sentence_index=7,
        ).payload(SOURCE)

        self.assertEqual(payload["char_start"], 100)
        self.assertNotIn("page", payload)
        self.assertNotIn("bbox", payload)

    def test_a_pdf_chunk_carries_a_rectangle_and_no_offsets(self):
        payload = Chunk(
            position_id="p3_b1", content="…", source_type=PDF,
            page=3, bbox=(10.0, 20.0, 300.0, 40.0), page_width=612, page_height=792,
        ).payload(SOURCE)

        self.assertEqual(payload["page"], 3)
        self.assertEqual(payload["bbox"], [10.0, 20.0, 300.0, 40.0])
        self.assertNotIn("char_start", payload)

    def test_the_filter_keys_come_from_the_source(self):
        payload = Chunk("s0", "…", TEXT, char_start=0, char_end=1).payload(SOURCE)
        self.assertEqual(payload["notice_id"], "OP00012345")
        self.assertEqual(payload["category"], "consulting")
        self.assertEqual(payload["subcategory"], "audit_accounting")


@override_settings(
    RAG={
        "CHUNK_CHARS": 120,
        "MIN_CHUNK_CHARS": 10,
        "CHUNK_OVERLAP": 0,
    }
)
class TextChunking(SimpleTestCase):
    """Offsets index the canonical string, and sentences are never cut."""

    def setUp(self):
        self.service = ExtractionService()

    def test_every_offset_pair_selects_its_own_content(self):
        """The property the viewer depends on, stated directly.

        A chunk whose `content` and whose `[char_start:char_end]` slice
        disagree is a highlight over the wrong words — and it is the failure
        that looks fine in every screenshot, because both halves are plausible
        on their own.
        """
        text = canonical(
            "<p>The bidder shall demonstrate an average annual turnover of USD "
            "22.4 million.</p><p>Experience of at least three similar contracts "
            "is required.</p><p>Bids must remain valid for 120 days.</p>"
        )
        chunks = list(self.service.text_chunks(text))

        self.assertTrue(chunks)
        for chunk in chunks:
            self.assertEqual(text[chunk.char_start : chunk.char_end], chunk.content)

    def test_a_sentence_longer_than_the_target_is_kept_whole(self):
        """Truncating it would make `content` disagree with its own range."""
        long_sentence = "The bidder " + "shall comply with the requirement " * 12 + "."
        chunks = list(self.service.text_chunks(canonical(long_sentence)))

        self.assertEqual(len(chunks), 1)
        self.assertGreater(len(chunks[0].content), 120)

    def test_headings_and_page_numbers_are_dropped(self):
        """Below `MIN_CHUNK_CHARS` a passage is noise at the top of results."""
        chunks = list(self.service.text_chunks(canonical("A. 12.")))
        self.assertEqual(chunks, [])

    def test_an_empty_source_produces_nothing_rather_than_one_empty_chunk(self):
        self.assertEqual(list(self.service.text_chunks("")), [])

    def test_position_ids_are_unique_within_a_source(self):
        """Two chunks sharing an id would upsert over each other silently."""
        text = canonical(
            "<p>" + "</p><p>".join(
                f"Requirement number {n} states a clear and separate condition."
                for n in range(20)
            ) + "</p>"
        )
        ids = [chunk.position_id for chunk in self.service.text_chunks(text)]
        self.assertEqual(len(ids), len(set(ids)))
