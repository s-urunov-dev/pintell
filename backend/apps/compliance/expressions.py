"""The decision engine: what a tender requires, evaluated against what a bidder has.

This module is the reason the product can claim that **the model never decides
eligibility**. A language model reads documents and proposes structured
requirements; everything in this file is ordinary arithmetic over those
requirements, so a verdict can be recomputed by hand, explained line by line,
and reproduced exactly a year later.

Three design decisions shape it.

**A requirement is a tree, not a number.** The obvious model — one key, one
operator, one value — cannot express what procurement documents actually say:

    Participation as contractor or JV member in at least 2 contracts within the
    last 5 years, each with a value of at least US$5,000,000, that have been
    successfully completed.

That is a count over a filtered portfolio, not a scalar comparison, and the
World Bank's standard documents are full of it. So a requirement is a small
expression tree that closes over the bidder's records.

**Who must satisfy it is part of the requirement.** The standard bidding
documents publish a matrix per criterion — the same threshold applies to a
single firm, to a joint venture's members combined, to each member separately,
or to at least one of them. A model that stores only the threshold silently
drops that column, and a JV that would qualify is told it does not.

**A missing answer is not a "no".** Every result is one of three values, and an
unknown never collapses into a failure. Telling a bidder they are ineligible
because *we* lack a number is the one error that costs them money, so the
engine says "not established" and the interface asks for the number instead.
Boolean composition follows Kleene's three-valued logic, which is what makes
that promise hold through ``all``/``any``/``not`` rather than only at the leaves.

Nothing here imports Django. The evaluator takes plain dataclasses, so it is
testable without a database and the storage layer can change without touching
the semantics.
"""

from __future__ import annotations

import operator
from dataclasses import dataclass, field
from datetime import date
from decimal import Decimal, InvalidOperation
from enum import Enum
from typing import Any, Callable, Iterable, Mapping, Sequence


class Verdict(str, Enum):
    """The outcome of evaluating one requirement (or one node of it)."""

    SATISFIED = "satisfied"
    FAILED = "failed"
    #: The requirement is understood, but the bidder has not told us enough to
    #: judge it. Never rendered as a failure — see the module docstring.
    UNKNOWN = "unknown"


class AppliesTo(str, Enum):
    """Who has to meet the requirement.

    Mirrors the columns of the qualification matrix in the World Bank's
    standard bidding documents. ``SINGLE`` is also the answer for a bid with no
    joint venture at all, which keeps the common case free of special-casing.
    """

    SINGLE = "single"
    JV_COMBINED = "jv_combined"
    JV_EACH = "jv_each"
    JV_AT_LEAST_ONE = "jv_at_least_one"


class ExpressionError(ValueError):
    """A requirement tree that cannot be evaluated as written."""


# ---------------------------------------------------------------------------
# The bidder's side
# ---------------------------------------------------------------------------
@dataclass(frozen=True)
class Portfolio:
    """One legal entity's declared capability.

    Two shapes, because requirements ask two kinds of question:

    * ``scalars`` — single declared values (``annual_turnover_avg``, ``iso_9001``)
    * ``collections`` — the records a count or an ``exists`` runs over
      (``contracts``, ``experts``, ``certificates``, ``equipment``)

    A key that is absent means *not declared*, which is what produces
    ``UNKNOWN``. A key present with the value ``None`` means the same thing —
    the two are deliberately not distinguished, because a form field left blank
    and a field never shown are the same state to the person filling it in.
    """

    name: str = ""
    scalars: Mapping[str, Any] = field(default_factory=dict)
    collections: Mapping[str, Sequence[Mapping[str, Any]]] = field(default_factory=dict)
    #: Share of the joint venture, as a percentage. ``None`` for a single bidder.
    jv_share: float | None = None
    is_jv_lead: bool = False

    def scalar(self, key: str) -> Any | None:
        return self.scalars.get(key)

    def has_scalar(self, key: str) -> bool:
        return self.scalars.get(key) is not None

    def items(self, entity: str) -> Sequence[Mapping[str, Any]]:
        return self.collections.get(entity) or ()

    def declares(self, entity: str) -> bool:
        """Whether the bidder has said anything at all about this record type.

        The distinction matters: a bidder with zero contracts *declared* is
        unknown, while a bidder who has stated "I have these three contracts"
        and none of them match is a genuine failure.
        """
        return entity in self.collections


@dataclass(frozen=True)
class Bid:
    """Who is bidding — one firm, or a joint venture of several.

    ``members`` is never empty. A single bidder is a one-member bid, so
    ``AppliesTo`` needs no separate code path for the non-JV case.
    """

    members: Sequence[Portfolio]

    def __post_init__(self) -> None:
        if not self.members:
            raise ExpressionError("a bid needs at least one member")

    @classmethod
    def single(cls, portfolio: Portfolio) -> "Bid":
        return cls(members=(portfolio,))

    @property
    def is_joint_venture(self) -> bool:
        return len(self.members) > 1

    @property
    def lead(self) -> Portfolio | None:
        for member in self.members:
            if member.is_jv_lead:
                return member
        return None

    def combined(self) -> Portfolio:
        """The joint venture read as one entity.

        Collections concatenate — a JV's experience is its members' experience
        pooled, which is exactly what "All Parties Combined" means. Scalars are
        **summed when numeric and dropped otherwise**: a combined turnover is
        the sum of the partners', but there is no meaningful combined answer to
        "do you hold ISO 9001", so that key becomes undeclared rather than
        guessing at ``any`` or ``all``.
        """
        if len(self.members) == 1:
            return self.members[0]

        scalars: dict[str, Any] = {}
        for key in {k for member in self.members for k in member.scalars}:
            values = [m.scalar(key) for m in self.members if m.has_scalar(key)]
            numbers = [_number(v) for v in values]
            if numbers and all(n is not None for n in numbers):
                scalars[key] = sum(numbers)  # type: ignore[arg-type]
            # Non-numeric keys are intentionally omitted: see the docstring.

        collections: dict[str, list[Mapping[str, Any]]] = {}
        for member in self.members:
            for entity, items in member.collections.items():
                collections.setdefault(entity, []).extend(items)

        return Portfolio(
            name=" + ".join(m.name for m in self.members if m.name),
            scalars=scalars,
            collections=collections,
        )


# ---------------------------------------------------------------------------
# Three-valued logic
# ---------------------------------------------------------------------------
def _and(verdicts: Iterable[Verdict]) -> Verdict:
    """Kleene conjunction: one failure decides it; otherwise doubt wins."""
    seen_unknown = False
    for verdict in verdicts:
        if verdict is Verdict.FAILED:
            return Verdict.FAILED
        if verdict is Verdict.UNKNOWN:
            seen_unknown = True
    return Verdict.UNKNOWN if seen_unknown else Verdict.SATISFIED


def _or(verdicts: Iterable[Verdict]) -> Verdict:
    """Kleene disjunction: one success decides it; otherwise doubt wins."""
    seen_unknown = False
    for verdict in verdicts:
        if verdict is Verdict.SATISFIED:
            return Verdict.SATISFIED
        if verdict is Verdict.UNKNOWN:
            seen_unknown = True
    return Verdict.UNKNOWN if seen_unknown else Verdict.FAILED


def _not(verdict: Verdict) -> Verdict:
    if verdict is Verdict.SATISFIED:
        return Verdict.FAILED
    if verdict is Verdict.FAILED:
        return Verdict.SATISFIED
    return Verdict.UNKNOWN


# ---------------------------------------------------------------------------
# Comparison
# ---------------------------------------------------------------------------
_COMPARATORS: dict[str, Callable[[Any, Any], bool]] = {
    ">=": operator.ge,
    ">": operator.gt,
    "<=": operator.le,
    "<": operator.lt,
    "==": operator.eq,
    "!=": operator.ne,
}


def _number(value: Any) -> Decimal | None:
    """Coerce to a comparable number, or ``None`` if it is not one.

    Booleans are excluded on purpose: Python treats ``True`` as ``1``, which
    would let ``iso_9001 >= 1`` quietly succeed and turn a certificate question
    into arithmetic.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, Decimal):
        return value
    if isinstance(value, (int, float)):
        return Decimal(str(value))
    if isinstance(value, str):
        try:
            return Decimal(value.replace(",", "").strip())
        except (InvalidOperation, ValueError):
            return None
    return None


def _compare(op: str, left: Any, right: Any) -> Verdict:
    """Apply one comparator, returning ``UNKNOWN`` rather than guessing.

    Values of different kinds do not compare: a declared ``"10,000,000"`` and a
    required ``10000000`` are the same number and must match, but a date and a
    number are not comparable and produce ``UNKNOWN`` instead of an exception —
    a malformed requirement should degrade to "we cannot tell", not take down
    the request that evaluated it.
    """
    comparator = _COMPARATORS.get(op)
    if comparator is None:
        raise ExpressionError(f"unknown comparator {op!r}")
    if left is None or right is None:
        return Verdict.UNKNOWN

    left_number, right_number = _number(left), _number(right)
    if left_number is not None and right_number is not None:
        return Verdict.SATISFIED if comparator(left_number, right_number) else Verdict.FAILED

    # Equality is meaningful for booleans, strings and dates; ordering is not
    # (an alphabetical ">=" on certificate names would be nonsense).
    if op in {"==", "!="}:
        if isinstance(left, bool) or isinstance(right, bool):
            if not (isinstance(left, bool) and isinstance(right, bool)):
                return Verdict.UNKNOWN
        return Verdict.SATISFIED if comparator(left, right) else Verdict.FAILED

    if isinstance(left, date) and isinstance(right, date):
        return Verdict.SATISFIED if comparator(left, right) else Verdict.FAILED

    return Verdict.UNKNOWN


# ---------------------------------------------------------------------------
# Evaluation trace
# ---------------------------------------------------------------------------
@dataclass
class Trace:
    """Why a node produced its verdict.

    Built on every evaluation rather than on demand, because the explanation is
    a product requirement, not a debugging aid: a bidder told "you do not
    qualify" without a reason does not trust the answer, and an operator cannot
    check a verdict they cannot see the working for.
    """

    node: str
    verdict: Verdict
    detail: str = ""
    children: list["Trace"] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        return {
            "node": self.node,
            "verdict": self.verdict.value,
            "detail": self.detail,
            "children": [child.as_dict() for child in self.children],
        }

    def lines(self, depth: int = 0) -> list[str]:
        """Flatten to indented text — the human-readable explanation."""
        prefix = "  " * depth
        rows = [f"{prefix}[{self.verdict.value}] {self.detail or self.node}"]
        for child in self.children:
            rows.extend(child.lines(depth + 1))
        return rows


@dataclass
class Result:
    verdict: Verdict
    trace: Trace

    @property
    def explanation(self) -> str:
        return "\n".join(self.trace.lines())


# ---------------------------------------------------------------------------
# Nodes
# ---------------------------------------------------------------------------
class Node:
    """Base class for one step of a requirement."""

    kind: str = ""

    def evaluate(self, portfolio: Portfolio) -> Result:  # pragma: no cover - abstract
        raise NotImplementedError

    def to_dict(self) -> dict[str, Any]:  # pragma: no cover - abstract
        raise NotImplementedError


@dataclass
class Scalar(Node):
    """Compare one declared value against a threshold.

    The degenerate case that a naive model would have supported on its own —
    kept as a first-class node so the simple majority of requirements stay
    simple to read in the database.
    """

    kind = "scalar"
    key: str
    op: str
    value: Any
    unit: str = ""
    label: str = ""

    def evaluate(self, portfolio: Portfolio) -> Result:
        declared = portfolio.scalar(self.key)
        verdict = _compare(self.op, declared, self.value)
        shown = "not declared" if declared is None else declared
        detail = f"{self.label or self.key}: {shown} {self.op} {self.value}"
        if self.unit:
            detail += f" {self.unit}"
        return Result(verdict, Trace(self.kind, verdict, detail))

    def to_dict(self) -> dict[str, Any]:
        payload = {"kind": self.kind, "key": self.key, "op": self.op, "value": self.value}
        if self.unit:
            payload["unit"] = self.unit
        if self.label:
            payload["label"] = self.label
        return payload


@dataclass
class Filter:
    """One condition a record must meet to be counted."""

    field: str
    op: str
    value: Any

    def matches(self, item: Mapping[str, Any]) -> Verdict:
        return _compare(self.op, item.get(self.field), self.value)

    def to_dict(self) -> dict[str, Any]:
        return {"field": self.field, "op": self.op, "value": self.value}


@dataclass
class Count(Node):
    """Count the records matching every filter, then compare the count.

    This is the node the scalar model could not express, and the one most
    qualification criteria actually need. A record whose relevant field is
    missing is **not counted and not held against the bidder**: the count comes
    back with an ``incomplete`` marker, and a count that falls short while
    records were incomplete is ``UNKNOWN`` rather than ``FAILED``.
    """

    kind = "count"
    entity: str
    op: str
    value: int
    where: Sequence[Filter] = ()
    label: str = ""

    def evaluate(self, portfolio: Portfolio) -> Result:
        if not portfolio.declares(self.entity):
            detail = f"{self.label or self.entity}: nothing declared"
            return Result(Verdict.UNKNOWN, Trace(self.kind, Verdict.UNKNOWN, detail))

        matched = 0
        incomplete = 0
        for item in portfolio.items(self.entity):
            verdicts = [f.matches(item) for f in self.where]
            outcome = _and(verdicts) if verdicts else Verdict.SATISFIED
            if outcome is Verdict.SATISFIED:
                matched += 1
            elif outcome is Verdict.UNKNOWN:
                incomplete += 1

        verdict = _compare(self.op, matched, self.value)
        # Falling short while some records were unreadable is not a refusal —
        # the missing field might have been the one that qualified them.
        if verdict is Verdict.FAILED and incomplete:
            verdict = Verdict.UNKNOWN

        detail = (
            f"{self.label or self.entity}: {matched} matching "
            f"{self.op} {self.value} required"
        )
        if incomplete:
            detail += f" ({incomplete} record(s) missing data)"
        return Result(verdict, Trace(self.kind, verdict, detail))

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {
            "kind": self.kind,
            "entity": self.entity,
            "op": self.op,
            "value": self.value,
        }
        if self.where:
            payload["where"] = [f.to_dict() for f in self.where]
        if self.label:
            payload["label"] = self.label
        return payload


@dataclass
class Exists(Node):
    """At least one record matches — ``Count`` with the common threshold."""

    kind = "exists"
    entity: str
    where: Sequence[Filter] = ()
    label: str = ""

    def evaluate(self, portfolio: Portfolio) -> Result:
        return Count(
            entity=self.entity, op=">=", value=1, where=self.where, label=self.label
        ).evaluate(portfolio)

    def to_dict(self) -> dict[str, Any]:
        payload: dict[str, Any] = {"kind": self.kind, "entity": self.entity}
        if self.where:
            payload["where"] = [f.to_dict() for f in self.where]
        if self.label:
            payload["label"] = self.label
        return payload


@dataclass
class All(Node):
    """Every child must hold — the "and" of the tree."""

    kind = "all"
    children: Sequence[Node] = ()
    label: str = ""

    def evaluate(self, portfolio: Portfolio) -> Result:
        results = [child.evaluate(portfolio) for child in self.children]
        verdict = _and(r.verdict for r in results)
        trace = Trace(self.kind, verdict, self.label or "all of:", [r.trace for r in results])
        return Result(verdict, trace)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "children": [c.to_dict() for c in self.children],
            **({"label": self.label} if self.label else {}),
        }


@dataclass
class Any_(Node):
    """At least one child must hold — the "or" of the tree.

    Named with a trailing underscore so it does not shadow ``typing.Any``; the
    serialised form is plain ``"any"``.
    """

    kind = "any"
    children: Sequence[Node] = ()
    label: str = ""

    def evaluate(self, portfolio: Portfolio) -> Result:
        results = [child.evaluate(portfolio) for child in self.children]
        verdict = _or(r.verdict for r in results)
        trace = Trace(self.kind, verdict, self.label or "any of:", [r.trace for r in results])
        return Result(verdict, trace)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "children": [c.to_dict() for c in self.children],
            **({"label": self.label} if self.label else {}),
        }


@dataclass
class Not(Node):
    """Negation — for the exclusions ("must not appear on the debarment list")."""

    kind = "not"
    child: Node = None  # type: ignore[assignment]
    label: str = ""

    def evaluate(self, portfolio: Portfolio) -> Result:
        inner = self.child.evaluate(portfolio)
        verdict = _not(inner.verdict)
        trace = Trace(self.kind, verdict, self.label or "not:", [inner.trace])
        return Result(verdict, trace)

    def to_dict(self) -> dict[str, Any]:
        return {
            "kind": self.kind,
            "child": self.child.to_dict(),
            **({"label": self.label} if self.label else {}),
        }


# ---------------------------------------------------------------------------
# Requirement — a tree plus who has to satisfy it
# ---------------------------------------------------------------------------
@dataclass
class Requirement:
    """One qualification criterion, ready to evaluate against a bid."""

    key: str
    expression: Node
    applies_to: AppliesTo = AppliesTo.SINGLE
    is_mandatory: bool = True
    #: Verbatim from the source document. Never paraphrased — see
    #: ``apps.compliance.grounding`` for why this is checked, not trusted.
    evidence_quote: str = ""
    source: str = ""
    label: str = ""

    def evaluate(self, bid: Bid) -> Result:
        """Evaluate against the whole bid, honouring ``applies_to``.

        For a single-member bid every mode reduces to the same evaluation, so
        the joint-venture branches below only ever run on an actual JV.
        """
        if self.applies_to is AppliesTo.SINGLE or not bid.is_joint_venture:
            result = self.expression.evaluate(bid.members[0])
            return Result(result.verdict, self._wrap(result.trace, "single entity"))

        if self.applies_to is AppliesTo.JV_COMBINED:
            result = self.expression.evaluate(bid.combined())
            return Result(result.verdict, self._wrap(result.trace, "all parties combined"))

        per_member = [
            (member, self.expression.evaluate(member)) for member in bid.members
        ]
        traces = [
            Trace(
                "member",
                result.verdict,
                member.name or "(unnamed member)",
                [result.trace],
            )
            for member, result in per_member
        ]

        if self.applies_to is AppliesTo.JV_EACH:
            verdict = _and(result.verdict for _, result in per_member)
            note = "each party"
        else:
            verdict = _or(result.verdict for _, result in per_member)
            note = "at least one party"

        return Result(verdict, Trace("requirement", verdict, self._headline(note), traces))

    def _wrap(self, inner: Trace, note: str) -> Trace:
        return Trace("requirement", inner.verdict, self._headline(note), [inner])

    def _headline(self, note: str) -> str:
        name = self.label or self.key
        tier = "mandatory" if self.is_mandatory else "preference"
        return f"{name} [{tier}, {note}]"

    def to_dict(self) -> dict[str, Any]:
        return {
            "key": self.key,
            "expression": self.expression.to_dict(),
            "applies_to": self.applies_to.value,
            "is_mandatory": self.is_mandatory,
            "evidence_quote": self.evidence_quote,
            "source": self.source,
            "label": self.label,
        }


@dataclass
class Assessment:
    """Every requirement of one tender, evaluated against one bid."""

    results: list[tuple[Requirement, Result]]

    @property
    def blockers(self) -> list[tuple[Requirement, Result]]:
        """Mandatory requirements the bid demonstrably fails."""
        return [
            (req, res)
            for req, res in self.results
            if req.is_mandatory and res.verdict is Verdict.FAILED
        ]

    @property
    def unknowns(self) -> list[tuple[Requirement, Result]]:
        """Requirements waiting on something the bidder has not declared."""
        return [(req, res) for req, res in self.results if res.verdict is Verdict.UNKNOWN]

    @property
    def hard_eligibility_pass(self) -> bool | None:
        """The TZ's ``hard_eligibility_pass`` — with a third state.

        ``True``/``False`` only when the answer is established; ``None`` while a
        mandatory requirement is still unknown. The API surfaces the boolean the
        contract promises **and** the reason it may be absent, rather than
        rounding "we do not know yet" down to "no".
        """
        if self.blockers:
            return False
        if any(req.is_mandatory for req, _ in self.unknowns):
            return None
        return True

    @property
    def status(self) -> str:
        """The four states the interface renders."""
        if not self.results:
            return "unrated"
        if self.blockers:
            return "blocked"
        if any(req.is_mandatory for req, _ in self.unknowns):
            return "incomplete"
        return "eligible"

    @property
    def coverage(self) -> float:
        """Share of requirements that reached a definite verdict."""
        if not self.results:
            return 0.0
        settled = sum(1 for _, res in self.results if res.verdict is not Verdict.UNKNOWN)
        return round(settled / len(self.results), 3)

    def explanation(self) -> str:
        """The deterministic ``explanation`` the TZ requires — no model involved."""
        lines: list[str] = []
        if self.blockers:
            lines.append("Blocked by:")
            lines += [f"  - {res.trace.detail}" for _, res in self.blockers]
        if self.unknowns:
            lines.append("Not yet established:")
            lines += [f"  - {res.trace.detail}" for _, res in self.unknowns]
        if not lines:
            lines.append("All extracted requirements are satisfied.")
        return "\n".join(lines)


def assess(requirements: Sequence[Requirement], bid: Bid) -> Assessment:
    """Evaluate every requirement against one bid."""
    return Assessment([(req, req.evaluate(bid)) for req in requirements])


#: What a vendor's own yes/no reads as in the explanation.
#:
#: Worded so the source of the answer is unmistakable in the trace. "You told us
#: you hold this" and "we computed this from your declared turnover" are both
#: legitimate verdicts and must never be shown as the same one — a bidder acting
#: on the first has a different obligation at submission than one acting on the
#: second, because only the first is a statement they will have to stand behind.
DECLARED_YES = "declared by the bidder: yes"
DECLARED_NO = "declared by the bidder: no"


def declared_result(satisfied: bool, label: str = "") -> Result:
    """One requirement answered by the vendor rather than computed.

    A separate constructor rather than a ``Scalar`` with a boolean value,
    because the two are not the same claim and the trace has to say which it
    is. The node name is ``declaration``, so an explanation printed for an
    auditor states its own provenance without needing the database.
    """
    verdict = Verdict.SATISFIED if satisfied else Verdict.FAILED
    detail = DECLARED_YES if satisfied else DECLARED_NO
    if label:
        detail = f"{label}: {detail}"
    return Result(verdict, Trace("declaration", verdict, detail))


def assess_with_declarations(
    requirements: Sequence[Requirement],
    bid: Bid,
    declarations: Sequence[bool | None],
) -> Assessment:
    """Evaluate, letting the vendor's own answer stand where they gave one.

    ``declarations`` is **positional**: entry *i* answers requirement *i*. Same
    convention as ``reporting.assessment_payload`` and for the same reason — the
    same ``key`` can be produced by two extraction layers, so keying by it would
    apply one answer to two rows the vendor saw as two questions.

    A declaration wins over the engine where both could answer. That is
    deliberate: the engine's answer comes from values the vendor typed into
    their own profile, so both are the vendor's word, and the more specific one
    is the answer they gave to this exact criterion. It also keeps the interface
    honest — a toggle that could be silently overruled by a stale profile field
    would be a control that does not control anything.

    ``None`` means unanswered, which is not the same as ``False``: the engine
    still runs, and the result may be UNKNOWN. Collapsing the two would turn
    every question a vendor has not reached yet into a failure.
    """
    results: list[tuple[Requirement, Result]] = []
    for index, requirement in enumerate(requirements):
        declared = declarations[index] if index < len(declarations) else None
        if declared is None:
            results.append((requirement, requirement.evaluate(bid)))
        else:
            results.append(
                (requirement, declared_result(declared, requirement.label))
            )
    return Assessment(results)


# ---------------------------------------------------------------------------
# Serialisation
# ---------------------------------------------------------------------------
_NODE_TYPES: dict[str, type[Node]] = {
    Scalar.kind: Scalar,
    Count.kind: Count,
    Exists.kind: Exists,
    All.kind: All,
    Any_.kind: Any_,
    Not.kind: Not,
}


# ---------------------------------------------------------------------------
# Describing a requirement without evaluating it
# ---------------------------------------------------------------------------
#: How a comparator reads to a person. ``==`` becomes ``=`` rather than being
#: spelled out, because these strings sit inside a phrase that already reads as
#: a condition ("annual turnover ≥ 5 000 000 USD").
_OPERATOR_WORDS = {
    ">=": "≥",
    ">": ">",
    "<=": "≤",
    "<": "<",
    "==": "=",
    "!=": "≠",
}


#: Below this, digits are not grouped. The grouping exists for money — 5 000 000
#: and 50 000 000 are one keystroke apart unseparated and an order of magnitude
#: apart in fact. But a year is a number too, and "2 019" reads as a bug rather
#: than as 2019. Every year is four digits and every amount this matters for has
#: more, so the digit count separates the two cleanly and no field-name
#: guesswork is needed.
_GROUPING_FLOOR = 10_000


def _describe_value(value: Any) -> str:
    """Render a threshold the way an operator reads a threshold."""
    if isinstance(value, bool):
        return "yes" if value else "no"
    number = _number(value)
    if number is None:
        return str(value)
    if number == number.to_integral_value():
        whole = int(number)
        if abs(whole) < _GROUPING_FLOOR:
            return str(whole)
        return f"{whole:,}".replace(",", " ")
    if abs(number) < _GROUPING_FLOOR:
        return str(number)
    return f"{number:,}".replace(",", " ")


def describe(node: Node) -> str:
    """One line of plain text for a requirement tree, with no bidder involved.

    ``Trace`` already explains a requirement, but only *after* it has been
    evaluated against a portfolio — it answers "why did this bidder fail". The
    operator console asks the question that comes before that one: "what does
    this tender actually demand?". There is no bid to evaluate at that point, so
    the trace cannot answer it and the stored ``expression`` JSON is the only
    source.

    Raw JSON is what the console would otherwise show, and it is unreadable at a
    glance for the case that matters — a nested ``all`` of ``count`` nodes with
    filters is four levels of braces describing one sentence of a bidding
    document.

    Deliberately not localised. The keys, entities and labels inside an
    expression come from the source document in its own language and are not
    translated anywhere else in the system; a half-translated sentence reads
    worse than a consistent English one. The console translates the column
    headings around it, not this.
    """
    if isinstance(node, Scalar):
        parts = [node.label or node.key, _OPERATOR_WORDS.get(node.op, node.op)]
        parts.append(_describe_value(node.value))
        if node.unit:
            parts.append(node.unit)
        return " ".join(parts)

    if isinstance(node, Exists):
        # `Exists` evaluates as `Count >= 1`, so it reads as that too rather
        # than as a separate concept the operator has to learn.
        return _describe_count(
            node.label, node.entity, ">=", 1, node.where, at_least_one=True
        )

    if isinstance(node, Count):
        return _describe_count(node.label, node.entity, node.op, node.value, node.where)

    if isinstance(node, All):
        return _describe_group(node.label, node.children, "and")

    if isinstance(node, Any_):
        return _describe_group(node.label, node.children, "or")

    if isinstance(node, Not):
        inner = describe(node.child) if node.child is not None else "?"
        return f"{node.label or 'not'}: {inner}"

    # An unknown node kind is a malformed row, not a crash: the console is the
    # screen an operator would use to find out that it exists.
    return getattr(node, "kind", "unknown") or "unknown"


def _describe_count(
    label: str,
    entity: str,
    op: str,
    value: Any,
    where: Sequence[Filter],
    *,
    at_least_one: bool = False,
) -> str:
    subject = label or entity
    if at_least_one:
        head = f"at least one {subject}"
    else:
        head = f"{subject}: count {_OPERATOR_WORDS.get(op, op)} {_describe_value(value)}"
    if not where:
        return head
    conditions = ", ".join(
        f"{f.field} {_OPERATOR_WORDS.get(f.op, f.op)} {_describe_value(f.value)}"
        for f in where
    )
    return f"{head} where {conditions}"


def _describe_group(label: str, children: Sequence[Node], joiner: str) -> str:
    if not children:
        return label or f"({joiner}: nothing)"
    described = f" {joiner} ".join(f"({describe(child)})" for child in children)
    return f"{label}: {described}" if label else described


def parse_node(payload: Mapping[str, Any]) -> Node:
    """Build a node from its stored JSON form.

    Requirements live in JSONB and are written by an extraction pipeline, so
    this is a trust boundary: an unknown node kind or a bad comparator is
    rejected here rather than surfacing as a strange verdict later.
    """
    if not isinstance(payload, Mapping):
        raise ExpressionError(f"expected an object, got {type(payload).__name__}")

    kind = payload.get("kind")
    node_type = _NODE_TYPES.get(str(kind))
    if node_type is None:
        raise ExpressionError(f"unknown node kind {kind!r}")

    label = str(payload.get("label", ""))

    if node_type is Scalar:
        return Scalar(
            key=_require(payload, "key"),
            op=_comparator(payload),
            value=payload.get("value"),
            unit=str(payload.get("unit", "")),
            label=label,
        )
    if node_type is Count:
        return Count(
            entity=_require(payload, "entity"),
            op=_comparator(payload),
            value=int(payload.get("value", 1)),
            where=_parse_filters(payload.get("where")),
            label=label,
        )
    if node_type is Exists:
        return Exists(
            entity=_require(payload, "entity"),
            where=_parse_filters(payload.get("where")),
            label=label,
        )
    if node_type is Not:
        child = payload.get("child")
        if child is None:
            raise ExpressionError("'not' needs a child")
        return Not(child=parse_node(child), label=label)

    children = payload.get("children") or []
    if not isinstance(children, Sequence) or isinstance(children, (str, bytes)):
        raise ExpressionError(f"{kind!r} needs a list of children")
    if not children:
        # An empty conjunction is vacuously true and an empty disjunction
        # vacuously false; both are almost certainly an extraction bug, and
        # neither answer is one a bidder should be given.
        raise ExpressionError(f"{kind!r} needs at least one child")
    parsed = [parse_node(child) for child in children]
    return node_type(children=parsed, label=label)  # type: ignore[call-arg]


def parse_requirement(payload: Mapping[str, Any]) -> Requirement:
    applies = str(payload.get("applies_to", AppliesTo.SINGLE.value))
    try:
        applies_to = AppliesTo(applies)
    except ValueError as exc:
        raise ExpressionError(f"unknown applies_to {applies!r}") from exc

    expression = payload.get("expression")
    if expression is None:
        raise ExpressionError("a requirement needs an expression")

    return Requirement(
        key=_require(payload, "key"),
        expression=parse_node(expression),
        applies_to=applies_to,
        is_mandatory=bool(payload.get("is_mandatory", True)),
        evidence_quote=str(payload.get("evidence_quote", "")),
        source=str(payload.get("source", "")),
        label=str(payload.get("label", "")),
    )


def _require(payload: Mapping[str, Any], key: str) -> str:
    value = payload.get(key)
    if not value:
        raise ExpressionError(f"missing required field {key!r}")
    return str(value)


def _comparator(payload: Mapping[str, Any]) -> str:
    op = str(payload.get("op", ""))
    if op not in _COMPARATORS:
        raise ExpressionError(f"unknown comparator {op!r}")
    return op


def _parse_filters(payload: Any) -> tuple[Filter, ...]:
    if not payload:
        return ()
    if not isinstance(payload, Sequence) or isinstance(payload, (str, bytes)):
        raise ExpressionError("'where' must be a list")
    filters = []
    for entry in payload:
        if not isinstance(entry, Mapping):
            raise ExpressionError("each filter must be an object")
        filters.append(
            Filter(
                field=_require(entry, "field"),
                op=_comparator(entry),
                value=entry.get("value"),
            )
        )
    return tuple(filters)
