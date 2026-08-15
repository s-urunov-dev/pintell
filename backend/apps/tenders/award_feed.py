"""Finished contracts: the browsable list, and the ones like a given tender.

Two readers, one table. The list answers "what has been decided in my line of
work" — a page of its own. `similar_awards` answers "who won the last few
contracts like the one I am reading", which belongs beside an open tender and
must be cheap enough to run on every detail request.

`similar_awards` **filters; it does not score** (D42). It used to weigh five
terms in a Django-free `similar.py` so the weights could be argued with in a
test. The weights were arguable and the result was not: a panel assembled out
of country and recency put the same two contracts under unrelated tenders, and
every fix pushed the terms further from anything a reader could check. What a
vendor asks is narrower than the score was trying to answer — *what has been
awarded in this line of work* — and a plain `WHERE` on the line of work says
exactly that, in one predicate the reader can verify against the row.
"""

from __future__ import annotations

import logging
from typing import Any

from django.db.models import F, Q, QuerySet

from .categories import TenderCategory
from .models import ContractAward, TenderNotice
from .regions import group_countries
from .subcategories import ConsultingSubcategory

logger = logging.getLogger(__name__)

#: Roles a company can hold in an award notice. The notice publishes these as
#: three separate lists and this keeps them separate — see D36.
ROLE_AWARDEE = "awardee"
ROLE_EVALUATED = "evaluated"
ROLE_REJECTED = "rejected"
ROLES = (ROLE_AWARDEE, ROLE_EVALUATED, ROLE_REJECTED)

#: How many contracts the panel shows. More than a handful stops being context
#: beside the tender and becomes a second list to read.
SIMILAR_LIMIT = 5


def award_rows(
    *,
    search: str = "",
    country: str = "",
    category: str = "",
    subcategory: str = "",
    role: str = "",
    country_group: str = "",
) -> QuerySet[ContractAward]:
    """Finished contracts, newest first, narrowed by the filters given.

    `search` looks across every company named in the notice, not only the
    winner: a vendor searching for a competitor wants the contracts it lost as
    much as the ones it won, and those names live in the bidder lists.
    """
    queryset = (
        ContractAward.objects.select_related("notice")
        .exclude(supplier_name="")
        # `nulls_last` spelled out: Postgres sorts NULLs first on DESC, so the
        # plain `-award_date` this started with led the feed with the awards
        # whose date upstream never published. The notice date breaks ties and
        # carries the undated ones, which still have one.
        .order_by(F("award_date").desc(nulls_last=True), "-notice__notice_date")
    )

    if country:
        queryset = queryset.filter(notice__country__iexact=country)
    elif country_group:
        countries = group_countries(country_group)
        # An unknown group returns nothing rather than everything, matching
        # `TenderNoticeFilter.filter_country_group`.
        queryset = queryset.filter(notice__country__in=countries) if countries \
            else queryset.none()

    if category:
        queryset = queryset.filter(notice__category__iexact=category)
    if subcategory:
        queryset = queryset.filter(notice__subcategory__iexact=subcategory)

    if role == ROLE_EVALUATED:
        queryset = queryset.exclude(evaluated_bidders=[])
    elif role == ROLE_REJECTED:
        queryset = queryset.exclude(rejected_bidders=[])
    # ROLE_AWARDEE needs no clause: `supplier_name` is already excluded blank,
    # so every row in this queryset names an awardee.

    if search:
        # The bidder lists are JSON, so the company names inside them are
        # matched as text. `icontains` on the serialised column finds a name
        # wherever it sits in the list, which a key-path lookup could not do
        # without knowing the index.
        queryset = queryset.filter(
            Q(supplier_name__icontains=search)
            | Q(notice__bid_description__icontains=search)
            | Q(evaluated_bidders__icontains=search)
            | Q(rejected_bidders__icontains=search)
        )

    return queryset


def participants(award: ContractAward) -> list[dict[str, Any]]:
    """Every company the notice names, each tagged with the role it held.

    Flattened into one list because that is how the page reads it, but the
    role is carried on every entry — losing on price and being ruled
    non-responsive are different facts, and a flat list without roles would
    quietly merge them.
    """
    rows: list[dict[str, Any]] = []

    # The winner is emitted from the flat columns rather than from
    # `awarded_bidders[0]`, because the website enrichment writes there and
    # only there. Co-members of a joint venture follow, without a website:
    # nothing has looked one up for them.
    if award.supplier_name:
        rows.append({
            "name": award.supplier_name,
            "country": award.supplier_country,
            "role": ROLE_AWARDEE,
            "website": award.supplier_website,
            "website_source": award.supplier_website_source,
        })

    for member in award.awarded_bidders[1:]:
        rows.append({
            "name": member.get("name", ""),
            "country": member.get("country", ""),
            "role": ROLE_AWARDEE,
            "website": "",
            "website_source": "",
        })

    for group, role in (
        (award.evaluated_bidders, ROLE_EVALUATED),
        (award.rejected_bidders, ROLE_REJECTED),
    ):
        for member in group:
            rows.append({
                "name": member.get("name", ""),
                "country": member.get("country", ""),
                "role": role,
                "website": "",
                "website_source": "",
                **({"reason": member["rejection_reason"]} if member.get("rejection_reason") else {}),
            })

    return [row for row in rows if row["name"]]


def similar_awards(
    notice: TenderNotice, *, limit: int = SIMILAR_LIMIT
) -> list[ContractAward]:
    """Awarded contracts closest in **meaning** to ``notice``.

    This used to be a category filter (D42) and is now vector retrieval over
    the semantic index (D45). What changed and what did not:

    * **The question is the same.** "What has been awarded in this line of
      work" — the panel still shows finished contracts with a named winner,
      one row per winner, and it still shows nothing rather than reaching for
      a weaker answer.
    * **Membership is no longer a column comparison.** The direction and its
      sub-direction decided it before; now the notice's most *distinctive*
      indexed passage does, and neighbours come back ordered by cosine. That
      is a real trade and the reason D42 went the other way: a reader could
      check "both are audit consultancies" and cannot check 0.87. The panel
      compensates the only way this codebase accepts — it shows the passage
      that matched beside the row, so there is a sentence to judge.
    * **Ordering is now relevance, not recency.** The date is still on the
      row; it no longer decides which rows exist.

    **The search itself runs once per notice, not once per view.** It used to
    run on every request — about fifteen Qdrant round trips to draw one panel,
    which the reader saw as a block arriving a second after the page around it.
    The neighbours are now computed once and stored (`rag_indexer.neighbours`),
    by the archive command, by the scheduled task, or by the first reader of a
    notice nobody has asked for yet. **What is not cached is this function's
    join**: whether a neighbour is an award with a named winner is a fact about
    `ContractAward` that a reparse can change (D42a), so it is asked fresh
    every time and a winner appearing shows up with nothing recomputed.

    **This makes ``apps.tenders`` depend on ``apps.rag_indexer``**, which is
    the reverse of how those two were built: the index was a cache nothing in
    the product read. It is imported inside the function rather than at module
    scope, and every failure path returns an empty list, so a Qdrant that is
    down or an archive never embedded costs this panel and nothing else — the
    tender page, the feed and every verdict are untouched.

    Empty is a normal answer, and the caller renders no panel for it.
    """
    from apps.rag_indexer import neighbours

    try:
        stored = neighbours.ensure(notice)
    except Exception as exc:  # noqa: BLE001 - a panel, never a page
        logger.info("No semantic neighbours for %s: %s", notice.pk, exc)
        return []

    candidates = [
        (row.award_notice_id, row.score, row.match_passage) for row in stored
    ]
    if not candidates:
        return []

    ranking = {notice_id: index for index, (notice_id, _, _) in enumerate(candidates)}
    passages = {notice_id: passage for notice_id, _, passage in candidates}
    scores = {notice_id: score for notice_id, score, _ in candidates}

    # The join that decides which neighbours are actually *awards*. Kept here
    # rather than in the vector layer because "has a named winner" is a fact
    # about `ContractAward` rows, and a second copy of that rule living beside
    # the collection is how the two come to disagree.
    awards = (
        ContractAward.objects.select_related("notice")
        .exclude(supplier_name="")
        .exclude(notice_id=notice.notice_id)
        .filter(notice_id__in=list(ranking))
    )

    # One row per winner. A firm that took three lots of the same project
    # answers "who won work like this" three times over and crowds out the
    # other answers — observed on `OP00460945`, where one company held three
    # of five rows with three near-identical descriptions.
    seen: set[str] = set()
    rows: list[ContractAward] = []
    for award in sorted(awards, key=lambda row: ranking.get(row.notice_id, 10**6)):
        winner = award.supplier_name.strip().casefold()
        if winner in seen:
            continue
        seen.add(winner)
        # Attached rather than stored: the serializer reads them off the
        # instance, and neither belongs in a column — both describe this
        # comparison, not this contract.
        award.match_score = scores.get(award.notice_id, 0.0)
        award.match_passage = passages.get(award.notice_id, "")
        rows.append(award)
        if len(rows) >= limit:
            break
    return rows


