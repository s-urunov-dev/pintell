"""Cutting an uploaded document on its headings instead of on a length.

The two behaviours worth pinning are the ones a sentence-packer gets wrong on a
Terms of Reference: a threshold separated from the heading that says what it is
*for*, and a table row separated from the header row that says what its numbers
are. Everything else here is bookkeeping around those two.
"""

from __future__ import annotations

from django.test import SimpleTestCase

from apps.rag_indexer.services import structured

TOR = """# Terms of Reference

Consulting services for road supervision.

## 3. Qualification Requirements

### 3.2 Financial Capacity

The average annual turnover shall be at least USD 22.4 million.

| Criterion | Requirement | Documentation |
| --- | --- | --- |
| Turnover | USD 22.4m | Audited accounts |
| Experience | 3 contracts | Completion certificates |

### 3.3 Personnel

A team leader with 10 years of experience is required.
"""


class MarkdownStructure(SimpleTestCase):
    def test_a_passage_carries_the_headings_above_it(self):
        blocks = structured.blocks_from_markdown(TOR)

        turnover = next(b for b in blocks if "22.4 million" in b.text)
        self.assertEqual(
            turnover.heading_path,
            ["Terms of Reference", "3. Qualification Requirements", "3.2 Financial Capacity"],
        )

    def test_a_table_is_one_block_with_its_header_row(self):
        blocks = structured.blocks_from_markdown(TOR)

        table = next(b for b in blocks if b.kind == structured.TABLE)
        self.assertIn("| Criterion | Requirement | Documentation |", table.text)
        self.assertIn("Audited accounts", table.text)
        self.assertIn("Completion certificates", table.text)

    def test_a_heading_starts_a_new_chunk(self):
        blocks = structured.blocks_from_markdown(TOR)

        chunks = [
            structured.chunk_text(batch)
            for batch in structured.group(blocks, chunk_chars=4000, min_chunk_chars=1)
        ]

        # 3.3 is its own chunk however much room 3.2 had left.
        self.assertTrue(any("3.3 Personnel" in chunk for chunk in chunks))
        personnel = next(chunk for chunk in chunks if "team leader" in chunk)
        self.assertNotIn("22.4 million", personnel)

    def test_a_table_is_never_packed_with_its_neighbours(self):
        blocks = structured.blocks_from_markdown(TOR)

        batches = list(structured.group(blocks, chunk_chars=100_000, min_chunk_chars=1))

        table_batches = [
            batch for batch in batches if any(b.kind == structured.TABLE for b in batch)
        ]
        self.assertEqual(len(table_batches), 1)
        self.assertEqual(len(table_batches[0]), 1)

    def test_a_chunk_prints_its_breadcrumb_once(self):
        blocks = structured.blocks_from_markdown(TOR)
        batch = [b for b in blocks if "22.4 million" in b.text]

        text = structured.chunk_text(batch)

        self.assertEqual(text.count("3.2 Financial Capacity"), 1)
        self.assertTrue(text.startswith("Terms of Reference › "))

    def test_a_fenced_block_survives_whole(self):
        blocks = structured.blocks_from_markdown(
            "# Data\n\n```\nrow one\n\nrow two\n```\n"
        )

        code = next(b for b in blocks if b.kind == structured.CODE)
        self.assertIn("row one", code.text)
        self.assertIn("row two", code.text)

    def test_setext_headings_are_headings_too(self):
        blocks = structured.blocks_from_markdown(
            "Qualification\n=============\n\nTurnover of USD 5m.\n"
        )

        body = next(b for b in blocks if "USD 5m" in b.text)
        self.assertEqual(body.heading_path, ["Qualification"])

    def test_the_rendering_is_what_the_chunks_are_measured_against(self):
        blocks = structured.blocks_from_markdown(TOR)

        rendered = structured.render(blocks)

        for block in blocks:
            self.assertIn(block.text, rendered)


class HtmlStructure(SimpleTestCase):
    def test_a_table_keeps_its_grid(self):
        blocks = structured.blocks_from_html(
            "<h2>Criteria</h2><p>See below.</p>"
            "<table><tr><th>Criterion</th><th>Requirement</th></tr>"
            "<tr><td>Turnover</td><td>USD 22.4m</td></tr></table>"
        )

        table = next(b for b in blocks if b.kind == structured.TABLE)
        self.assertIn("| Criterion | Requirement |", table.text)
        self.assertIn("| Turnover | USD 22.4m |", table.text)
        # The document opens at `<h2>`, so level 1 is padded — and the padding
        # is not printed.
        self.assertEqual(table.breadcrumb, "Criteria")

    def test_malformed_markup_yields_what_it_can(self):
        blocks = structured.blocks_from_html("<h1>Open<p>Unclosed paragraph")

        self.assertTrue(any("Unclosed paragraph" in b.text for b in blocks))
