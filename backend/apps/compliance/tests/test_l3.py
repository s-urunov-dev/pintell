"""L3 reads a mirrored document, and pays only for the parts that state a rule.

Nothing here touches the network. ``extract_with_model`` takes a client, so the
tests hand it a scripted double whose ``messages.create`` returns whatever the
case is about — and, in the refusal cases, records that it was never asked.
"""

from __future__ import annotations

import hashlib
import json
from decimal import Decimal

from django.test import TestCase

from apps.compliance import l3, llm
from apps.compliance.text import canonical, contains_quote, sentences
from apps.tenders.models import HarvestedDocument, TenderNotice

# ---------------------------------------------------------------------------
# Real source text
# ---------------------------------------------------------------------------
# Section G of the Terms of Reference for the Environmental and Social
# Consultant on the Rogun Hydropower Project (Tajikistan), mirrored from the
# Drive link in the notice and parsed by ``pypdf``. Real text rather than a
# constructed fixture, because its quirks are the thing the chunker has to
# survive: the bullets arrive as "- ", the list carries no sentence-ending
# punctuation before them, and "Environment al" is split by the PDF's own
# line breaking.
QUALIFICATION_SECTION = (
    "G. Experience and Qualification Requirements 1. The Consultant should have "
    "the following qualifications: - University degree (higher education) in "
    "environmental engineering , or environmental sciences, or social sciences, "
    "or other relevant technical field. Specialization in environmental and/or "
    "social safeguards or occupational health and safety would be an advantage. "
    "- At least five (5) years of experience in preparing and leading the "
    "preparation of E&S documents for large infrastructure projects, including "
    "dams and hydropower that meet the requirements of the World Bank. - At "
    "least five (5) years of experience in the preparation and implementation of "
    "environmental management and mitigation plans for mid-size and large "
    "hydropower projects, including work related to biodiversity conservation "
    "and dam safety. - Good knowledge of environmental legislation of the "
    "Republic of Tajikistan; - Fluency in verbal and written English, Russian, "
    "and Tajik languages."
)

# Background from the same document. It states no condition on a bidder, which
# is what the prefilter has to notice.
BACKGROUND = (
    "6. Vakhsh Cascade. The main rivers in Tajikistan are classified as "
    "transboundary. Several of those rivers cross the boundaries of two "
    "countries and some others the boundaries of four countries. During the "
    "Soviet period, water resources were shared among the five Central Asia "
    "republics based on plans for water resources development in the Amu Darya "
    "and Syr Darya river basins. With the establishment of the Interstate "
    "Commission for Water Coordination in 1992, the newly independent states "
    "prepared a regional water strategy, but continued to respect existing "
    "principles until the adoption of a new water-sharing agreement. The new "
    "agreement was signed by the Heads of the five states in 1996. The "
    "agreement included the construction of the Kambarata 1 project in Kyrgyz "
    "Republic and the Rogun project in Tajikistan."
)

# One bullet, copied exactly. What a well-behaved model returns as its quote.
REAL_QUOTE = (
    "- At least five (5) years of experience in the preparation and "
    "implementation of environmental management and mitigation plans for "
    "mid-size and large hydropower projects, including work related to "
    "biodiversity conservation and dam safety."
)


def _requirement(key: str = "years_experience", quote: str = REAL_QUOTE) -> dict:
    return {
        "key": key,
        "label": "Years of relevant experience",
        "applies_to": "single",
        "is_mandatory": True,
        "evidence_quote": quote,
        "expression": {"kind": "scalar", "key": key, "op": ">=", "value": 5},
    }


# ---------------------------------------------------------------------------
# The scripted model
# ---------------------------------------------------------------------------
# The chunk prefilter is now the fallback for documents too large to send whole
# (`DEFAULT_WHOLE_DOCUMENT_CHARS`). Every case below that is *about* chunking
# therefore has to ask for it: these fixtures are a few thousand characters and
# would otherwise go in as one request, which is the new default and is covered
# by `WholeDocumentTests`.
CHUNKED = {"whole_document_chars": 0}


class FakeUsage:
    def __init__(self, input_tokens: int, output_tokens: int):
        self.input_tokens = input_tokens
        self.output_tokens = output_tokens
        self.cache_read_input_tokens = 0
        self.cache_creation_input_tokens = 0


class FakeBlock:
    def __init__(self, text: str):
        self.type = "text"
        self.text = text


class FakeResponse:
    def __init__(self, rows: list[dict], *, tokens: tuple[int, int] = (1000, 200)):
        self.content = [FakeBlock(json.dumps({"requirements": rows}))]
        self.usage = FakeUsage(*tokens)
        self.model = "claude-haiku-4-5"
        self.stop_reason = "end_turn"


class FakeClient:
    """One scripted answer per call, and a record of every request made.

    A script entry that is an exception is raised instead of returned, which is
    how the "chunk 2 of 3 died" case is written.
    """

    def __init__(self, *script):
        self.script = list(script)
        self.requests: list[dict] = []
        self.messages = self

    def create(self, **kwargs):
        self.requests.append(kwargs)
        answer = self.script.pop(0) if self.script else FakeResponse([])
        if isinstance(answer, Exception):
            raise answer
        return answer

    @property
    def calls(self) -> int:
        return len(self.requests)

    def source_text(self, index: int) -> str:
        """The document text shown on the ``index``-th request."""
        content = self.requests[index]["messages"][0]["content"]
        return content.split("<source>\n", 1)[1].rsplit("\n</source>", 1)[0]


def make_document(text: str, **overrides) -> HarvestedDocument:
    fields = {
        "url": "https://drive.google.com/file/d/1MCaaEXLvc4UErDuRhRff5JJRsJrbVu0U/view",
        "kind": HarvestedDocument.Kind.TOR,
        "status": HarvestedDocument.Status.FETCHED,
        "text": text,
        "text_chars": len(text),
        "has_text_layer": True,
        "parser": "pypdf",
    }
    fields.update(overrides)
    fields.setdefault(
        "url_hash", hashlib.sha256(fields["url"].encode()).hexdigest()
    )
    return HarvestedDocument.objects.create(**fields)


# ---------------------------------------------------------------------------
class RefusalTests(TestCase):
    """What L3 declines to read, and never pays to find out."""

    def test_a_scan_is_refused_without_a_request(self):
        """"We cannot read this" is the harvester's answer, not one to re-derive.

        ``has_text_layer=False`` is a scanned document. Sending its empty text
        would buy an empty answer and record it as a model that found nothing,
        which is a different and much worse fact than a document nobody can
        read.
        """
        document = make_document("x" * 5000, has_text_layer=False)
        client = FakeClient()

        result = l3.extract(document, client=client)

        self.assertEqual(client.calls, 0)
        self.assertEqual(result.requirements, [])
        self.assertEqual(result.notes["refused"], "no_text_layer")
        self.assertIn("no text layer", result.error)

    def test_an_unfetched_document_is_refused_without_a_request(self):
        document = make_document(
            "", status=HarvestedDocument.Status.ACCESS_DENIED, has_text_layer=None
        )
        client = FakeClient()

        result = l3.extract(document, client=client)

        self.assertEqual(client.calls, 0)
        self.assertEqual(result.notes["refused"], "not_fetched")

    def test_a_document_below_the_useful_length_is_refused(self):
        """The harvester's own floor, not a second opinion about it."""
        document = make_document("Terms of Reference. Annex 1.")
        client = FakeClient()

        result = l3.extract(document, client=client)

        self.assertEqual(client.calls, 0)
        self.assertEqual(result.notes["refused"], "too_short")

    def test_a_document_stating_no_condition_costs_nothing(self):
        """Background prose is not an extraction failure; it is a measurement.

        This is also the cheapest guard against an expired share page: the
        mirror holds several stored as ``fetched`` with a text layer, because
        what arrived was a real HTML error screen.
        """
        document = make_document(BACKGROUND * 3)
        client = FakeClient()

        result = l3.extract(document, client=client)

        self.assertEqual(client.calls, 0)
        self.assertEqual(result.requirements, [])
        self.assertEqual(result.notes["refused"], "no chunk carried qualification language")
        # Not an error. Nothing failed.
        self.assertTrue(result.ok)

    def test_minified_asset_text_selects_nothing(self):
        """The largest 'TOR' in the mirror is 400k characters of CSS.

        It has a text layer, it fetched cleanly, and it contains no whitespace
        for a thousand characters at a time. It must cost nothing.
        """
        document = make_document(".Button__btn--38SD2{background-color:var(--vkui-accent)}" * 400)
        client = FakeClient()

        result = l3.extract(document, client=client)

        self.assertEqual(client.calls, 0)


# ---------------------------------------------------------------------------
class ChunkingTests(TestCase):
    """The boundaries, which decide whether a quote can be verified at all."""

    def setUp(self):
        self.text = canonical(f"{BACKGROUND} {QUALIFICATION_SECTION} {BACKGROUND}")
        self.units = l3._units(self.text)

    def _chunks(self, chunk_chars: int = 600, overlap_units: int = 2):
        return l3._chunk(
            self.text, self.units, chunk_chars=chunk_chars, overlap_units=overlap_units
        )

    def test_every_chunk_is_a_literal_slice_of_the_document(self):
        """The property the whole grounding story rests on.

        A chunk assembled by rejoining pieces is *nearly* the document, and
        "nearly" is what turns a correct extraction into a recorded
        hallucination. Slicing makes the two identical by construction.
        """
        for chunk in self._chunks():
            with self.subTest(chunk=chunk.index):
                self.assertIn(chunk.text, self.text)

    def test_a_chunk_never_begins_or_ends_inside_a_word(self):
        for chunk in self._chunks():
            with self.subTest(chunk=chunk.index):
                start = self.text.index(chunk.text)
                end = start + len(chunk.text)
                if start > 0:
                    self.assertTrue(self.text[start - 1].isspace())
                if end < len(self.text):
                    self.assertTrue(self.text[end].isspace() or chunk.text[-1].isspace())

    def test_every_sentence_survives_whole_in_some_chunk(self):
        """The invariant that makes a quote spanning a cut impossible.

        A sentence that no single chunk contains in full can still be extracted
        — the model sees half of it in each of two chunks — and the quote it
        returns is then unfindable in either. That reads as a hallucination in
        the accuracy table when the extraction was right and the chunker was
        wrong. Overlap exists for exactly this.
        """
        chunks = self._chunks()
        for sentence in sentences(self.text):
            with self.subTest(sentence=sentence[:60]):
                self.assertTrue(
                    any(sentence in chunk.text for chunk in chunks),
                    "no chunk carries this sentence whole",
                )

    def test_a_quote_at_a_chunk_boundary_is_verifiable_where_it_came_from(self):
        """Concretely, on the sentence the boundary actually falls on."""
        chunks = self._chunks()
        carriers = [c for c in chunks if contains_quote(c.text, REAL_QUOTE)]
        self.assertTrue(carriers)
        for chunk in carriers:
            self.assertTrue(contains_quote(chunk.text, REAL_QUOTE))
            # And what holds for the chunk holds for the document, because the
            # chunk is a slice of it. The pipeline re-verifies against the
            # document and must reach the same answer.
            self.assertTrue(contains_quote(self.text, REAL_QUOTE))

    def test_adjacent_chunks_overlap_rather_than_abut(self):
        """The repeated tail is what keeps a heading with the clause under it.

        The budget has to have room for it: with a chunk barely larger than the
        units it holds, the overlap is dropped rather than allowed to stall the
        walk, so this is asserted where it is meant to apply.
        """
        chunks = self._chunks(chunk_chars=1800, overlap_units=2)
        self.assertGreater(len(chunks), 1)
        for earlier, later in zip(chunks, chunks[1:]):
            end = self.text.index(earlier.text) + len(earlier.text)
            self.assertLess(self.text.index(later.text), end)

    def test_text_with_no_sentence_boundaries_is_still_broken_up(self):
        """HTML rows arrive as one blob: ``html_to_text`` marks paragraphs with
        newlines and ``canonical`` collapses them, so a page whose paragraphs
        lack full stops has a single 'sentence' of any length."""
        blob = "word " * 2000
        units = l3._units(canonical(blob))
        self.assertGreater(len(units), 1)
        self.assertTrue(all(end - start <= l3.MAX_UNIT_CHARS for start, end in units))

    def test_an_overlap_too_large_for_the_next_unit_does_not_stall(self):
        """A parameter combination must never hang the worker.

        Stepping back by more than the chunk can hold would re-emit the same
        window forever. The overlap is shrunk instead — continuity lost on one
        boundary, which is recoverable; a hung Celery task is not.
        """
        chunks = l3._chunk(self.text, self.units, chunk_chars=300, overlap_units=20)
        self.assertGreater(len(chunks), 1)


# ---------------------------------------------------------------------------
class SelectionTests(TestCase):
    """Which parts of a document are worth a request."""

    def test_the_qualification_section_outscores_the_background(self):
        text = canonical(f"{BACKGROUND} {QUALIFICATION_SECTION}")
        chunks = l3._score(
            l3._chunk(text, l3._units(text), chunk_chars=900, overlap_units=1)
        )
        best = max(chunks, key=lambda c: c.score)
        self.assertIn("Qualification Requirements", best.text)
        self.assertGreaterEqual(best.score, l3.DEFAULT_MIN_SCORE)

    def test_a_russian_document_is_not_silently_skipped(self):
        """An English-only prefilter returns nothing for most of a CIS corpus.

        Measured: with English patterns alone, 19 of the 20 Cyrillic Terms of
        Reference in the mirror selected no chunk at all. That is the failure
        hardest to see from an accuracy table, because it looks like a model
        that found nothing.
        """
        russian = (
            "ТЕХНИЧЕСКОЕ ЗАДАНИЕ. Квалификационные требования к консультанту. "
            "Консультант должен иметь высшее образование в области экономики и "
            "опыт работы не менее пяти лет по аналогичным проектам, а также "
            "сертификат международной ассоциации аудиторов. "
        ) * 3
        document = make_document(russian)
        client = FakeClient(FakeResponse([]))

        l3.extract(document, client=client)

        self.assertGreaterEqual(client.calls, 1)

    def test_selection_keeps_document_order(self):
        text = canonical(f"{QUALIFICATION_SECTION} {BACKGROUND} {QUALIFICATION_SECTION}")
        chunks = l3._score(
            l3._chunk(text, l3._units(text), chunk_chars=700, overlap_units=1)
        )
        selected = l3._select(chunks, max_chunks=3, min_score=2)
        self.assertEqual([c.index for c in selected], sorted(c.index for c in selected))

    def test_the_chunk_cap_is_recorded_rather_than_applied_silently(self):
        document = make_document(f"{QUALIFICATION_SECTION} " * 12)
        client = FakeClient(*[FakeResponse([]) for _ in range(20)])

        result = l3.extract(document, client=client, **CHUNKED, chunk_chars=700, max_chunks=2)

        self.assertEqual(client.calls, 2)
        self.assertTrue(result.notes["chunk_cap_reached"])
        self.assertGreater(result.notes["chunks_skipped"], 0)


# ---------------------------------------------------------------------------
class ExtractionTests(TestCase):
    """What comes back, and what is attached to it."""

    def setUp(self):
        self.document = make_document(
            f"{BACKGROUND} {QUALIFICATION_SECTION} {BACKGROUND}"
        )

    def test_a_human_can_find_the_quote_from_what_is_recorded(self):
        """``source`` names the chunk and ``source_document_id`` names the file.

        Without both, "show me where this came from" — the affordance D4 says
        the product is sold on — is a search of a forty-page PDF.
        """
        client = FakeClient(FakeResponse([_requirement()]))

        result = l3.extract(self.document, client=client)

        [requirement] = result.requirements
        self.assertRegex(requirement.source, r"^tor:chunk \d+/\d+$")
        self.assertEqual(requirement.source_document_id, self.document.pk)

    def test_the_same_criterion_in_two_chunks_yields_one_requirement(self):
        """A summary table restating the body, and the overlap window, both
        produce this. The first occurrence wins: it is the chunk the user is
        sent to."""
        client = FakeClient(
            FakeResponse([_requirement()]),
            FakeResponse([_requirement()]),
            FakeResponse([_requirement()]),
        )

        result = l3.extract(self.document, client=client, **CHUNKED, chunk_chars=700)

        self.assertGreater(client.calls, 1)
        self.assertEqual(len(result.requirements), 1)
        self.assertEqual(result.notes["duplicates_dropped"], client.calls - 1)

    def test_the_same_key_with_a_different_quote_is_kept(self):
        """Two experts can both need five years, and both statements are real."""
        other = REAL_QUOTE.replace("five (5)", "ten (10)")
        client = FakeClient(FakeResponse([_requirement(), _requirement(quote=other)]))

        result = l3.extract(self.document, client=client)

        self.assertEqual(len(result.requirements), 2)
        self.assertNotIn("duplicates_dropped", result.notes)

    def test_a_key_a_cheaper_layer_already_read_is_dropped(self):
        client = FakeClient(FakeResponse([_requirement(key="bid_security")]))

        result = l3.extract(self.document, client=client, exclude_keys=["bid_security"])

        self.assertEqual(result.requirements, [])
        self.assertEqual(result.notes["excluded_keys_dropped"], 1)

    def test_excluded_keys_are_also_named_in_the_instruction(self):
        client = FakeClient(FakeResponse([]))

        l3.extract(self.document, client=client, exclude_keys=["bid_security"])

        self.assertIn("bid_security", client.requests[0]["messages"][0]["content"])

    def test_an_unfindable_quote_is_counted_but_not_deleted(self):
        """``Grounding.NOT_FOUND`` exists to hold exactly this row.

        Dropping it here would delete the evidence before the pipeline ever saw
        it, and the hallucination rate — the number D4 says the approach is
        judged on — would read as zero for the layer most able to produce one.
        An empty quote is different and is dropped upstream in ``llm.py``: that
        is a model declining to claim, not a model claiming something the
        source does not say.
        """
        invented = "The Consultant shall hold a valid ISO 37001 certification."
        client = FakeClient(FakeResponse([_requirement(quote=invented)]))

        result = l3.extract(self.document, client=client)

        self.assertEqual(len(result.requirements), 1)
        self.assertEqual(result.notes["quotes_unverified"], 1)

    def test_only_the_selected_text_is_sent(self):
        """What is not sent cannot be quoted — so it must be worth sending."""
        client = FakeClient(FakeResponse([]), FakeResponse([]), FakeResponse([]))

        result = l3.extract(self.document, client=client, **CHUNKED, chunk_chars=700)

        self.assertIn(
            "Qualification Requirements",
            "".join(client.source_text(i) for i in range(client.calls)),
        )
        self.assertLess(result.notes["selected_chars"], result.notes["document_chars"])
        self.assertGreater(result.notes["chunks_skipped"], 0)


# ---------------------------------------------------------------------------
class DegradationTests(TestCase):
    """A failure part way through must not discard what was already read."""

    def setUp(self):
        self.document = make_document(f"{QUALIFICATION_SECTION} " * 6)

    def test_a_failure_on_a_later_chunk_keeps_the_earlier_ones(self):
        client = FakeClient(
            FakeResponse([_requirement()]),
            RuntimeError("connection reset"),
            FakeResponse([_requirement(key="never_reached")]),
        )

        result = l3.extract(self.document, client=client, **CHUNKED, chunk_chars=700, max_chunks=6)

        self.assertEqual([r.key for r in result.requirements], ["years_experience"])
        self.assertIn("connection reset", result.error)
        self.assertFalse(result.ok)

    def test_reading_stops_at_the_first_failure(self):
        """An error here is almost always a condition of the run — no key, a
        rate limit, a timeout — not of this chunk's text. Continuing would
        multiply one systemic failure by the chunk cap."""
        client = FakeClient(
            FakeResponse([]),
            RuntimeError("rate limited"),
            FakeResponse([]),
            FakeResponse([]),
        )

        result = l3.extract(self.document, client=client, **CHUNKED, chunk_chars=700, max_chunks=6)

        self.assertEqual(client.calls, 2)
        self.assertGreaterEqual(result.notes["chunks_aborted"], 1)

    def test_a_refusal_is_recorded_with_what_was_already_extracted(self):
        refused = FakeResponse([])
        refused.stop_reason = "refusal"
        client = FakeClient(FakeResponse([_requirement()]), refused)

        result = l3.extract(self.document, client=client, **CHUNKED, chunk_chars=700)

        self.assertEqual(len(result.requirements), 1)
        self.assertIn("declined", result.error)


# ---------------------------------------------------------------------------
class AccountingTests(TestCase):
    """The cost fields exist for the D6 ablation and are filled in as spent."""

    def test_tokens_costs_and_chunk_counts_add_up_across_chunks(self):
        document = make_document(f"{QUALIFICATION_SECTION} " * 6)
        client = FakeClient(
            FakeResponse([], tokens=(1000, 200)),
            FakeResponse([], tokens=(1500, 300)),
            FakeResponse([], tokens=(500, 100)),
        )

        result = l3.extract(document, client=client, **CHUNKED, chunk_chars=700, max_chunks=3)

        self.assertEqual(client.calls, 3)
        self.assertEqual(result.notes["chunks_read"], 3)
        self.assertEqual(result.input_tokens, 3000)
        self.assertEqual(result.output_tokens, 600)
        self.assertGreater(result.cost_usd, Decimal("0"))
        self.assertEqual(result.model, "claude-haiku-4-5")
        self.assertEqual(result.prompt_version, llm.PROMPT_VERSION)

    def test_a_refused_document_records_no_spend(self):
        document = make_document("x" * 5000, has_text_layer=False)
        result = l3.extract(document, client=FakeClient())
        self.assertEqual(result.input_tokens, 0)
        self.assertEqual(result.cost_usd, Decimal("0"))


# ---------------------------------------------------------------------------
class BestDocumentTests(TestCase):
    """Which of a notice's several files L3 should be pointed at."""

    def setUp(self):
        self.notice = TenderNotice.objects.create(
            notice_id="OP00456288", bid_description="Rogun E&S Consultant"
        )

    def _attach(self, url: str, kind: str, text: str, **overrides):
        document = make_document(text, url=url, kind=kind, **overrides)
        document.notices.add(self.notice)
        return document

    def test_the_terms_of_reference_wins_over_a_project_document(self):
        self._attach("https://x/pad.pdf", HarvestedDocument.Kind.PROJECT_DOC,
                     BACKGROUND * 5)
        tor = self._attach("https://x/tor.pdf", HarvestedDocument.Kind.TOR,
                           QUALIFICATION_SECTION)
        self.assertEqual(l3.best_document(self.notice), tor)

    def test_the_fuller_parse_wins_between_two_copies(self):
        """Borrowers re-upload the same file; the short copy is the truncated
        parse, not a different document."""
        self._attach("https://x/short.pdf", HarvestedDocument.Kind.TOR,
                     QUALIFICATION_SECTION)
        full = self._attach("https://x/full.pdf", HarvestedDocument.Kind.TOR,
                            QUALIFICATION_SECTION * 4)
        self.assertEqual(l3.best_document(self.notice), full)

    def test_an_unreadable_attachment_is_never_returned(self):
        self._attach("https://x/scan.pdf", HarvestedDocument.Kind.TOR,
                     QUALIFICATION_SECTION, has_text_layer=False)
        self.assertIsNone(l3.best_document(self.notice))


# ---------------------------------------------------------------------------
class WholeDocumentTests(TestCase):
    """A document that fits is sent whole, in one request.

    The prefilter it replaces chose at most six 6 000-character chunks by
    counting keyword families, which made any criterion phrased outside that
    vocabulary invisible rather than wrong. These cases pin the new default and
    the two properties that had to survive it: a document with no qualification
    language anywhere still costs nothing, and a document too large to send
    still falls back to chunking.
    """

    def test_a_document_that_fits_is_sent_in_one_request(self):
        document = make_document(BACKGROUND + " " + QUALIFICATION_SECTION)
        client = FakeClient()

        result = l3.extract(document, client=client)

        self.assertEqual(client.calls, 1)
        self.assertIs(result.notes["whole_document"], True)
        self.assertEqual(result.notes["chunks_selected"], 1)

    def test_the_background_prose_is_sent_too_not_just_the_scoring_part(self):
        """The point of the change: what is not sent cannot be quoted, and the
        prefilter decided that at a granularity it had no evidence for."""
        document = make_document(BACKGROUND + " " + QUALIFICATION_SECTION)
        client = FakeClient()

        l3.extract(document, client=client)

        sent = client.source_text(0)
        self.assertIn("Vakhsh Cascade", sent)
        self.assertIn("At least five (5) years of experience", sent)

    def test_every_character_of_the_document_reaches_the_model(self):
        document = make_document(BACKGROUND + " " + QUALIFICATION_SECTION)
        client = FakeClient()

        result = l3.extract(document, client=client)

        self.assertEqual(
            result.notes["selected_chars"], result.notes["document_chars"]
        )

    def test_a_document_with_no_qualification_language_still_costs_nothing(self):
        """The half of the old design that was actually earning is kept.

        An expired share page and a pure scope-of-work TOR must not become paid
        requests just because the prefilter stopped choosing within documents.
        """
        document = make_document(BACKGROUND * 3)
        client = FakeClient()

        result = l3.extract(document, client=client)

        self.assertEqual(client.calls, 0)
        self.assertEqual(result.requirements, [])
        self.assertEqual(result.notes["chunks_selected"], 0)

    def test_a_document_too_large_to_send_falls_back_to_chunking(self):
        document = make_document(QUALIFICATION_SECTION * 40)
        client = FakeClient()

        result = l3.extract(
            document, client=client, whole_document_chars=1000, chunk_chars=2000
        )

        self.assertIs(result.notes["whole_document"], False)
        self.assertGreater(client.calls, 1)
        self.assertLess(result.notes["selected_chars"], result.notes["document_chars"])

    def test_the_ceiling_is_measured_against_canonical_text(self):
        """`canonical` collapses the whitespace `pypdf` invents, so the decision
        has to be made after that — not against the raw column."""
        body = BACKGROUND + " " + QUALIFICATION_SECTION
        padded = body.replace(" ", "     ")
        document = make_document(padded)
        client = FakeClient()

        result = l3.extract(document, client=client, whole_document_chars=len(body) + 50)

        self.assertIs(result.notes["whole_document"], True)
        self.assertEqual(client.calls, 1)
