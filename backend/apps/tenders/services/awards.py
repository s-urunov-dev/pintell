"""Parse Contract Award notices into structured competitor data.

Upstream publishes the winner, the prices and the contract duration only as
prose inside ``notice_text``:

    Date Notification of Award Issued
    (YYYY/MM/DD)
    2025/06/22
    Duration of Contract
    30 Day(s)
    Awarded Bidder(s):
    GLORY OFFICE SOLUTION (746232)
    67 Motijheel BA/A (4th Floor) Dhaka-1000
    Country: Bangladesh
    Bid Price at Opening
    BDT
    Evaluated Bid Price
    BDT 15952213.00
    Signed Contract price
    BDT 15952213.00

The layout is generated from a template, so a line-oriented parser is both
more accurate and far cheaper than asking a model — the AI layer is reserved
for the parts that genuinely need judgement (the contract's direction, and
finding the winner's website).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field
from datetime import date, datetime
from decimal import Decimal, InvalidOperation
from html import unescape

from django.db.models import Q
from django.utils import timezone

from ..models import ContractAward, TenderNotice

logger = logging.getLogger(__name__)

# 2: bidder sections are split per company rather than read until the first
#    price label, which is what populates the evaluated and rejected lists and
#    stops a joint venture's co-members landing in the winner's address.
# 3: the "Small Assignment Contract Award" template is read as well. Upstream
#    publishes two layouts under one `notice_type`, and the labels differ in
#    every position that matters — the date, the awardee heading and the price
#    block all have other names there. A parser that knew only the first
#    layout dropped 2,989 of the 16,806 mirrored award notices on the floor:
#    they were not malformed, they were a template nobody had read.
PARSER_VERSION = 3

# Labels as they appear in the rendered notice text. Where two are listed, the
# second is the "Small Assignment" template's spelling of the same thing.
_AWARD_DATE_LABELS = ("date notification of award issued", "contract signature date")
_DURATION_LABEL = "duration of contract"
#: `awarded firm` covers both `Awarded Firm(s):` and `Awarded Firm/Individual:`.
_AWARDED_LABELS = ("awarded bidder", "awarded firm")
_EVALUATED_LABEL = "evaluated bidder"
_REJECTED_LABEL = "rejected bidder"
_OWNERSHIP_LABEL = "beneficial ownership details"
_REJECTION_REASON_LABEL = "reason for rejection"
_REGISTRY_LABEL = "registry id"
_PRICE_LABELS = {
    "bid price at opening": "bid_price_opening",
    "evaluated bid price": "evaluated_price",
    "signed contract price": "contract_price",
    "contract price": "contract_price",
}

#: The Small Assignment template prints the price as a stack of three bare
#: labels — `Price:` / `Currency:` / `Amount:` — followed by the two values.
#: Only the amount label is matched: it is the one that immediately precedes
#: the values, so the currency and the number are found by reading forward
#: from a single known position rather than by pairing three labels up.
_AMOUNT_LABEL = "amount"

# The body is a run of sections, and each one ends where the next begins.
# `Beneficial Ownership Details` has to be in this list even though nobody
# reads it: it repeats the winner's name in the *same* `NAME (id)` form and
# then lists its directors, so a bidder parser that does not stop here files
# a company's shareholders as rival firms.
_SECTION_HEADINGS = (
    *_AWARDED_LABELS, _EVALUATED_LABEL, _REJECTED_LABEL, _OWNERSHIP_LABEL,
)

# Labels that appear *inside* a bidder list, between one company and the next.
# These are not terminators. Upstream interleaves each bidder's prices with the
# following bidder's name, which is why reading a section "until the first
# price label" — as this parser used to — found the first company and stopped:
# across the mirrored corpus that left `evaluated_bidders` empty on 99.7% of
# the award notices that actually name one.
#
# `Registry ID` is here for a narrower reason: it sits between a company's
# country and the next company, and a parser that does not recognise it as a
# label files the registry number as that company's street address.
_RECORD_LABELS = tuple(_PRICE_LABELS) + (
    "evaluation scores", _REJECTION_REASON_LABEL, _REGISTRY_LABEL,
)

#: Labels that stand alone on their line, matched whole rather than as a
#: prefix. The distinction is load-bearing: `price` as a prefix would read
#: PRICEWATERHOUSECOOPERS as a label and drop the company, and that firm wins
#: consulting contracts in this archive.
_BARE_LABELS = frozenset({
    "price", "currency", _AMOUNT_LABEL, "scores", "technical", "financial",
})

#: What upstream prints in the awardee position when the contract went to an
#: individual it does not name. It is a placeholder, not a company, and 1,744
#: awards carry it — letting it reach `supplier_name` would invent the largest
#: firm in the archive and put it at the top of `companies.py`. The record is
#: kept (the contract and its price are real); only the flat winner columns
#: are left empty, which is also what keeps these out of the similar-awards
#: panel, whose question is *who* won.
_ANONYMOUS_AWARDEES = frozenset({"individual consultant", "individual"})

# Upstream writes a bare hyphen where a company published no address.
_ADDRESS_PLACEHOLDERS = {"-", "--", "n/a", "na", "."}

_MONEY_RE = re.compile(r"([A-Z]{3})?\s*([\d][\d,\.]*)")
_DATE_RE = re.compile(r"(\d{4})[/-](\d{1,2})[/-](\d{1,2})")
_SUPPLIER_REF_RE = re.compile(r"\(([0-9]{3,})\)\s*$")
_COUNTRY_RE = re.compile(r"^country\s*:\s*(.+)$", re.IGNORECASE)
_NAME_RE = re.compile(r"^name\s*:\s*(.+)$", re.IGNORECASE)
_ADDRESS_RE = re.compile(r"^address\s*:\s*(.*)$", re.IGNORECASE)


@dataclass
class AwardDetails:
    supplier_name: str = ""
    supplier_reference: str = ""
    supplier_address: str = ""
    supplier_country: str = ""
    currency: str = ""
    bid_price_opening: Decimal | None = None
    evaluated_price: Decimal | None = None
    contract_price: Decimal | None = None
    award_date: date | None = None
    contract_duration: str = ""
    # The three bidder lists the notice distinguishes, kept apart because the
    # notice keeps them apart: a firm that was evaluated and lost is a
    # different competitive signal from one that was thrown out as
    # non-responsive, and merging them would state something upstream doesn't.
    awarded_bidders: list[dict[str, str]] = field(default_factory=list)
    evaluated_bidders: list[dict[str, str]] = field(default_factory=list)
    rejected_bidders: list[dict[str, str]] = field(default_factory=list)

    @property
    def is_useful(self) -> bool:
        """Worth storing only when at least the winner or a price was found."""
        return bool(self.supplier_name or self.contract_price or self.evaluated_price)


def parse_award_text(notice_text: str) -> AwardDetails:
    """Extract award details from a notice body (HTML or plain text)."""
    details = AwardDetails()
    lines = _to_lines(notice_text)
    if not lines:
        return details

    index = 0
    total = len(lines)
    while index < total:
        line = lines[index]
        lowered = line.lower()

        if lowered.startswith(_AWARD_DATE_LABELS):
            details.award_date = _find_date(lines, index)
        elif lowered.startswith(_DURATION_LABEL):
            details.contract_duration = _next_value(lines, index, max_ahead=2)[:64]
        elif _is_awarded_heading(lowered):
            details.awarded_bidders, index = _parse_bidder_block(
                lines, index + 1, details
            )
            _apply_winner(details)
            continue
        elif _EVALUATED_LABEL in lowered:
            details.evaluated_bidders, index = _parse_bidder_block(
                lines, index + 1, details
            )
            continue
        elif _REJECTED_LABEL in lowered:
            details.rejected_bidders, index = _parse_bidder_block(
                lines, index + 1, details, capture_rejection_reason=True
            )
            continue
        else:
            _absorb_price(lines, index, details)

        index += 1

    return details


def _is_awarded_heading(lowered: str) -> bool:
    """Whether a line opens the awarded-bidder section.

    `Evaluated Bidder(s)` contains `bidder` too, so the evaluated heading has
    to be excluded explicitly — it is checked here rather than at the call
    site so the two award headings cannot drift apart.
    """
    if _EVALUATED_LABEL in lowered:
        return False
    return any(label in lowered for label in _AWARDED_LABELS)


def _absorb_price(lines: list[str], index: int, details: AwardDetails) -> bool:
    """Read a price label at ``index`` into ``details``; True when one matched.

    First value wins, which is what makes the top-level prices the *winner's*:
    the awarded section is published before the evaluated and rejected ones,
    so a later section's numbers can never overwrite it.
    """
    lowered = lines[index].lower()
    for label, attribute in _PRICE_LABELS.items():
        if not lowered.startswith(label):
            continue
        currency, amount = _find_money(lines, index)
        if currency and not details.currency:
            details.currency = currency
        if amount is not None and getattr(details, attribute) is None:
            setattr(details, attribute, amount)
        return True

    if lowered.rstrip(":").strip() == _AMOUNT_LABEL:
        _absorb_amount_block(lines, index, details)
        return True
    return False


def _absorb_amount_block(lines: list[str], index: int, details: AwardDetails) -> None:
    """Read the Small Assignment template's `Amount:` values into ``details``.

    Two values follow the label, in order: the currency and the number. The
    currency is a *name*, not an ISO code — `Kyrgyzstan Som (Kyrgyzstan Som)`,
    with the name repeated in brackets — so it is stored as printed with the
    duplicate dropped. Mapping those twelve names onto ISO codes would be a
    fact this codebase does not have a source for, and the front end already
    falls back to `NAME 1 234,56` for anything that is not a three-letter code.

    The number lands in ``contract_price``: this template publishes one figure
    and calls it the contract amount, so filing it as a bid-opening price
    would say something the notice does not.
    """
    for offset in range(1, 5):
        position = index + offset
        if position >= len(lines):
            return
        line = lines[position]
        if _is_label(line.lower()):
            # Another label before any value: the block is empty, which the
            # one notice in 2,989 without an amount actually looks like.
            return
        if not details.currency and not _MONEY_RE.match(line):
            details.currency = _clean_currency_name(line)
            continue
        _, amount = _find_money(lines, position)
        if amount is not None and details.contract_price is None:
            details.contract_price = amount
        return


def _clean_currency_name(value: str) -> str:
    """`Kyrgyzstan Som (Kyrgyzstan Som)` -> `Kyrgyzstan Som`.

    Only the exact repetition is dropped. A bracketed part that says something
    else is kept, because then it is carrying information rather than echoing.
    """
    text = value.strip()
    match = re.match(r"^(.*?)\s*\((.*)\)$", text)
    if match and match.group(1).strip().casefold() == match.group(2).strip().casefold():
        text = match.group(1).strip()
    return text[:64]


def _to_lines(notice_text: str) -> list[str]:
    """Flatten the notice body into non-empty lines.

    Every tag becomes a line break, because the upstream template encodes the
    label/value structure purely as markup — ``<b>Duration of Contract</b><br>
    30 Day(s)``. The line breaks are the parse structure, so this cannot go
    through the display sanitiser: that collapses all whitespace and would
    merge every label into its neighbours.
    """
    if not notice_text:
        return []

    text = re.sub(r"<[^>]+>", "\n", notice_text)
    text = unescape(text)
    lines = [re.sub(r"[ \t\r\f\v]+", " ", line).strip() for line in text.split("\n")]
    return [line for line in lines if line]


def _next_value(lines: list[str], index: int, max_ahead: int = 3) -> str:
    """First line after ``index`` that looks like a value rather than a label."""
    for offset in range(1, max_ahead + 1):
        position = index + offset
        if position >= len(lines):
            break
        candidate = lines[position]
        if candidate.startswith("(") and candidate.endswith(")"):
            continue  # e.g. the "(YYYY/MM/DD)" format hint
        if candidate.endswith(":"):
            break
        return candidate
    return ""


def _find_date(lines: list[str], index: int) -> date | None:
    for offset in range(0, 4):
        position = index + offset
        if position >= len(lines):
            break
        match = _DATE_RE.search(lines[position])
        if match:
            try:
                return datetime(
                    int(match.group(1)), int(match.group(2)), int(match.group(3))
                ).date()
            except ValueError:
                return None
    return None


def _find_money(lines: list[str], index: int) -> tuple[str, Decimal | None]:
    """Currency and amount for a price label, which may sit on the next line."""
    for offset in range(0, 3):
        position = index + offset
        if position >= len(lines):
            break
        text = lines[position]
        if offset == 0:
            # Strip the label itself so "Evaluated Bid Price" cannot be read
            # as a number by a later regex.
            for label in _PRICE_LABELS:
                if text.lower().startswith(label):
                    text = text[len(label):]
                    break
        match = _MONEY_RE.search(text)
        if not match:
            continue
        currency = (match.group(1) or "").strip()
        raw_amount = match.group(2).replace(",", "")
        try:
            amount = Decimal(raw_amount)
        except (InvalidOperation, ValueError):
            amount = None
        if currency or amount is not None:
            return currency, amount
    return "", None


def _parse_bidder_block(
    lines: list[str],
    start: int,
    details: AwardDetails,
    *,
    capture_rejection_reason: bool = False,
) -> tuple[list[dict[str, str]], int]:
    """Read one bidder section into records, and say where the section ended.

    A record is delimited by the company line, not by the labels around it.
    Two forms of company line occur, and both are needed: `Name: Alke Insaat`
    in the older template, and a bare `METAG INSAAT TICARET (851176)` in the
    current one. The trailing `(id)` is what makes the second form safe to
    split on — it is present on 1,534 of the 1,535 awarded-bidder lines in the
    mirrored corpus, so it identifies a company line without heuristics about
    capitalisation or length.

    Prices met along the way are handed to ``details`` rather than skipped,
    because the winner's prices are published *inside* the awarded section: a
    parser that consumed them silently would have lost every contract value.
    """
    records: list[dict[str, str]] = []
    current: dict[str, str] | None = None
    # Free-text lines are only address while we are still in a record's header.
    # The first label inside the record ends it, so an interleaved `USD 9014065.00`
    # cannot be filed as the next company's street.
    in_header = False
    awaiting_reason = False
    index = start

    while index < len(lines):
        line = lines[index]
        lowered = line.lower()

        if any(heading in lowered for heading in _SECTION_HEADINGS):
            break

        if awaiting_reason:
            awaiting_reason = False
            if current is not None and not _is_label(lowered):
                current["rejection_reason"] = line[:255]
                index += 1
                continue

        name_match = _NAME_RE.match(line)
        if name_match:
            current = _new_record(name_match.group(1))
            records.append(current)
            in_header = True
            index += 1
            continue

        if _SUPPLIER_REF_RE.search(line):
            current = _new_record(line)
            records.append(current)
            in_header = True
            index += 1
            continue

        if _absorb_price(lines, index, details):
            in_header = False
            index += 1
            continue

        if _is_label(lowered):
            in_header = False
            awaiting_reason = capture_rejection_reason and lowered.startswith(
                _REJECTION_REASON_LABEL
            )
            index += 1
            continue

        country_match = _COUNTRY_RE.match(line)
        if country_match:
            if current is not None:
                current["country"] = country_match.group(1).strip()[:255]
            # The country closes the header: everything upstream prints after
            # it belongs to a label, not to this company.
            in_header = False
            index += 1
            continue

        address_match = _ADDRESS_RE.match(line)
        if address_match:
            _append_address(current, address_match.group(1))
            index += 1
            continue

        if current is None:
            # A section whose first company carries neither `Name:` nor an
            # `(id)`. One notice in 1,535 looks like this; taking the line at
            # face value names the bidder, where skipping it names nobody.
            current = _new_record(line)
            records.append(current)
            in_header = True
        elif in_header:
            _append_address(current, line)

        index += 1

    return records, index


def _is_label(lowered: str) -> bool:
    return lowered.startswith(_RECORD_LABELS) or lowered.rstrip(":").strip() in _BARE_LABELS


def _new_record(value: str) -> dict[str, str]:
    """Split a company line into its name and the upstream supplier id."""
    record: dict[str, str] = {}
    text = value.strip()
    match = _SUPPLIER_REF_RE.search(text)
    if match:
        record["reference"] = match.group(1)[:64]
        text = text[: match.start()].strip()
    record["name"] = text[:512]
    return record


def _append_address(record: dict[str, str] | None, value: str) -> None:
    if record is None:
        return
    text = value.strip()
    if not text or text.lower() in _ADDRESS_PLACEHOLDERS:
        return
    existing = record.get("address", "")
    record["address"] = f"{existing}\n{text}".strip() if existing else text


def _apply_winner(details: AwardDetails) -> None:
    """Promote the first awarded bidder into the flat ``supplier_*`` columns.

    Only the first: 66 of 1,535 award notices name several awarded bidders —
    a joint venture, or a lot split between firms — and the flat columns are
    what `companies.py` counts wins by. Spreading a JV across them would
    either double-count the contract or invent a company whose name is two
    companies. The full membership stays in ``awarded_bidders``.
    """
    if not details.awarded_bidders:
        return
    winner = details.awarded_bidders[0]
    if winner.get("name", "").strip().casefold() in _ANONYMOUS_AWARDEES:
        # Upstream named no company. Leaving the flat columns empty is what
        # keeps `companies.py` from counting a firm called "Individual
        # Consultant" — see `_ANONYMOUS_AWARDEES`.
        return
    details.supplier_name = winner.get("name", "")
    details.supplier_reference = winner.get("reference", "")
    details.supplier_address = winner.get("address", "")
    details.supplier_country = winner.get("country", "")


# ---------------------------------------------------------------------------
# Persistence
# ---------------------------------------------------------------------------
def parse_notice_award(notice: TenderNotice) -> ContractAward | None:
    """Parse and store the award for one notice (``None`` if nothing usable)."""
    if not notice.is_award:
        return None

    details = parse_award_text(notice.notice_text_raw or notice.notice_text_sanitized)
    if not details.is_useful:
        return None

    award, _ = ContractAward.objects.update_or_create(
        notice=notice,
        defaults={
            "supplier_name": details.supplier_name,
            "supplier_reference": details.supplier_reference,
            "supplier_address": details.supplier_address,
            "supplier_country": details.supplier_country,
            "currency": details.currency[:64],
            "bid_price_opening": details.bid_price_opening,
            "evaluated_price": details.evaluated_price,
            "contract_price": details.contract_price,
            "award_date": details.award_date or notice.notice_date,
            "contract_duration": details.contract_duration,
            "awarded_bidders": details.awarded_bidders,
            "evaluated_bidders": details.evaluated_bidders,
            "rejected_bidders": details.rejected_bidders,
            "parsed_at": timezone.now(),
            "parser_version": PARSER_VERSION,
        },
    )
    return award


def parse_pending_awards(*, limit: int = 500) -> dict[str, int]:
    """Parse award notices no current-version ``ContractAward`` row covers.

    "Pending" means never parsed *or* parsed by an older parser. Without the
    second half a fix like the one that produced version 2 would only ever
    reach notices arriving after it shipped, and the mirrored archive — which
    is the whole competitor record — would keep serving what the broken parser
    wrote. Re-parsing is free: it reads text already stored.
    """
    queryset = (
        TenderNotice.objects.filter(notice_type="Contract Award")
        .filter(Q(award__isnull=True) | Q(award__parser_version__lt=PARSER_VERSION))
        .exclude(notice_text_raw="")
        .order_by("-notice_date")[:limit]
    )

    parsed = skipped = 0
    for notice in queryset:
        if parse_notice_award(notice):
            parsed += 1
        else:
            skipped += 1

    logger.info("Award parsing: %s parsed, %s without usable details", parsed, skipped)
    return {"parsed": parsed, "skipped": skipped}
