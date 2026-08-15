"""Run the extraction layers over a notice and persist what survives.

The layers themselves know nothing about each other or about the database
(``extraction.py``). Everything that makes the stack a *stack* rather than four
independent extractors lives here:

* **Cheapest first, and only for what is missing.** Each layer is told which
  keys earlier layers already established and is asked not to look for them
  again. This is the entire economic argument of DECISIONS.md D6 — L1 costs
  nothing and answers a quarter of the corpus, so L2 must never be billed for
  those notices' turnover figures a second time. A pipeline that ran every layer
  over everything would produce the same rows at several times the price, and
  the ablation table would have nothing to compare.

* **Rejection at the boundary, not at read time.** A layer proposes a raw
  expression tree; ``expressions.parse_requirement`` decides whether the engine
  can evaluate it. A tree that will not parse is a malformed extraction, and it
  is dropped and counted here rather than stored and discovered later by a view
  that was trying to answer a bidder. The counter matters as much as the drop:
  the malformed rate per layer and per model tier is a measurement, and silently
  swallowing it would hide the failure mode most likely to grow when the prompt
  changes.

* **Degrading is the normal case, not the exception.** A missing API key, a
  refused request, a document that will not parse — none of these may erase what
  an earlier layer already found. A run is only ``FAILED`` when *nothing* was
  extracted **and** something actually went wrong; a notice that states no
  qualification criteria at all is a correct, common outcome (L1 finds nothing
  in roughly three notices out of four — see ``l1.py``), and marking those
  failed would both misreport the corpus and make the batch retry a settled
  answer forever.

* **Nothing is ever overwritten.** Re-running writes a new ``ExtractionRun``
  with its own rows, because the run table exists to make the ablation
  reconstructable and an updated-in-place row destroys exactly that. The read
  path (``views._requirement_rows``) collapses the accumulation for display.

L2 and L3 are imported lazily inside the dispatcher. That is not a
circular-import workaround: those layers pull in an SDK and a PDF parser that a
deployment running L1 only should not need to have installed, and an absent
layer has to be a recorded state rather than an ImportError at startup — the
same contract the harvester holds for a dead link.
"""

from __future__ import annotations

import logging
import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any

from django.db import transaction
from django.db.models import Count, F, Max, Q

from apps.tenders.models import HarvestedDocument, TenderNotice

from . import expressions, grounding, l1, scoring
from .extraction import Extracted, ExtractedExpert, LayerResult
from .models import ExtractionRun, TenderExpertPosition, TenderRequirement

logger = logging.getLogger(__name__)

#: Cheapest first. This is both the order layers run in and the order their
#: labels are written into ``ExtractionRun.layers``, so the ablation can group by
#: that string and get a set that means one thing.
LAYER_ORDER = (
    TenderRequirement.Layer.L1.value,
    TenderRequirement.Layer.L2.value,
    TenderRequirement.Layer.L3.value,
)

#: What a caller gets without asking. L1 only: it needs no key, no network and
#: no budget, so this default is safe to schedule against the whole corpus.
DEFAULT_LAYERS = (TenderRequirement.Layer.L1.value,)

#: Notices per batch. A module constant rather than a ``settings`` entry because
#: no COMPLIANCE settings block exists yet; when one is added (mirroring
#: ``HARVEST``) this becomes its default rather than staying here.
DEFAULT_BATCH_SIZE = 25

#: How many mirrored documents L3 reads for one notice. Bidding documents run to
#: hundreds of pages and a notice can link a dozen; without a cap one notice
#: could spend a day's budget. The most specific kinds are read first, so the cap
#: cuts off the least likely documents rather than an arbitrary set.
L3_MAX_DOCUMENTS = 3

#: Kinds L3 should read first — the two that actually carry a Section III
#: qualification matrix. ``notice_links`` classified them at harvest time.
_DOCUMENT_PRIORITY = {
    HarvestedDocument.Kind.TOR: 0,
    HarvestedDocument.Kind.BIDDING: 1,
    HarvestedDocument.Kind.PROJECT_DOC: 2,
    HarvestedDocument.Kind.OTHER: 3,
}

#: ``TenderRequirement.key`` is 64 characters. A longer key is not truncated —
#: see ``_rejected_key`` for why.
_KEY_MAX = TenderRequirement._meta.get_field("key").max_length
_LABEL_MAX = TenderRequirement._meta.get_field("label").max_length
_SOURCE_MAX = TenderRequirement._meta.get_field("source").max_length
_MODEL_MAX = ExtractionRun._meta.get_field("model").max_length
_PROMPT_MAX = ExtractionRun._meta.get_field("prompt_version").max_length
_TITLE_MAX = TenderExpertPosition._meta.get_field("title").max_length


class UnknownLayer(ValueError):
    """A layer label that does not exist. A caller mistake, not a data state."""


def normalise_layers(layers: Any) -> tuple[str, ...]:
    """Clean a requested layer set into canonical order, cheapest first.

    Order is imposed rather than respected: asking for ``L2,L1`` and getting L2
    billed for what L1 would have found free is never what the caller meant, and
    honouring the typed order would make two runs with the same layer set
    incomparable in the ablation.
    """
    if isinstance(layers, str):
        layers = layers.split(",")
    requested = {str(label).strip().upper() for label in layers if str(label).strip()}
    unknown = sorted(requested - set(LAYER_ORDER))
    if unknown:
        raise UnknownLayer(f"unknown layer(s): {', '.join(unknown)}")
    return tuple(label for label in LAYER_ORDER if label in requested)


@dataclass
class PipelineStats:
    """What one batch did, in the shape the harvester reports its own batches.

    Every counter here answers a question the evaluation asks. ``unparseable``
    and ``superseded`` in particular are not diagnostics: they are the malformed
    rate and the overlap between layers, both of which the ablation table needs
    per layer set.
    """

    notices: int = 0
    runs: int = 0
    skipped: int = 0
    failed: int = 0
    requirements: int = 0
    verified: int = 0
    not_found: int = 0
    #: Proposals the engine could not parse, dropped at the boundary.
    unparseable: int = 0
    #: Proposals for a key an earlier layer had already established.
    superseded: int = 0
    #: Layers asked for but not installed in this build.
    unavailable: int = 0
    #: Expert positions accepted, and how many the taxonomy had no role for.
    #: The second is the number that says whether the CEO's 36 roles cover what
    #: tenders actually ask for — a gap in the list, not a fault in the run.
    expert_positions: int = 0
    expert_unmapped: int = 0
    cost_usd: Decimal = Decimal("0")
    input_tokens: int = 0
    output_tokens: int = 0
    errors: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "notices": self.notices,
            "runs": self.runs,
            "skipped": self.skipped,
            "failed": self.failed,
            "requirements": self.requirements,
            "verified": self.verified,
            "not_found": self.not_found,
            "unparseable": self.unparseable,
            "superseded": self.superseded,
            "unavailable": self.unavailable,
            "expert_positions": self.expert_positions,
            "expert_unmapped": self.expert_unmapped,
            "cost_usd": str(self.cost_usd),
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
            "errors": self.errors[:20],
        }


# ---------------------------------------------------------------------------
# One notice
# ---------------------------------------------------------------------------
def extract_for_notice(
    notice: TenderNotice,
    *,
    layers: Any = DEFAULT_LAYERS,
    config: Any = None,
    force: bool = False,
) -> ExtractionRun | None:
    """Run the requested layers over one notice and store the result.

    Returns the run that was written, or ``None`` when the notice was skipped
    because it already had a successful run for this exact layer set.

    ``force=False`` compares against the layers that *took part* in an earlier
    run, not against what that run was asked for. So a pass that requested
    ``L1,L2`` while L2 was uninstalled recorded ``L1``, and re-requesting
    ``L1,L2`` after installing it runs again rather than being skipped — which
    is the behaviour the deployment story needs, since layers arrive one at a
    time.

    Never raises for anything a layer, a model or a document does. ``layers``
    itself is validated and will raise :class:`UnknownLayer`, because a typo in
    a management command argument is a caller mistake and silently running a
    different layer set would corrupt the ablation.
    """
    run, _ = _extract(notice, layers=layers, config=config, force=force)
    return run


def extract_from_supplied(
    notice: TenderNotice, *, config: Any = None
) -> ExtractionRun | None:
    """Phase two — read the documents a vendor obtained and gave us.

    The first phase reads what we could reach on our own: the notice body, and
    any document its links led to. For most notices that ends with nothing,
    because the criteria are in a Terms of Reference the notice only names. What
    the notice does publish is a contact, and a vendor who writes to it is
    usually sent the document — so the second phase reads that (DECISIONS.md
    D17).

    Three differences from a first pass, each with a reason.

    **Only L3 runs.** The notice body has not changed since phase one, so
    re-running L1 and L2 over it would spend a request to reproduce a result
    already stored.

    **It always runs.** ``force`` guards against re-reading the same sources;
    here a new document is the entire reason the caller is asking, and skipping
    it because a run labelled ``L3`` already exists would mean an upload
    silently did nothing.

    **It starts from what is already known.** Keys established in phase one are
    excluded, so the vendor is not billed for extracting the turnover figure the
    notice body already stated. Only what is still missing is paid for.

    Returns ``None`` when the vendor has supplied nothing readable — no run row,
    because nothing was read.
    """
    from apps.tenders.models import HarvestedDocument  # noqa: PLC0415 - app boundary

    supplied = notice.harvested_documents.usable().filter(
        origin=HarvestedDocument.Origin.CLIENT_SUPPLIED
    )
    if not supplied.exists():
        return None

    run, _ = _extract(
        notice,
        layers=(TenderRequirement.Layer.L3.value,),
        config=config,
        force=True,
        document_origin=HarvestedDocument.Origin.CLIENT_SUPPLIED,
        seed_keys=established_keys(notice),
    )
    return run


def established_keys(notice: TenderNotice) -> frozenset[str]:
    """Taxonomy keys already extracted for this notice and usable.

    ``is_usable`` is the filter, not merely "a row exists": a key whose only
    row failed grounding has not been established at all, and asking the next
    phase to skip it would leave the tender with an unverifiable claim and no
    second attempt at a verifiable one.
    """
    rows = notice.requirements.exclude(
        grounding=TenderRequirement.Grounding.NOT_FOUND
    ).values_list("key", flat=True)
    return frozenset(rows)


def _extract(
    notice: TenderNotice,
    *,
    layers: Any,
    config: Any,
    force: bool,
    document_origin: str | None = None,
    seed_keys: frozenset[str] = frozenset(),
) -> tuple[ExtractionRun | None, PipelineStats]:
    """The whole pass, with its counters. Split out so the batch can add them up.

    ``extract_for_notice`` returns only the run because that is what a caller
    working on one notice wants; the batch needs the counts, and recomputing
    them from the database afterwards would mean a query per notice and would
    still not recover the drops, which are never stored.
    """
    requested = normalise_layers(layers)
    stats = PipelineStats(notices=1)

    label = ",".join(requested)
    if not force and _already_read(notice, label, requested):
        stats.skipped = 1
        return None, stats

    reference_year = notice.notice_date.year if notice.notice_date else None
    documents = _Documents(notice, origin=document_origin)
    roles = _role_vocabulary()

    results: list[tuple[str, LayerResult]] = []
    problems: list[str] = []
    accepted: list[tuple[str, Extracted, str, HarvestedDocument | None]] = []
    positions: list[tuple[str, ExtractedExpert, str, HarvestedDocument | None]] = []
    # A later phase starts from what earlier ones established, so a vendor
    # uploading a TOR does not pay to re-extract the turnover figure the notice
    # body already stated. Empty for a first pass.
    found_keys: set[str] = set(seed_keys)

    for layer in requested:
        started = time.monotonic()
        result = _run_layer(
            layer,
            notice,
            reference_year=reference_year,
            # Only keys that were *accepted* are excluded. A key L1 proposed and
            # the boundary rejected is still missing, and asking L2 for it is
            # the point of having L2 at all.
            exclude_keys=frozenset(found_keys),
            config=config,
            documents=documents,
            role_slugs=roles,
        )
        if result is None:
            stats.unavailable += 1
            problems.append(f"{layer}: not available in this build")
            continue

        # A deterministic layer has no reason to time itself, and a layer that
        # crashed before it could is exactly the one whose latency is
        # interesting. Wall time is only filled in where the layer left it zero,
        # so a metered layer's own measurement of its API call still wins.
        if not result.duration_ms:
            result.duration_ms = int((time.monotonic() - started) * 1000)
        results.append((layer, result))
        if result.error:
            problems.append(f"{layer}: {result.error}")

        for proposal in result.requirements:
            key = (proposal.key or "").strip()
            if key and key in found_keys:
                # Two layers agreeing is the expected case, not a conflict: the
                # shallower answer is kept because it is the one that cost
                # nothing and, for L1, was read by a rule rather than a model.
                stats.superseded += 1
                continue
            if not key or _rejected_key(key) or not _parses(proposal):
                stats.unparseable += 1
                continue

            document = documents.get(proposal.source_document_id)
            state = grounding.verify(
                proposal.evidence_quote,
                _source_text(notice, document, layer=layer),
                layer=layer,
            )
            found_keys.add(key)
            accepted.append((layer, proposal, state, document))

        for position in result.experts:
            title = (position.title or "").strip()
            if not title:
                continue
            document = documents.get(position.source_document_id)
            state = grounding.verify(
                position.evidence_quote,
                _source_text(notice, document, layer=layer),
                layer=layer,
            )
            positions.append((layer, position, state, document))

    return _persist(notice, requested, results, accepted, positions, problems, stats)


def _persist(
    notice: TenderNotice,
    requested: tuple[str, ...],
    results: list[tuple[str, LayerResult]],
    accepted: list[tuple[str, Extracted, str, HarvestedDocument | None]],
    positions: list[tuple[str, ExtractedExpert, str, HarvestedDocument | None]],
    problems: list[str],
    stats: PipelineStats,
) -> tuple[ExtractionRun, PipelineStats]:
    """Write the run and its rows in one transaction.

    Atomic because a run row with no requirements and a set of requirements with
    no run are both lies about what happened, and the ablation reads both
    tables.
    """
    took_part = tuple(layer for layer, _ in results)
    # A run where no layer could even start still records the layers it was
    # *asked* for. An empty ``layers`` column would drop the row out of every
    # ablation grouping, which is the one place a failure most needs to be
    # visible.
    layer_label = ",".join(took_part or requested)

    totals = LayerResult()
    for _, result in results:
        totals.merge(result)

    error = "; ".join(problems)[:4000]
    # FAILED means "produced nothing, and for a reason". Producing nothing
    # quietly is the correct answer for most notices (see the module docstring),
    # and a failed run there would make ``extract_pending`` retry it forever.
    #
    # A run that found only expert positions counts as having produced
    # something: the notice named the team it needs and stated no financial
    # threshold, which is an ordinary shape for a consulting REOI and not a
    # failure of anything.
    status = (
        ExtractionRun.Status.FAILED
        if not accepted and not positions and problems
        else ExtractionRun.Status.OK
    )

    with transaction.atomic():
        run = ExtractionRun.objects.create(
            notice=notice,
            layers=layer_label[:32],
            model=_join_unique(result.model for _, result in results)[:_MODEL_MAX],
            prompt_version=_join_unique(
                result.prompt_version for _, result in results
            )[:_PROMPT_MAX],
            input_tokens=totals.input_tokens,
            output_tokens=totals.output_tokens,
            cost_usd=totals.cost_usd,
            duration_ms=totals.duration_ms,
            status=status,
            error=error,
        )
        TenderRequirement.objects.bulk_create(
            [
                TenderRequirement(
                    notice=notice,
                    run=run,
                    layer=layer,
                    key=proposal.key.strip(),
                    label=(proposal.label or "")[:_LABEL_MAX],
                    label_uz=(proposal.label_uz or "")[:_LABEL_MAX],
                    label_ru=(proposal.label_ru or "")[:_LABEL_MAX],
                    expression=proposal.expression,
                    applies_to=proposal.applies_to,
                    is_mandatory=proposal.is_mandatory,
                    # Blank rather than defaulted: a layer that did not judge
                    # importance must not look like one that judged it
                    # ``medium``. The fallback is applied once, in
                    # ``scoring.weight_of``, where it is documented.
                    importance=_importance_of(proposal),
                    evidence_quote=proposal.evidence_quote or "",
                    source=(proposal.source or "")[:_SOURCE_MAX],
                    source_document=document,
                    grounding=state,
                )
                for layer, proposal, state, document in accepted
            ]
        )
        roles = _resolve_roles(position.role_slug for _, position, _, _ in positions)
        TenderExpertPosition.objects.bulk_create(
            [
                TenderExpertPosition(
                    notice=notice,
                    run=run,
                    layer=layer,
                    title=position.title.strip()[:_TITLE_MAX],
                    role=roles.get(position.role_slug),
                    count=position.count,
                    is_mandatory=position.is_mandatory,
                    evidence_quote=position.evidence_quote or "",
                    source=(position.source or "")[:_SOURCE_MAX],
                    source_document=document,
                    grounding=state,
                )
                for layer, position, state, document in positions
            ]
        )

    stats.runs = 1
    stats.failed = 1 if status == ExtractionRun.Status.FAILED else 0
    stats.requirements = len(accepted)
    stats.verified = sum(1 for *_, state, _ in accepted if state == grounding.VERIFIED)
    stats.not_found = sum(1 for *_, state, _ in accepted if state == grounding.NOT_FOUND)
    stats.expert_positions = len(positions)
    stats.expert_unmapped = sum(
        1 for _, position, _, _ in positions if not roles.get(position.role_slug)
    )
    stats.cost_usd = totals.cost_usd
    stats.input_tokens = totals.input_tokens
    stats.output_tokens = totals.output_tokens
    stats.errors = [f"{notice.pk}: {problem}" for problem in problems]
    return run, stats


def _already_read(
    notice: TenderNotice, layer_label: str, requested: tuple[str, ...]
) -> bool:
    """Whether this notice has been read at this depth **over what it now has**.

    The older rule asked only whether a successful run existed for the layer
    set, and it was wrong in a way that cost the product its deepest layer.
    Extraction is chained off the sync — a notice published at 11:01 is read at
    11:01 — while the harvester runs on its own ten-minute schedule and reaches
    that notice's Terms of Reference at 11:10. So L3 was handed an empty
    document set, found nothing, and the run was recorded ``ok``, which is
    correct: nothing went wrong, there was simply nothing to read. The rule then
    read that ``ok`` as "settled" and the TOR that arrived nine minutes later was
    never opened. Measured on the deployed server on 2026-08-14: **every** notice
    extracted since 2026-08-10 that has a mirrored document had that document
    arrive after its run — five of five — and the whole corpus carried 13 L3
    requirements against 126 from L2.

    So a run settles a notice only when it saw the sources the notice has now.
    Documents only count when L3 was asked for: a mirrored TOR says nothing
    about whether L1 has read the notice body, and re-queueing an L1-only
    deployment every time the harvester succeeds would spend nothing and
    achieve nothing.

    This is the same question D22 asked about layers — "already read" is
    relative to *what* was read — asked one level down, about sources. It
    cannot loop: a fetched document is never re-fetched (``harvest.select_pending``
    offers only never-tried and failed rows), so each document moves a notice
    back into the queue exactly once, and ``MAX_ATTEMPTS`` bounds the rest.
    """
    runs = ExtractionRun.objects.filter(
        notice=notice, layers=layer_label, status=ExtractionRun.Status.OK
    )
    latest = runs.aggregate(at=Max("created_at"))["at"]
    if latest is None:
        return False
    if TenderRequirement.Layer.L3.value not in requested:
        return True

    # A document mirrored after the last successful run is a source that run
    # could not have been shown, whoever it belongs to.
    return not notice.harvested_documents.usable().filter(fetched_at__gt=latest).exists()


def _join_unique(values) -> str:
    """Distinct non-empty values in first-seen order, comma-joined.

    Two metered layers can run different models in one pass — Haiku for the
    notice body, Sonnet for a 200-page bidding document is a plausible
    configuration. The run records the pair rather than the first one, because
    the ablation groups on ``(layers, model)`` and a half-truth there would
    quietly merge two different configurations into one row.
    """
    seen: list[str] = []
    for value in values:
        value = (value or "").strip()
        if value and value not in seen:
            seen.append(value)
    return ",".join(seen)


def _importance_of(proposal: Extracted) -> str:
    """The proposal's importance, or blank if it named a level we do not have.

    ``llm._to_extracted`` already filters this against the vocabulary, and this
    is the second gate rather than a duplicate of the first: L1 constructs
    ``Extracted`` directly, a future layer may too, and the value ends up in a
    weight table that a percentage is divided by. A level nobody defined must
    reach the column as "not judged", never as itself.
    """
    level = (proposal.importance or "").strip().lower()
    return level if level in scoring.IMPORTANCE_LEVELS else ""


def _rejected_key(key: str) -> bool:
    """Whether a proposed taxonomy key cannot be stored as it stands.

    Truncating would be the obvious fix and is the wrong one: the key *is* the
    taxonomy, and a silently shortened one either collides with a real key or
    invents a new one that no portfolio field will ever match. A key this long
    did not come from the taxonomy, so the proposal is malformed.
    """
    return len(key) > _KEY_MAX


def _parses(proposal: Extracted) -> bool:
    """Whether the engine can evaluate what this layer proposed.

    The payload is assembled in ``TenderRequirement.as_payload()``'s shape so
    that what is validated here is exactly what will be read back later — a
    check against a different shape would pass rows the read path then chokes on.

    ``parse_requirement`` documents ``ExpressionError``, but its own internals
    call ``int()`` and recurse, so adversarial JSON can surface a bare
    ``TypeError`` or a ``RecursionError`` instead. All of them mean the same
    thing here, and a malformed extraction must not be able to kill a batch.
    """
    payload = {
        "key": proposal.key,
        "label": proposal.label,
        "expression": proposal.expression,
        "applies_to": proposal.applies_to,
        "is_mandatory": proposal.is_mandatory,
        "evidence_quote": proposal.evidence_quote,
        "source": proposal.source,
    }
    try:
        expressions.parse_requirement(payload)
    except (expressions.ExpressionError, TypeError, ValueError, RecursionError):
        return False
    return True


def _source_text(
    notice: TenderNotice, document: HarvestedDocument | None, *, layer: str
) -> str:
    """The text a quote from this layer has to be found in.

    A proposal that names a mirrored document is checked against that document;
    everything else against the notice body. When a named document cannot be
    loaded the check falls back to the notice, where a quote copied out of a PDF
    will not be found — so the row is marked ``not_found``. That is the
    conservative direction: an unverifiable requirement is held back from
    verdicts rather than trusted because its source went missing.
    """
    if document is not None:
        return document.text or ""
    return notice.notice_text_sanitized or ""


class _Documents:
    """The mirrored documents for one notice, fetched at most once.

    A per-notice cache rather than a query per proposal: L3 can return twenty
    requirements citing the same document, and grounding each of them needs that
    document's full text.
    """

    def __init__(self, notice: TenderNotice, *, origin: str | None = None):
        self._notice = notice
        #: Which provenance L3 may read. ``None`` means both, which is what a
        #: full pass wants; the two phases pass one each. Filtering here rather
        #: than inside L3 keeps the layer a function of the document it is
        #: given — it should not have to know why it was given that one.
        self._origin = origin
        self._readable: list[HarvestedDocument] | None = None
        self._by_id: dict[str, HarvestedDocument | None] = {}

    def readable(self) -> list[HarvestedDocument]:
        """Documents with text worth reading, most relevant kind first."""
        if self._readable is None:
            queryset = self._notice.harvested_documents.usable()
            if self._origin is not None:
                queryset = queryset.filter(origin=self._origin)
            rows = list(queryset)
            rows.sort(key=lambda doc: (_DOCUMENT_PRIORITY.get(doc.kind, 9), doc.url))
            self._readable = rows
            self._by_id.update({doc.pk: doc for doc in rows})
        return self._readable

    def get(self, document_id: str | None) -> HarvestedDocument | None:
        if not document_id:
            return None
        if document_id not in self._by_id:
            # A layer may cite a document that is not linked to this notice —
            # a project-wide bidding document, most plausibly. Looked up rather
            # than refused, but never created: an id that matches nothing
            # resolves to ``None`` and the row grounds against the notice
            # instead, which fails it. See ``_source_text``.
            self._by_id[document_id] = HarvestedDocument.objects.filter(
                pk=document_id
            ).first()
        return self._by_id[document_id]


# ---------------------------------------------------------------------------
# Layer dispatch
# ---------------------------------------------------------------------------
def _role_vocabulary() -> list[str]:
    """The expert roles a layer may classify a position as, or nothing.

    Wrapped rather than called directly for the reason every network and
    enrichment path here is wrapped: extraction must degrade, never break. A
    deployment whose taxonomy fixture has not been loaded, or that dropped the
    experts app entirely, gets an empty vocabulary — the schema then omits the
    expert array (``llm.build_schema``) and requirements extract exactly as they
    did before D20. Nothing about the older answer depends on the newer one.
    """
    try:
        from apps.experts.vocabulary import role_slugs  # noqa: PLC0415

        return role_slugs()
    except Exception as exc:  # noqa: BLE001 - a directory problem is not a run problem
        logger.warning("Expert vocabulary unavailable, extracting without it: %s", exc)
        return []


def _resolve_roles(slugs: Any) -> dict[str, Any]:
    """Map every slug a run returned onto its role row, in one query.

    A dict rather than a per-position lookup because a TOR naming eight
    positions would otherwise cost eight queries inside the write transaction.
    Slugs with no row — the ``other`` sentinel, or a role retired since the run
    — are simply absent, and the position is stored unmapped.
    """
    wanted = {slug for slug in slugs if slug}
    if not wanted:
        return {}
    try:
        from apps.experts.models import ExpertType  # noqa: PLC0415

        return {
            role.slug: role
            for role in ExpertType.objects.roles().filter(slug__in=wanted)
        }
    except Exception as exc:  # noqa: BLE001 - same contract as above
        logger.warning("Expert roles could not be resolved: %s", exc)
        return {}


def _run_layer(
    layer: str,
    notice: TenderNotice,
    *,
    reference_year: int | None,
    exclude_keys: frozenset[str],
    config: Any,
    documents: _Documents,
    role_slugs: list[str] | None = None,
) -> LayerResult | None:
    """One layer's answer, or ``None`` when the layer is not installed.

    Every call is wrapped: the contract says a layer returns ``error`` rather
    than raising, but this pipeline runs third-party SDKs and PDF parsers over
    text written by whoever published the notice, and trusting that contract
    with a whole batch behind it would be optimistic.
    """
    try:
        if layer == TenderRequirement.Layer.L1.value:
            return _run_l1(notice, reference_year=reference_year, exclude_keys=exclude_keys)
        if layer == TenderRequirement.Layer.L2.value:
            return _run_l2(
                notice,
                reference_year=reference_year,
                exclude_keys=exclude_keys,
                config=config,
                role_slugs=role_slugs,
            )
        if layer == TenderRequirement.Layer.L3.value:
            return _run_l3(
                documents,
                reference_year=reference_year,
                exclude_keys=exclude_keys,
                config=config,
                role_slugs=role_slugs,
            )
    except ImportError:
        return None
    except Exception as exc:  # noqa: BLE001 - one layer must not kill a batch
        logger.exception("%s failed for %s", layer, notice.pk)
        return LayerResult(error=f"{type(exc).__name__}: {exc}"[:500])
    return None


def _run_l1(
    notice: TenderNotice, *, reference_year: int | None, exclude_keys: frozenset[str]
) -> LayerResult:
    """Adapt the rule extractor to the layer contract.

    ``l1.extract`` predates ``extraction.py`` and returns parsed
    ``expressions.Requirement`` objects — it has no reason to hand back an
    unparseable tree, since it builds the tree itself. The conversion is
    therefore lossless in one direction only, which is fine: what the pipeline
    needs is the raw form, and it revalidates it at the boundary like any other
    layer's rather than trusting the provenance.
    """
    requirements = l1.extract(
        notice.notice_text_sanitized, reference_year=reference_year
    )
    return LayerResult(
        requirements=[
            Extracted(
                key=requirement.key,
                expression=requirement.expression.to_dict(),
                label=requirement.label,
                applies_to=requirement.applies_to.value,
                is_mandatory=requirement.is_mandatory,
                evidence_quote=requirement.evidence_quote,
                source=requirement.source or "notice_body",
            )
            for requirement in requirements
            if requirement.key not in exclude_keys
        ],
        notes={"rules_matched": len(requirements)},
    )


def _run_l2(
    notice: TenderNotice,
    *,
    reference_year: int | None,
    exclude_keys: frozenset[str],
    config: Any,
    role_slugs: list[str] | None = None,
) -> LayerResult:
    from . import l2  # noqa: PLC0415 - see the module docstring

    return l2.extract(
        notice.notice_text_sanitized or "",
        reference_year=reference_year,
        exclude_keys=exclude_keys,
        config=config,
        role_slugs=role_slugs,
    )


def _run_l3(
    documents: _Documents,
    *,
    reference_year: int | None,
    exclude_keys: frozenset[str],
    config: Any,
    role_slugs: list[str] | None = None,
) -> LayerResult:
    """L3 over every readable document for the notice, folded into one result.

    Documents are read in sequence and each is told what the previous ones
    already found, so a bidding document that repeats the TOR's qualification
    table is not paid for twice. The keys accumulated here are local to L3;
    what the pipeline accepts is decided afterwards, at the boundary.
    """
    from . import l3  # noqa: PLC0415 - see the module docstring

    combined = LayerResult()
    seen = set(exclude_keys)
    readable = documents.readable()[:L3_MAX_DOCUMENTS]
    for document in readable:
        result = l3.extract(
            document,
            reference_year=reference_year,
            exclude_keys=frozenset(seen),
            config=config,
            role_slugs=role_slugs,
        )
        for proposal in result.requirements:
            # The layer is asked to set this; setting it here as well means a
            # quote is still checked against the document it came from when the
            # layer forgets, rather than against the notice, where it would fail.
            proposal.source_document_id = proposal.source_document_id or document.pk
            seen.add(proposal.key)
        for position in result.experts:
            position.source_document_id = position.source_document_id or document.pk
        combined.merge(result)

    combined.notes["documents_read"] = len(readable)
    return combined


# ---------------------------------------------------------------------------
# Batch
# ---------------------------------------------------------------------------
#: How many times one notice may be attempted at one layer set before the batch
#: stops offering it.
#:
#: The cap exists because the selection below asks "has this been read *at this
#: depth*", and a notice that fails for a reason of its own — a body the
#: splitter cannot cut, a document that parses to noise — would otherwise be
#: picked up by every cycle forever, spending a metered request each time to
#: fail identically. Three attempts is enough to ride out the failures that are
#: not the notice's fault (a rate limit, a timeout, a key that arrived late) and
#: few enough that a genuinely broken notice costs three requests rather than
#: one per half-hour. Past the cap it needs ``force``, which is a person
#: deciding to spend again.
MAX_ATTEMPTS = 3


def select_pending(
    limit: int,
    *,
    queryset=None,
    layers: Any = DEFAULT_LAYERS,
    force: bool = False,
):
    """Notices this layer set has not successfully read yet, newest first.

    **Per layer set, not "no run at all", and that is the whole point of this
    function.** The older rule — any run disqualifies the notice — meant a
    corpus read once at L1 could never be read deeper by a batch: the notices
    were filtered out before ``force`` was consulted, so both the console's
    button and ``--force --limit`` selected nothing and reported success. The
    per-notice half of the pipeline never had that gap (``_extract`` compares
    against the requested layer set), so this is the selection catching up with
    the decision that was already being made one notice at a time.

    What follows from it is the behaviour an operator expects from a button:
    pressing it reads whatever is not read *at the depth this deployment now
    runs*, and pressing it again when everything is current selects nothing and
    costs nothing. A key arriving, a deeper layer set, a run that failed — all
    three are the same question, and this is where it is asked.

    **And per source set, once L3 is in it.** A successful run no longer settles
    a notice whose documents arrived after it — see ``_already_read`` for the
    measurement that forced this. The two halves have to agree: a notice this
    function keeps offering and ``_extract`` keeps skipping is a batch that
    spends its whole limit on rows it will not read.

    ``force`` drops the condition entirely, for the case the layer set cannot
    express: the same depth, read again, because the model or the prompt
    changed underneath it.

    Newest first because the product is about open tenders, and because notice
    formatting drifts — the recent end of the corpus is the one the extractors
    are being tuned against.
    """
    if queryset is None:
        queryset = TenderNotice.objects.focus()

    rows = queryset.exclude(notice_text_sanitized="")
    if not force:
        # The same label ``_persist`` writes and ``_extract`` compares against.
        requested = normalise_layers(layers)
        label = ",".join(requested)
        at_this_depth = Q(extraction_runs__layers=label)
        ok_here = at_this_depth & Q(extraction_runs__status=ExtractionRun.Status.OK)
        # ``distinct=True`` because the L3 branch below joins a second table:
        # without it one run against three mirrored documents counts as three
        # attempts, and MAX_ATTEMPTS would retire a notice on its first pass.
        rows = rows.annotate(
            _ok_here=Count("extraction_runs", filter=ok_here, distinct=True),
            _tries_here=Count("extraction_runs", filter=at_this_depth, distinct=True),
        )

        if TenderRequirement.Layer.L3.value in requested:
            # Two aggregates over two different joins, compared. Django spells
            # the "read again because a source is newer" test this way; doing it
            # in Python would mean loading the whole open feed to filter it.
            rows = rows.annotate(
                _last_ok_at=Max("extraction_runs__created_at", filter=ok_here),
                _last_document_at=Max(
                    "harvested_documents__fetched_at",
                    filter=Q(
                        harvested_documents__status=HarvestedDocument.Status.FETCHED,
                        harvested_documents__text_chars__gte=(
                            HarvestedDocument.MIN_USEFUL_CHARS
                        ),
                    ),
                ),
            ).filter(
                Q(_ok_here=0) | Q(_last_document_at__gt=F("_last_ok_at")),
                _tries_here__lt=MAX_ATTEMPTS,
            )
        else:
            rows = rows.filter(_ok_here=0, _tries_here__lt=MAX_ATTEMPTS)

    # Two joins with aggregates over both can repeat a notice; the ordering is
    # unaffected, but a batch handed the same row twice would pay twice.
    return list(rows.distinct().order_by("-notice_date", "-notice_id")[:limit])


def extract_pending(
    *,
    limit: int | None = None,
    layers: Any = DEFAULT_LAYERS,
    config: Any = None,
    queryset=None,
    force: bool = False,
) -> PipelineStats:
    """Extract one bounded batch. Never raises for anything a notice does."""
    stats = PipelineStats()
    # ``layers`` reaches the selection as well as the extraction: what counts as
    # already-read depends on how deep this batch is about to read.
    notices = select_pending(
        limit or DEFAULT_BATCH_SIZE, queryset=queryset, layers=layers, force=force
    )
    for notice in notices:
        stats = _accumulate(stats, extract_one(notice, layers=layers, config=config, force=force))
    logger.info("Extraction batch complete: %s", stats.as_dict())
    return stats


def extract_one(
    notice: TenderNotice, *, layers: Any = DEFAULT_LAYERS, config: Any = None, force: bool = False
) -> PipelineStats:
    """One notice, with its counters, never raising.

    The batch and the management command both want the numbers rather than the
    run object, and both need one bad notice to cost one notice.
    """
    try:
        _, stats = _extract(notice, layers=layers, config=config, force=force)
        return stats
    except UnknownLayer:
        # A bad layer set is a caller mistake and is the same mistake for every
        # notice in the batch, so it propagates rather than being counted 25
        # times as a per-notice error.
        raise
    except Exception as exc:  # noqa: BLE001 - one notice must not kill a batch
        # ``getattr`` rather than ``notice.pk``: this handler runs when something
        # about the notice was already wrong, and a failure while reporting a
        # failure would take the batch down after all.
        identifier = getattr(notice, "pk", "?")
        logger.exception("Extraction failed for %s", identifier)
        return PipelineStats(notices=1, errors=[f"{identifier}: {exc}"])


def _accumulate(total: PipelineStats, one: PipelineStats) -> PipelineStats:
    total.notices += one.notices
    total.runs += one.runs
    total.skipped += one.skipped
    total.failed += one.failed
    total.requirements += one.requirements
    total.verified += one.verified
    total.not_found += one.not_found
    total.unparseable += one.unparseable
    total.superseded += one.superseded
    total.unavailable += one.unavailable
    total.expert_positions += one.expert_positions
    total.expert_unmapped += one.expert_unmapped
    total.cost_usd += one.cost_usd
    total.input_tokens += one.input_tokens
    total.output_tokens += one.output_tokens
    total.errors.extend(one.errors)
    return total


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------
def corpus_report() -> dict[str, Any]:
    """What the extraction stack has covered, as counts.

    The same role ``harvest.corpus_report`` plays for the document mirror: these
    are the numbers that say whether the ablation has enough data behind it, and
    they come from the whole corpus rather than from whatever was last run by
    hand.
    """
    from django.db.models import Count, Sum

    notices_total = TenderNotice.objects.exclude(notice_text_sanitized="").count()
    # The batch walks ``focus()``, so measuring coverage against the whole
    # 25,000-row mirror would report a permanent 0% and hide the real progress.
    # Both denominators are reported: the mirror is the honest total, the focus
    # scope is what the pipeline is actually working through.
    notices_in_scope = (
        TenderNotice.objects.focus().exclude(notice_text_sanitized="").count()
    )
    notices_with_run = (
        TenderNotice.objects.filter(extraction_runs__isnull=False).distinct().count()
    )
    notices_with_requirements = (
        TenderNotice.objects.filter(requirements__isnull=False).distinct().count()
    )

    run_totals = ExtractionRun.objects.aggregate(
        runs=Count("id"),
        cost=Sum("cost_usd"),
        input_tokens=Sum("input_tokens"),
        output_tokens=Sum("output_tokens"),
        duration=Sum("duration_ms"),
    )
    failed_runs = ExtractionRun.objects.filter(
        status=ExtractionRun.Status.FAILED
    ).count()

    by_layer_set = {
        row["layers"]: {"runs": row["runs"], "cost": row["cost"] or Decimal("0")}
        for row in ExtractionRun.objects.values("layers").annotate(
            runs=Count("id"), cost=Sum("cost_usd")
        )
    }
    by_layer = {
        row["layer"]: row["count"]
        for row in TenderRequirement.objects.values("layer").annotate(count=Count("id"))
    }

    # One row per requirement would be wasteful over a large corpus, so the
    # grounding tally is built from grouped counts rather than from the rows.
    state_counts: list[str] = []
    for row in TenderRequirement.objects.values("grounding").annotate(count=Count("id")):
        state_counts.extend([row["grounding"]] * row["count"])
    rate = grounding.rate_over(state_counts)

    return {
        "notices_total": notices_total,
        "notices_in_scope": notices_in_scope,
        "notices_with_run": notices_with_run,
        "notices_without_run": max(notices_total - notices_with_run, 0),
        "notices_with_requirements": notices_with_requirements,
        "runs": run_totals["runs"] or 0,
        "failed_runs": failed_runs,
        "cost_usd": run_totals["cost"] or Decimal("0"),
        "input_tokens": run_totals["input_tokens"] or 0,
        "output_tokens": run_totals["output_tokens"] or 0,
        "duration_ms": run_totals["duration"] or 0,
        "by_layer_set": by_layer_set,
        "by_layer": by_layer,
        "grounding": rate.as_dict(),
        # How much of the work is done: of the notices the batch will walk, how
        # many have been read at all.
        "coverage_rate": (
            round(notices_with_run / notices_in_scope, 3) if notices_in_scope else None
        ),
        # The number the whole layering exists to move: of the notices actually
        # read, how many yielded a machine-readable requirement. L1 alone should
        # land near 23% (l1.py, DECISIONS.md D12) — a figure far above that is a
        # sign the rules are matching things they should not, not a success.
        "yield_rate": (
            round(notices_with_requirements / notices_with_run, 3)
            if notices_with_run
            else None
        ),
    }
