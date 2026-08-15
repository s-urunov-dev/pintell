"""Storage for what a tender requires and what a vendor has.

``expressions.py`` deliberately imports no Django: it evaluates plain
dataclasses so the semantics can be tested without a database. This module is
the other half — it persists those dataclasses and nothing more. Every model
here maps onto a structure that already exists in the engine, and the
translation is mechanical (``Requirement.to_dict`` / ``parse_requirement``,
``Portfolio``).

Two choices are worth stating, because the obvious alternative was rejected.

**The expression tree is stored as JSON, not as rows.** A requirement is a tree
of counts, filters and comparisons whose shape varies per criterion; modelling
it relationally would mean a node table, an edge table, and a join for every
evaluation, to gain a query nobody runs — the engine always loads the whole
requirement. The engine's own serialisation round-trips exactly, so JSON keeps
one representation instead of two.

**A vendor's portfolio is also JSON, and that one is temporary.** It mirrors
``Portfolio.scalars`` / ``Portfolio.collections`` with zero impedance, which is
what makes the first UI cheap to build. It is the wrong shape for the feed
query in DECISIONS.md D8, which wants denormalised, indexed hard-gate columns.
Those columns get added when the feed is built; until a vendor can enter data
there is nothing to denormalise.
"""

from __future__ import annotations

from django.db import models

from apps.tenders.models import HarvestedDocument, TenderNotice


class ExtractionRun(models.Model):
    """One pass of the extraction stack over one notice.

    This table exists for the evaluation, not for the product. DECISIONS.md D6
    promises an ablation table — recall, precision, grounding rate, cost and
    latency for L1 / L1+L2 / L1+L2+L3, crossed with model tier. That table
    is only defensible if the numbers behind it are recorded per run at the
    moment the run happened, rather than reconstructed later from logs or
    remembered from a notebook.

    So a run keeps its own layer set, model and cost even when a later run
    supersedes it. Rows are never overwritten; ``TenderRequirement.run`` says
    which pass produced a given requirement.
    """

    class Status(models.TextChoices):
        OK = "ok", "Completed"
        # The stack degrades rather than raising: a missing API key, a refused
        # request or a malformed response records a failed run and leaves
        # whatever earlier layers produced. Same contract as the harvester.
        FAILED = "failed", "Failed"

    notice = models.ForeignKey(
        TenderNotice, on_delete=models.CASCADE, related_name="extraction_runs"
    )

    #: Which layers took part, in order — ``"L1"``, ``"L1,L2"``, ``"L1,L2,L3"``.
    #: A string rather than a set of booleans because the ablation groups by it
    #: directly, and because the order of application is part of what is being
    #: measured.
    layers = models.CharField(max_length=32)

    #: Empty when no language model was involved. L1 is deterministic, which is
    #: the point of putting it first: the product answers something with no API
    #: key at all.
    model = models.CharField(max_length=64, blank=True)
    #: Identifies the prompt the run used, so a change in extraction quality can
    #: be attributed to the prompt rather than guessed at.
    prompt_version = models.CharField(max_length=32, blank=True)

    input_tokens = models.PositiveIntegerField(default=0)
    output_tokens = models.PositiveIntegerField(default=0)
    #: Six decimal places because a single Haiku extraction costs a fraction of
    #: a cent, and the ablation compares tiers whose costs differ by 20×.
    cost_usd = models.DecimalField(max_digits=12, decimal_places=6, default=0)
    duration_ms = models.PositiveIntegerField(default=0)

    status = models.CharField(max_length=8, choices=Status.choices, default=Status.OK)
    error = models.TextField(blank=True)

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["notice", "-created_at"]),
            models.Index(fields=["layers", "model"]),
        ]
        ordering = ["-created_at"]

    def __str__(self) -> str:
        return f"{self.notice_id} [{self.layers}{' ' + self.model if self.model else ''}]"


class TenderRequirement(models.Model):
    """One qualification criterion extracted from one notice.

    The persisted form of ``expressions.Requirement``. Read back with
    ``expressions.parse_requirement(row.as_payload())``.
    """

    class Layer(models.TextChoices):
        # There is deliberately no L0. An earlier design had one: standard
        # requirements implied by the procurement method, read out of the
        # Procurement Regulations. It was dropped (DECISIONS.md D17) because a
        # requirement nobody wrote into this tender's documents is not a fact
        # about this tender, and because the layer's evidence would have been a
        # clause reference rather than a quote — the one kind of row the
        # grounding verifier could never check.
        L1 = "L1", "Deterministic rule over the notice body"
        L2 = "L2", "LLM extraction from the notice body"
        L3 = "L3", "Parsed from a tender document"

    class Importance(models.TextChoices):
        """How much of the bid one criterion decides.

        Read off the document rather than assigned by us: an eligibility gate
        the text says a bidder must pass is ``high``, a stated preference or an
        advantage is ``low``, and everything the document requires without
        marking it either way is ``medium``.

        **Nothing here reaches a verdict.** Eligibility is computed by
        ``expressions.py`` from thresholds and declared values and has no
        importance input; this field orders the criteria a vendor is shown and
        weights the readiness percentage, which is a summary *of* verdicts
        rather than one of them. Keeping it out of the engine is what stops a
        model's judgement about emphasis turning into a judgement about who may
        bid.

        ``UNSET`` is the value for a layer that has no basis for the judgement.
        L1 matches a number beside a topic word and reads none of the language
        that would say whether the criterion gates the bid, so it leaves this
        blank; the scoring treats blank as ``medium`` in one documented place
        (``scoring.WEIGHTS``) rather than having every layer guess.
        """

        HIGH = "high", "Decides eligibility"
        MEDIUM = "medium", "Required, not marked either way"
        LOW = "low", "Stated as a preference or an advantage"
        UNSET = "", "Not judged"

    class Grounding(models.TextChoices):
        # The quote was found verbatim in the source text.
        VERIFIED = "verified", "Quote found in source"
        # The quote was not found. The requirement is kept but must never reach
        # a verdict: this is the hallucination signal, and deleting the row
        # would destroy the measurement it exists to produce.
        NOT_FOUND = "not_found", "Quote not found — do not use"
        # No `exempt` state either, for the same reason as no L0: it existed
        # only for rows whose evidence was a regulation clause. Every remaining
        # layer reads a document we hold, so every row can be checked against
        # the text it came from, and a state meaning "grounding does not apply"
        # would now only ever be a way to opt out of the measurement.
        UNCHECKED = "unchecked", "Not verified yet"

    notice = models.ForeignKey(
        TenderNotice, on_delete=models.CASCADE, related_name="requirements"
    )
    run = models.ForeignKey(
        ExtractionRun, on_delete=models.CASCADE, related_name="requirements"
    )
    layer = models.CharField(max_length=2, choices=Layer.choices, db_index=True)

    #: Taxonomy key — ``annual_turnover_avg``, ``similar_contracts_count``. Not
    #: a foreign key to a taxonomy table: the taxonomy is still being derived
    #: from the gold set, and freezing it in a schema before the data is read
    #: is exactly the mistake the free-form annotation round exists to avoid.
    key = models.CharField(max_length=64, db_index=True)
    label = models.CharField(max_length=200, blank=True)

    #: The same criterion named in the two other languages the product ships
    #: in. Blank where the layer did not produce one — L1 writes English only,
    #: and a row extracted before the schema asked for translations keeps
    #: whatever it had. ``label_for`` falls back rather than showing a blank.
    #:
    #: Deliberately three columns rather than a JSON blob keyed by language:
    #: the set of languages is fixed by the product (uz/en/ru), a column is
    #: what a list view can order by, and a blob would invite a fourth language
    #: to arrive without anyone deciding to support one.
    label_uz = models.CharField(max_length=200, blank=True)
    label_ru = models.CharField(max_length=200, blank=True)

    #: ``expressions.Node.to_dict()`` — the whole tree.
    expression = models.JSONField()

    applies_to = models.CharField(max_length=16, default="single")
    is_mandatory = models.BooleanField(default=True)

    #: See ``Importance``. Indexed because the vendor's list is ordered by it
    #: and the operator console groups on it.
    importance = models.CharField(
        max_length=6,
        choices=Importance.choices,
        default=Importance.UNSET,
        blank=True,
        db_index=True,
    )

    #: Copied from the source, never paraphrased. The grounding verifier
    #: searches for this string in the source text; a quote that has been
    #: tidied, translated or shortened will not be found, and the requirement
    #: is then marked NOT_FOUND rather than silently trusted.
    evidence_quote = models.TextField(blank=True)

    #: Where the quote came from: ``notice_body``, or a chunk reference within
    #: ``source_document``.
    source = models.CharField(max_length=120, blank=True)
    source_document = models.ForeignKey(
        HarvestedDocument,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="requirements",
    )

    grounding = models.CharField(
        max_length=12, choices=Grounding.choices, default=Grounding.UNCHECKED
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        indexes = [
            models.Index(fields=["notice", "layer"]),
            models.Index(fields=["grounding"]),
        ]
        ordering = ["notice_id", "layer", "key"]

    def __str__(self) -> str:
        return f"{self.key} ({self.layer}) for {self.notice_id}"

    @property
    def is_usable(self) -> bool:
        """Whether this requirement may take part in a verdict.

        A requirement whose quote could not be found in the source is evidence
        of a bad extraction, not evidence about the tender. It stays in the
        table so the hallucination rate stays measurable, and stays out of
        every assessment.
        """
        return self.grounding != self.Grounding.NOT_FOUND

    def label_for(self, lang: str) -> str:
        """The criterion's name in one language, falling back rather than blank.

        English is the fallback for both other languages because it is the
        language every source document is written in — so an untranslated row
        shows the criterion as the tender itself words it, which is worse to
        read and impossible to misunderstand. The key is the last resort, for
        rows whose layer produced no label at all.
        """
        translated = {"uz": self.label_uz, "ru": self.label_ru}.get(lang, "")
        return translated or self.label or self.key

    def as_payload(self) -> dict:
        """The shape ``expressions.parse_requirement`` expects."""
        return {
            "key": self.key,
            "label": self.label,
            "expression": self.expression,
            "applies_to": self.applies_to,
            "is_mandatory": self.is_mandatory,
            "evidence_quote": self.evidence_quote,
            "source": self.source,
        }


class TenderExpertPosition(models.Model):
    """One expert position a tender requires the bidder's team to include.

    Extracted from the same text, in the same call, by the same layers as a
    requirement (D20) — and kept in a different table, which is the decision
    this class exists to record.

    A requirement is evaluable: it becomes an ``expressions`` tree, meets a
    vendor's declared portfolio, and produces satisfied / failed / unknown. A
    position is not. Nothing in a vendor profile answers "do you have a
    Resettlement Specialist", so a position modelled as a requirement would be
    permanently UNKNOWN — a row that can never resolve, dragging every
    assessment it appears in to "incomplete" and teaching vendors to ignore the
    state that is supposed to mean *tell us one more thing*. So positions live
    beside the verdict, never inside it.

    What they obey identically is the evidence rule. A position carries the
    sentence that named it, that quote is verified against the source the same
    way, and a position whose quote cannot be found is kept and marked rather
    than deleted — same reason as D4, and the same measurement.
    """

    notice = models.ForeignKey(
        TenderNotice, on_delete=models.CASCADE, related_name="expert_positions"
    )
    run = models.ForeignKey(
        ExtractionRun, on_delete=models.CASCADE, related_name="expert_positions"
    )
    layer = models.CharField(max_length=2, choices=TenderRequirement.Layer.choices)

    #: The position as the document names it. Always populated — it is what the
    #: vendor reads, and it survives a taxonomy that cannot file it.
    title = models.CharField(max_length=200)

    #: The directory role this position maps to, or NULL when the taxonomy has
    #: no row for it. A foreign key here, unlike ``TenderRequirement.key``,
    #: because this taxonomy is not being derived from a gold set — it was
    #: written by the CEO, shipped as a fixture, and constrains the extraction
    #: schema itself, so the value is a key in that table before it ever
    #: arrives (``apps.experts.vocabulary``).
    #:
    #: SET_NULL rather than CASCADE: retiring a role from the directory must not
    #: delete the evidence that tenders were asking for it.
    role = models.ForeignKey(
        "experts.ExpertType",
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="tender_positions",
    )

    #: How many of this position the document asks for.
    count = models.PositiveSmallIntegerField(default=1)
    #: False only where the document itself calls the position desirable rather
    #: than required.
    is_mandatory = models.BooleanField(default=True)

    evidence_quote = models.TextField(blank=True)
    source = models.CharField(max_length=120, blank=True)
    source_document = models.ForeignKey(
        HarvestedDocument,
        on_delete=models.SET_NULL,
        null=True,
        blank=True,
        related_name="expert_positions",
    )
    grounding = models.CharField(
        max_length=12,
        choices=TenderRequirement.Grounding.choices,
        default=TenderRequirement.Grounding.UNCHECKED,
    )

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "tender expert position"
        verbose_name_plural = "tender expert positions"
        indexes = [
            models.Index(fields=["notice", "layer"]),
            models.Index(fields=["role"]),
        ]
        ordering = ["notice_id", "title"]

    def __str__(self) -> str:
        return f"{self.title} for {self.notice_id}"

    @property
    def is_usable(self) -> bool:
        """Whether this position may be shown to a vendor as a fact.

        Same rule as ``TenderRequirement.is_usable`` and for the same reason: a
        position whose quote is not in the source is evidence about the
        extraction, not about the tender.
        """
        return self.grounding != TenderRequirement.Grounding.NOT_FOUND


class ComplianceSpan(models.Model):
    """One line of one page of a mirrored PDF, with the rectangle it occupies.

    The index that lets a requirement point at the place in the document its
    quote came from. Built by ``spans.index_pdf`` from the stored bytes, once
    per document, and read by the viewer.

    **Attached to the document, not to the notice.** One Terms of Reference is
    routinely referenced by several notices of the same project — that is why
    ``HarvestedDocument`` is keyed by URL in the first place — and indexing it
    per notice would parse the same file several times and store the same
    rectangles under different owners.

    **A span carries no meaning.** It is not a requirement, not a claim and not
    a fragment of one: it is a line of text and where it sits. Which lines
    matter to which criterion is decided per request by matching the stored,
    already-verified quote against this index (``spans.Locator``), and is
    deliberately not a column here — a stored location that disagreed with its
    own quote is the one failure this feature must not be able to have.
    """

    document = models.ForeignKey(
        HarvestedDocument, on_delete=models.CASCADE, related_name="spans"
    )

    #: ``p3_l12``. Legible on purpose: it travels to the browser and appears in
    #: the DOM, and a person debugging a misplaced highlight can find the line
    #: in the document by eye from the id alone.
    span_id = models.CharField(max_length=32)

    page = models.PositiveSmallIntegerField()
    #: Position in the document as a whole. The viewer draws in page order and
    #: the locator walks in reading order, and neither can derive it from
    #: ``page`` alone.
    order = models.PositiveIntegerField()

    text = models.TextField()

    #: PDF points, origin at the **top left** — pdfplumber's ``top``/``bottom``
    #: convention, which is also the browser's. Stored flat rather than as one
    #: JSON blob so a future query can ask about geometry; the API assembles
    #: them into a ``bbox`` object because that is what the viewer wants.
    x0 = models.FloatField()
    top = models.FloatField()
    x1 = models.FloatField()
    bottom = models.FloatField()

    #: The page's own size, repeated per span. The client renders the page at
    #: whatever width the window allows and needs the original to scale the
    #: rectangle; carrying it here means the payload has no second shape.
    page_width = models.FloatField()
    page_height = models.FloatField()

    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        verbose_name = "compliance span"
        verbose_name_plural = "compliance spans"
        constraints = [
            models.UniqueConstraint(
                fields=["document", "span_id"], name="one_span_per_document_id"
            )
        ]
        indexes = [models.Index(fields=["document", "order"])]
        ordering = ["document_id", "order"]

    def __str__(self) -> str:
        return f"{self.span_id} of {self.document_id}"


class VendorProfile(models.Model):
    """What one vendor declares about itself.

    Maps onto ``expressions.Portfolio``: ``scalars`` are single declared values,
    ``collections`` are the records a count or an ``exists`` runs over. An
    absent key means *not declared*, which is what makes a requirement evaluate
    to UNKNOWN rather than to a failure — so a partially filled profile is a
    normal state here, not an invalid one.

    Joint ventures are a bid-time composition of several profiles
    (``expressions.Bid``), not a property of a profile. The UI for building one
    is deferred; the engine already handles it.
    """

    #: The account this profile belongs to. One profile per vendor account, so
    #: the API never has to be told *which* profile — it reads the session.
    #:
    #: That is a security property, not a convenience. Before accounts existed,
    #: a profile was addressed by a sequential integer in the URL, which made
    #: one vendor's declared finances readable by anyone who could count
    #: (docs/OPEN-QUESTIONS.md Q8). Nullable because profiles created before accounts
    #: existed have no owner and are kept rather than deleted; they are
    #: unreachable through the API, which is the correct outcome for a record
    #: nobody can prove they own.
    user = models.OneToOneField(
        "auth.User",
        on_delete=models.CASCADE,
        related_name="vendor_profile",
        null=True,
        blank=True,
    )

    name = models.CharField(max_length=255)
    country = models.CharField(max_length=100, blank=True)

    #: ``{"annual_turnover_avg": 12000000, "iso_9001": true}``
    scalars = models.JSONField(default=dict, blank=True)
    #: ``{"contracts": [...], "experts": [...], "certificates": [...]}``
    collections = models.JSONField(default=dict, blank=True)

    #: When the vendor agreed to us storing this. Recorded from the first
    #: version because consent that was never timestamped cannot be evidenced
    #: later.
    #: NEEDS_VERIFICATION: the retention period and the wording of the consent
    #: are open legal questions — see docs/OPEN-QUESTIONS.md. No retention
    #: or deletion logic is implemented until they are answered, rather than
    #: implementing a guessed policy that would have to be unwound.
    consented_at = models.DateTimeField(null=True, blank=True)

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    def as_portfolio_payload(self) -> dict:
        return {
            "name": self.name,
            "scalars": self.scalars or {},
            "collections": self.collections or {},
        }


class RequirementDeclaration(models.Model):
    """A vendor's own answer to one extracted requirement: yes or no.

    **Why this exists beside the profile rather than inside it.** The profile
    holds *values* — a turnover figure, a list of contracts — and the engine
    derives a verdict from them by arithmetic. That is the right shape for the
    criteria that are arithmetic, and the wrong shape for most of what tenders
    actually ask. "Advanced university degree in agricultural economics" is not
    a number a vendor can enter; it is a fact they either have or do not, and
    the only party who can answer is the vendor. Before this model the interface
    asked them to upload a document or enter a value for such a criterion, and a
    vendor who did neither was left at "not yet established" forever — which is
    the state most assessments were stuck in.

    **A declaration is a claim by the vendor about themselves, and that is a
    different kind of thing from an extracted requirement.** The rule that no
    claim reaches a verdict without a verbatim quote governs what we say *the
    tender demands* — a statement about a third party's document, which we must
    be able to prove. This is the vendor speaking about their own company, where
    the vendor is the authority and the evidence is their signature on the bid.
    Keeping the two in separate tables is what stops the distinction eroding:
    nothing here ever becomes a ``TenderRequirement``, and no verdict presents a
    declaration as though the tender document said it.

    The verdict therefore records *who decided* — see
    ``expressions.assess_with_declarations``. "You told us you hold this" and
    "we computed this from your declared turnover" are both legitimate answers
    and must never be shown as the same one.
    """

    profile = models.ForeignKey(
        VendorProfile, on_delete=models.CASCADE, related_name="declarations"
    )
    #: The specific extracted row, not the criterion in the abstract. Two
    #: layers can extract the same criterion into two rows with different keys
    #: (the corpus shows exactly this), and a vendor answering the one in front
    #: of them must not be assumed to have answered the other.
    requirement = models.ForeignKey(
        TenderRequirement, on_delete=models.CASCADE, related_name="declarations"
    )
    satisfied = models.BooleanField()

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "requirement"], name="one_declaration_per_requirement"
            )
        ]
        indexes = [models.Index(fields=["profile", "requirement"])]
        ordering = ["-updated_at"]

    def __str__(self) -> str:
        state = "yes" if self.satisfied else "no"
        return f"{self.profile_id} → {self.requirement_id}: {state}"


class ComplianceScore(models.Model):
    """How ready one vendor is for one tender, as the last computed figure.

    **This is a cache, and saying so is the point.** Every other table in this
    app is a record of something that happened — a run, an extraction, a claim a
    vendor made — and none of them is ever overwritten. This one is derived: it
    is exactly what ``scoring.score_rows`` returns for the declarations and
    requirements currently stored, it is recomputed and overwritten on every
    assessment, and deleting the whole table would lose nothing that cannot be
    rebuilt from the two it is derived from.

    So why store it at all, given the number is a pure function of data we
    already hold? Because the question "which of the tenders I am watching am I
    closest to being able to bid on" is a list, and answering it from the source
    tables means loading every requirement and every declaration for every
    notice and running the engine once per row. A column can be ordered by; an
    assessment cannot.

    Two consequences follow from it being a cache, and both are deliberate:

    * **It is never read to answer "am I eligible".** The assessment endpoint
      recomputes from ``expressions.py`` every time and writes the result here
      on the way out. A stale row can therefore be wrong in a list and can never
      be wrong in a verdict.
    * **A row's absence means nothing.** It means nobody has opened that
      assessment yet, not that the vendor scores zero.
    """

    profile = models.ForeignKey(
        VendorProfile, on_delete=models.CASCADE, related_name="scores"
    )
    notice = models.ForeignKey(
        TenderNotice, on_delete=models.CASCADE, related_name="vendor_scores"
    )

    #: Weight satisfied over total weight, in ``[0, 1]``. Stored as a fraction
    #: rather than a percentage for the reason ``scoring.Score`` returns one: a
    #: number already rounded for display cannot be re-derived, and this column
    #: is meant to be sorted and compared, not printed.
    score = models.FloatField(default=0.0)
    #: What ``score`` would reach if everything still unknown were satisfied.
    #: The gap to ``score`` is the work left; the gap from here to 1.0 is what
    #: has already been lost, and a single column cannot say both.
    ceiling = models.FloatField(default=0.0)

    #: The weights the two fractions divide, kept so a stored score can be
    #: checked against its own inputs without re-running the engine.
    earned = models.PositiveIntegerField(default=0)
    open_weight = models.PositiveIntegerField(default=0)
    lost = models.PositiveIntegerField(default=0)
    total = models.PositiveIntegerField(default=0)

    requirements = models.PositiveIntegerField(default=0)
    satisfied = models.PositiveIntegerField(default=0)

    #: A mandatory criterion has failed. Ordering a list by ``score`` alone
    #: would put an impossible bid above a possible one, so a list has to be
    #: able to read this without recomputing anything.
    blocked = models.BooleanField(default=False)

    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        constraints = [
            models.UniqueConstraint(
                fields=["profile", "notice"], name="one_score_per_profile_notice"
            )
        ]
        indexes = [models.Index(fields=["profile", "-score"])]
        ordering = ["-score", "notice_id"]

    def __str__(self) -> str:
        return f"{self.profile_id} → {self.notice_id}: {self.score:.0%}"
