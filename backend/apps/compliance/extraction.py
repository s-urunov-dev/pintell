"""The contract every extraction layer answers to.

L1, L2 and L3 read different sources with wildly different machinery — a
regular expression over a notice body, a language model with a JSON schema, a
chunked walk through a mirrored PDF. What makes the layering measurable rather
than merely tidy is that all three hand back the *same* shape, and that
nothing downstream can tell which one produced a given row except by reading
its ``layer`` field.

Two rules are enforced here rather than trusted to each layer:

* **A layer proposes; it never persists.** ``LayerResult`` is a plain dataclass
  with no Django import. A layer that cannot reach its model, or that gets a
  malformed answer, returns a result carrying ``error`` — it does not raise,
  and it does not decide what that means for the notice. The pipeline records
  the run either way, which is what keeps a failed L2 from erasing a good L1.

* **A proposal without a quote is already invalid.** ``evidence_quote`` is not
  optional metadata; it is the thing the grounding verifier searches for in the
  source. A layer that cannot copy a verbatim sentence out of what it just read
  should return nothing for that requirement rather than a paraphrase, because
  a paraphrase fails grounding and is recorded as a hallucination — correctly,
  but for the wrong reason.

The cost fields exist for DECISIONS.md D6's ablation table. They are filled in
by the layer that spent the money, at the moment it spent it, because a cost
reconstructed later from logs is not evidence.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any


@dataclass
class Extracted:
    """One requirement a layer proposes, before anything has been verified.

    Deliberately not ``expressions.Requirement``: that type carries a parsed
    ``Node`` and refuses to hold an unparseable tree, which is the wrong
    behaviour on the way *in*. A model can return a tree that does not parse,
    and the pipeline needs to count that as a malformed extraction rather than
    crash inside the layer. So ``expression`` stays raw here and is parsed —
    and rejected if it will not parse — at the boundary.
    """

    #: Taxonomy key: ``annual_turnover_avg``, ``similar_contracts_count``.
    key: str
    #: ``expressions.Node.to_dict()`` shape. Raw — see the class docstring.
    expression: dict[str, Any]
    label: str = ""
    applies_to: str = "single"
    is_mandatory: bool = True
    #: Copied verbatim from the source. Never paraphrased, never tidied.
    evidence_quote: str = ""
    #: Where the quote came from: ``notice_body``, or a page reference.
    source: str = ""
    #: ``HarvestedDocument.pk`` when the quote came from a mirrored document.
    source_document_id: str | None = None

    #: ``high`` / ``medium`` / ``low`` — how much of the bid this criterion
    #: decides, read off the *document's own* signals: whether it is an
    #: eligibility gate, whether failing it disqualifies, whether the text calls
    #: it desirable rather than required.
    #:
    #: **This never touches a verdict**, and the separation is the reason the
    #: field is safe to have at all. "The model reads; it never decides" is
    #: about eligibility: whether a bidder qualifies is computed by
    #: ``expressions.py`` from thresholds and declared values, and importance is
    #: not an input to that computation anywhere. What it does change is the
    #: order the criteria are shown in and the weight they carry in the
    #: readiness percentage — both presentation of a set of verdicts already
    #: reached, never one of the verdicts.
    #:
    #: Empty is a real value: a layer with no basis for the judgement (L1, whose
    #: rules match a number next to a topic word and read no surrounding
    #: language) leaves it blank rather than guessing ``medium``, and the
    #: default lands at the boundary instead. See ``models.Importance``.
    importance: str = ""

    #: The criterion named in Uzbek and Russian. ``label`` stays the English
    #: one, which is also the source language of every document in the mirror.
    #:
    #: Only the *label* is translated, and that boundary is load-bearing:
    #: ``evidence_quote`` is what the grounding verifier searches for in the
    #: source, so a translated quote would fail to match its own document and be
    #: recorded as a hallucination that never happened. A vendor therefore reads
    #: the criterion in their language and checks it against the sentence in the
    #: borrower's — which is the correct pairing, since the sentence is the part
    #: that has to be verifiable against the tender.
    label_uz: str = ""
    label_ru: str = ""


@dataclass
class ExtractedExpert:
    """One expert position a layer proposes the tender is asking for.

    A consulting tender names the people the assignment needs — "the team shall
    include a Team Leader with 15 years of experience and one Resettlement
    Specialist" — and a firm without them cannot bid at all. That sentence
    states a requirement, but not one ``expressions.py`` can evaluate: nothing
    in a vendor profile answers "do you have this person", and pretending
    otherwise would put a verdict on data we do not hold.

    So a position travels beside the requirements rather than among them. It
    obeys the same evidence rule — a quote or it is not proposed — and it
    reaches a different table, a different API field, and no verdict at all.

    ``role_slug`` is already a key in the expert taxonomy, or the ``UNMAPPED``
    sentinel. It arrives that way because the extraction schema constrains it to
    that vocabulary (``apps.experts.vocabulary``); there is deliberately no
    matching step here that could get it wrong.
    """

    #: The position as the document writes it, verbatim. Kept even when the role
    #: resolves, because "Senior Resettlement and Livelihoods Specialist" says
    #: more to a vendor than the taxonomy row it files under.
    title: str
    #: A role slug, or ``vocabulary.UNMAPPED``. Never free text.
    role_slug: str = ""
    #: How many of this position the document asks for. 1 unless it says more.
    count: int = 1
    #: False only where the document itself marks the position optional or
    #: desirable rather than required.
    is_mandatory: bool = True
    #: Copied verbatim from the source. Same rule as ``Extracted``, and for the
    #: same reason: a position nobody wrote down is not a position.
    evidence_quote: str = ""
    source: str = ""
    source_document_id: str | None = None


@dataclass
class LayerResult:
    """What one layer returns for one notice.

    ``error`` being non-empty does not mean ``requirements`` is empty: a
    document-parsing layer can read three chunks and fail on the fourth, and
    throwing away the three would be worse than recording a partial run.
    """

    requirements: list[Extracted] = field(default_factory=list)

    #: Expert positions read out of the same text, in the same call. Empty for
    #: L1, which has no rule for them: a position is named in prose that varies
    #: per borrower, which is what a keyword rule is worst at.
    experts: list[ExtractedExpert] = field(default_factory=list)

    #: Empty for deterministic layers. That emptiness is the point of L1:
    #: the product answers something with no API key at all.
    model: str = ""
    #: Identifies the prompt, so a change in quality is attributable.
    prompt_version: str = ""

    input_tokens: int = 0
    output_tokens: int = 0
    cost_usd: Decimal = Decimal("0")
    duration_ms: int = 0

    #: Set when the layer degraded. Recorded on the run; never raised.
    error: str = ""

    #: Free-form counters a layer wants in the ablation (chunks read, quotes
    #: discarded before returning, requests batched). Not schema — notes.
    notes: dict[str, Any] = field(default_factory=dict)

    @property
    def ok(self) -> bool:
        return not self.error

    def merge(self, other: "LayerResult") -> "LayerResult":
        """Fold another result of the *same* layer into this one.

        Used where one layer makes several calls for one notice — L3 over a
        multi-chunk document, most obviously. Costs add; the first error wins,
        because the first failure is the one with the useful message.
        """
        self.requirements.extend(other.requirements)
        self.experts.extend(other.experts)
        self.input_tokens += other.input_tokens
        self.output_tokens += other.output_tokens
        self.cost_usd += other.cost_usd
        self.duration_ms += other.duration_ms
        self.model = self.model or other.model
        self.prompt_version = self.prompt_version or other.prompt_version
        self.error = self.error or other.error
        for name, value in other.notes.items():
            if isinstance(value, int) and isinstance(self.notes.get(name), int):
                self.notes[name] += value
            else:
                self.notes[name] = value
        return self
