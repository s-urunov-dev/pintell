"""L1 — qualification criteria read from the notice body by rule, not by model.

The second layer of the extraction stack (DECISIONS.md D6). It runs before any
language model, costs nothing, takes about a millisecond, and needs no API key —
which is why it exists at all: whatever L1 can establish is something L2 never
has to be asked, and something the product can still answer when the key is
missing or the budget is spent.

**Why rules are viable here.** Procurement English is unusually regular. The
World Bank's own templates produce phrasing that borrowers edit around rather
than rewrite, and they spell every number twice:

    Participation as a contractor, joint venture member, management contractor,
    or subcontractor, in at least two (2) contracts each with a value of at
    least USD 22,400,000.00 (Twenty-two million and four hundred thousand
    United States Dollars) ... within the last ten (10) years (2016-2025)

The parenthesised digit after the spelled-out word is a gift: `two (2)` and
`ten (10)` are trivially parsed where "two" and "ten" alone would not be. The
amount appears as digits with separators and again in words, so the digits can
be read and the words used as a check.

**What this layer will not do.** It does not infer requirements the notice does
not state. A notice that says only "qualification requirements are set out in
Section III of the RFB" yields nothing here, and that is the correct output —
the requirements exist, but not in this document. Filling that gap from what the
that document is L3's job, and when the notice links no document, getting one
is the vendor's — the notice publishes a contact precisely so a bidder can ask
for it, and a document they obtain that way is read by the same L3 (D17).

**How common a non-empty result is.** Measured over 600 CIS Invitation for Bids
notices: 20% state a bid security, 4% an average annual turnover, 4% a
similar-contract requirement, 3% liquid assets — 23% carry at least one. So L1
returns nothing for roughly three notices in four, by the nature of the corpus
rather than by a limitation of the rules. See DECISIONS.md D12.

No Django here, for the same reason as ``expressions.py``: this is testable
against strings, and the storage layer can change without touching it.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from decimal import Decimal, InvalidOperation

from .expressions import AppliesTo, Count, Filter, Requirement, Scalar
from .text import canonical, sentences

#: Currency codes seen in the corpus. Amounts in a local currency are read but
#: **not converted**: the exchange rate to apply — which year's, from which
#: source — is an open question (docs/OPEN-QUESTIONS.md Q11), and a guessed
#: conversion would silently change a threshold by tens of percent.
_CURRENCIES = {
    "USD": "USD", "US$": "USD", "$": "USD", "US DOLLARS": "USD",
    "EUR": "EUR", "€": "EUR",
    "UZS": "UZS", "SOUM": "UZS", "SUM": "UZS",
    "KGS": "KGS", "TJS": "TJS", "GEL": "GEL", "MDL": "MDL", "UAH": "UAH",
}

_MONEY_RE = re.compile(
    r"(?P<currency>USD|US\$|EUR|UZS|KGS|TJS|GEL|MDL|UAH|€|\$)"
    r"\s*"
    r"(?P<amount>\d{1,3}(?:[,\s]\d{3})*(?:\.\d+)?|\d+(?:\.\d+)?)"
    r"\s*(?P<scale>million|billion|mln|bn)?",
    re.IGNORECASE,
)

#: "at least two (2)", "minimum of 2", "not less than two (2)". The
#: parenthesised digit is preferred when present because the spelled-out word
#: may be split across a line break in the source.
_COUNT_RE = re.compile(
    r"(?:at least|minimum(?:\s+of)?|no(?:t)? less than|a minimum of)\s+"
    r"(?:[a-z]+\s*)?\((?P<paren>\d{1,3})\)"
    r"|(?:at least|minimum(?:\s+of)?|no(?:t)? less than)\s+(?P<plain>\d{1,3})\b",
    re.IGNORECASE,
)

#: "within the last ten (10) years", "past three (3) years", "last 5 years".
_WINDOW_RE = re.compile(
    r"(?:last|past|preceding|previous)\s+(?:[a-z]+\s*)?\((?P<paren>\d{1,2})\)\s*years"
    r"|(?:last|past|preceding|previous)\s+(?P<plain>\d{1,2})\s*years",
    re.IGNORECASE,
)

#: An explicit year range the notice states for itself — "(2016-2025)",
#: "(2023-2025)". Preferred over an arithmetic window when present: the
#: borrower has already done the subtraction, and its answer is authoritative
#: even when it disagrees with the stated number of years.
_YEAR_RANGE_RE = re.compile(r"\((?P<from>19|20)(?P<from_rest>\d{2})\s*[-–—]\s*(?:19|20)\d{2}\)")

_SCALES = {"million": 1_000_000, "mln": 1_000_000, "billion": 1_000_000_000, "bn": 1_000_000_000}


@dataclass(frozen=True)
class Money:
    amount: Decimal
    currency: str


def parse_money(text: str) -> list[Money]:
    """Every currency amount in a fragment, in order of appearance."""
    found: list[Money] = []
    for match in _MONEY_RE.finditer(text):
        raw = match.group("amount").replace(",", "").replace(" ", "")
        try:
            amount = Decimal(raw)
        except InvalidOperation:  # pragma: no cover - regex admits nothing else
            continue
        scale = (match.group("scale") or "").lower()
        if scale:
            amount *= _SCALES[scale]
        code = _CURRENCIES.get(match.group("currency").upper(), match.group("currency").upper())
        found.append(Money(amount, code))
    return found


def _first_int(pattern: re.Pattern[str], text: str) -> int | None:
    match = pattern.search(text)
    if not match:
        return None
    value = match.group("paren") or match.group("plain")
    return int(value) if value else None


def _year_floor(sentence: str, reference_year: int | None) -> int | None:
    """The earliest year a contract may have been completed in and still count.

    Prefers a range the notice states outright; falls back to subtracting the
    stated window from the notice's own year. Returns ``None`` when neither is
    available — the filter is then simply not added, which is the honest
    outcome: an unbounded count is wrong, but so is a window we invented.
    """
    explicit = _YEAR_RANGE_RE.search(sentence)
    if explicit:
        return int(explicit.group("from") + explicit.group("from_rest"))
    window = _first_int(_WINDOW_RE, sentence)
    if window is None or reference_year is None:
        return None
    return reference_year - window + 1


# ---------------------------------------------------------------------------
# Rules
# ---------------------------------------------------------------------------
# Each rule reads one sentence and returns at most one requirement. Sentence
# scope is deliberate: it keeps the evidence quote and the numbers that produced
# it in the same fragment, so a quote can never be evidence for a threshold that
# came from somewhere else on the page.

_TURNOVER_RE = re.compile(r"annual\s+(?:\w+\s+)?turnover", re.IGNORECASE)
_LIQUID_RE = re.compile(r"liquid assets|cash[-\s]?flow requirement", re.IGNORECASE)
_BID_SECURITY_RE = re.compile(r"bid security|bid[-\s]securing declaration", re.IGNORECASE)
_CONTRACTS_RE = re.compile(
    r"(?:participation|experience)\b.{0,200}?\bcontracts?\b|similar\s+(?:contracts?|nature)",
    re.IGNORECASE | re.DOTALL,
)


def _turnover(sentence: str, reference_year: int | None) -> Requirement | None:
    if not _TURNOVER_RE.search(sentence):
        return None
    amounts = parse_money(sentence)
    if not amounts:
        return None
    money = max(amounts, key=lambda m: m.amount)
    return Requirement(
        key="annual_turnover_avg",
        label="Average annual turnover",
        expression=Scalar(
            key="annual_turnover_avg",
            op=">=",
            value=float(money.amount),
            unit=money.currency,
            label="Average annual turnover",
        ),
        # The notice states one threshold; whether it applies to a JV combined
        # or to each member is in the Section III matrix, which is not in this
        # document. SINGLE is the conservative reading and is what the engine
        # falls back to for a non-JV bid anyway.
        applies_to=AppliesTo.SINGLE,
        is_mandatory=True,
        evidence_quote=sentence,
        source="notice_body",
    )


def _liquid_assets(sentence: str, reference_year: int | None) -> Requirement | None:
    if not _LIQUID_RE.search(sentence):
        return None
    amounts = parse_money(sentence)
    if not amounts:
        return None
    money = min(amounts, key=lambda m: m.amount)
    return Requirement(
        key="liquid_assets",
        label="Liquid assets / cash flow",
        expression=Scalar(
            key="liquid_assets",
            op=">=",
            value=float(money.amount),
            unit=money.currency,
            label="Liquid assets",
        ),
        applies_to=AppliesTo.SINGLE,
        is_mandatory=True,
        evidence_quote=sentence,
        source="notice_body",
    )


def _similar_contracts(sentence: str, reference_year: int | None) -> Requirement | None:
    """Count of comparable past contracts, with the value floor and time window.

    This is the criterion that motivated the expression tree in the first place
    (DECISIONS.md D1): stored as a bare count it would drop the value floor and
    the window, and then tell a vendor with two small recent contracts that they
    qualify for a threshold they miss by an order of magnitude.
    """
    if not _CONTRACTS_RE.search(sentence):
        return None
    count = _first_int(_COUNT_RE, sentence)
    if count is None:
        return None

    filters: list[Filter] = []
    amounts = parse_money(sentence)
    if amounts:
        # The smallest amount in the sentence is the per-contract floor: a
        # notice offering "two contracts of at least X, or one of at least Y"
        # states Y > X, and the pair that goes with the count we read is the
        # smaller one.
        filters.append(Filter("value_usd", ">=", float(min(a.amount for a in amounts))))
    floor = _year_floor(sentence, reference_year)
    if floor is not None:
        filters.append(Filter("completed_year", ">=", floor))

    return Requirement(
        key="similar_contracts_count",
        label="Similar contracts",
        expression=Count(
            entity="contracts",
            op=">=",
            value=count,
            where=tuple(filters),
            label="Similar contracts",
        ),
        applies_to=AppliesTo.SINGLE,
        is_mandatory=True,
        evidence_quote=sentence,
        source="notice_body",
    )


def _bid_security(sentence: str, reference_year: int | None) -> Requirement | None:
    """The bid security a bidder must post.

    Emitted as a **preference, not a gate**, and the distinction is deliberate.
    A bid security is money the bidder has to put up, not a quality they have to
    possess, and no source we hold says it must be covered by any particular
    balance-sheet item. Modelling it as mandatory against an invented portfolio
    key would be exactly the guess docs/OPEN-QUESTIONS.md exists to prevent.

    It is extracted anyway because it is the single most frequently stated
    figure in the corpus (20% of Invitation for Bids notices) and it is what a
    small vendor actually needs to know before deciding to bid. Undeclared, it
    evaluates to UNKNOWN and never counts against anyone.

    The portfolio key it reads is provisional — see docs/OPEN-QUESTIONS.md Q7.
    """
    if not _BID_SECURITY_RE.search(sentence):
        return None
    amounts = parse_money(sentence)
    if not amounts:
        return None
    money = max(amounts, key=lambda m: m.amount)
    return Requirement(
        key="bid_security",
        label="Bid security",
        expression=Scalar(
            key="bid_security_capacity",
            op=">=",
            value=float(money.amount),
            unit=money.currency,
            label="Bid security the bidder can post",
        ),
        applies_to=AppliesTo.SINGLE,
        is_mandatory=False,
        evidence_quote=sentence,
        source="notice_body",
    )


_RULES = (_turnover, _liquid_assets, _similar_contracts, _bid_security)


def extract(raw_text: str | None, *, reference_year: int | None = None) -> list[Requirement]:
    """Every requirement L1 can establish from one notice body.

    ``reference_year`` is the notice's own year, used to turn "within the last
    five years" into a date floor. Omitted, time windows are dropped rather than
    computed against today: a notice from 2019 evaluated against 2026 would
    silently exclude every contract that actually qualified.

    At most one requirement per key is returned. When two sentences state the
    same criterion — common, because notices restate the headline figures in the
    qualification paragraph — the first is kept, since the restatement is
    usually the abbreviated one.
    """
    text = canonical(raw_text)
    if not text:
        return []

    found: dict[str, Requirement] = {}
    fragments = sentences(text)
    for index, sentence in enumerate(fragments):
        for candidate in _candidates(fragments, index):
            for rule in _RULES:
                requirement = rule(candidate, reference_year)
                if requirement is not None and requirement.key not in found:
                    found[requirement.key] = requirement
    return list(found.values())


#: A clause that ends in a colon has its figure in what follows — "accompanied
#: by a Bid Security in the amount of:" with the sum in the next paragraph is a
#: real and recurring shape. Joining only across a colon is deliberate: it is
#: the punctuation that *states* continuation, so the amount picked up is the
#: one the clause was pointing at rather than the next number on the page.
_CONTINUES_RE = re.compile(r":\s*\.?\s*$")


def _candidates(fragments: list[str], index: int) -> list[str]:
    """The fragment itself, plus its continuation when it announces one."""
    sentence = fragments[index]
    if _CONTINUES_RE.search(sentence) and index + 1 < len(fragments):
        return [sentence, f"{sentence} {fragments[index + 1]}"]
    return [sentence]
