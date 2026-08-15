"""Companies that have won contracts — the competitor view.

Built by aggregating :class:`ContractAward` rows by supplier. There is no
company table upstream: the only identity a winner has is the name printed in
the award notice, so that name is the key.

Two honest limitations shape what this exposes:

* **Names are not normalised upstream.** "PEARL CONSTRUCTION" and "Pearl
  Construction Ltd." are separate rows here. Matching them would need fuzzy
  logic that silently merges genuinely different firms, which is worse than
  showing them apart.
* **The sample is thin.** Most winners appear exactly once, so this ranks
  nothing and scores nothing — it counts. A "top supplier" with four wins is
  not meaningfully ahead of one with three, and presenting it as a rating
  would invent precision the archive does not support.

So the shape is a filterable roster, ordered by wins, with the counts visible.
"""

from __future__ import annotations

from decimal import Decimal
from typing import Any

from django.db.models import Count, Max, Min, Q, QuerySet, Sum

from .models import ContractAward


def _base_queryset() -> QuerySet[ContractAward]:
    """Awards that can be attributed to a company at all."""
    return ContractAward.objects.exclude(supplier_name="")


def company_rows(
    *,
    search: str = "",
    country: str = "",
    category: str = "",
    ordering: str = "-wins",
) -> QuerySet[dict[str, Any]]:
    """One row per supplier name, with the numbers worth comparing.

    Filters narrow which *awards* are counted, so filtering by category gives
    "who wins construction work", not "companies that have ever won anything,
    filtered afterwards".
    """
    queryset = _base_queryset()

    if search:
        queryset = queryset.filter(supplier_name__icontains=search)
    if country:
        queryset = queryset.filter(supplier_country__iexact=country)
    if category:
        queryset = queryset.filter(notice__category=category)

    rows = (
        queryset.values("supplier_name")
        .annotate(
            wins=Count("notice"),
            # Countries and websites can differ between a company's awards;
            # the most recent spelling is the best single answer.
            country=Max("supplier_country"),
            website=Max("supplier_website"),
            website_source=Max("supplier_website_source"),
            first_award=Min("award_date"),
            latest_award=Max("award_date"),
            # Prices are per-award and often in different currencies, so a
            # single total would be meaningless. `contract_value` is only
            # summed where the currency agrees — see `company_currency_note`.
            total_usd=Sum("contract_price", filter=Q(currency="USD")),
            usd_awards=Count("notice", filter=Q(currency="USD")),
        )
        .order_by(_safe_ordering(ordering), "supplier_name")
    )
    return rows


#: Orderings a client may ask for. Anything else falls back to wins desc, so a
#: crafted `ordering` cannot reach an arbitrary column.
ALLOWED_ORDERING = {
    "wins": "wins",
    "-wins": "-wins",
    "name": "supplier_name",
    "-name": "-supplier_name",
    "latest": "latest_award",
    "-latest": "-latest_award",
    "value": "total_usd",
    "-value": "-total_usd",
}


def _safe_ordering(requested: str) -> str:
    return ALLOWED_ORDERING.get((requested or "").strip(), "-wins")


def company_awards(supplier_name: str) -> QuerySet[ContractAward]:
    """Every award won by one company, newest first.

    Matched on the exact stored name — see the module docstring on why this
    does not attempt to merge spelling variants.
    """
    return (
        _base_queryset()
        .filter(supplier_name__iexact=supplier_name.strip())
        .select_related("notice")
        .order_by("-award_date")
    )


def company_summary(supplier_name: str) -> dict[str, Any] | None:
    """Headline numbers for one company, or ``None`` when it has no awards."""
    awards = list(company_awards(supplier_name))
    if not awards:
        return None

    usd = [a.contract_price for a in awards if a.currency == "USD" and a.contract_price]
    categories: dict[str, int] = {}
    countries: dict[str, int] = {}
    for award in awards:
        category = award.notice.category
        if category and category != "unknown":
            categories[category] = categories.get(category, 0) + 1
        country = (award.notice.country or "").strip()
        if country:
            countries[country] = countries.get(country, 0) + 1

    latest = awards[0]
    return {
        "name": latest.supplier_name,
        "country": latest.supplier_country,
        "website": latest.supplier_website,
        "website_source": latest.supplier_website_source,
        "wins": len(awards),
        "total_usd": sum(usd, Decimal("0")) if usd else None,
        "usd_awards": len(usd),
        "first_award": min((a.award_date for a in awards if a.award_date), default=None),
        "latest_award": max((a.award_date for a in awards if a.award_date), default=None),
        "by_category": [
            {"value": value, "count": count}
            for value, count in sorted(categories.items(), key=lambda kv: -kv[1])
        ],
        "by_country": [
            {"value": value, "count": count}
            for value, count in sorted(countries.items(), key=lambda kv: -kv[1])
        ],
    }
