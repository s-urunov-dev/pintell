"""Notices out as a spreadsheet, and corrected categories back in.

The keyword rules, the metered AI path and `review_categories` all decide a
notice's direction from inside this process. This is the fourth way: hand the
rows to a person, let them fix them in a spreadsheet, and read the result. The
surface is the Django admin (`admin.py`), which means a login — correcting the
archive is a staff job, not a public one.

**Categories are not a related table.** `category` and `subcategory` are
`TextChoices` columns on `TenderNotice`, so there is no second model to join
and export. What a person editing the file actually needs from a second sheet
is the *vocabulary* — which values are legal, what each one means, and which
of them apply only inside Consulting — so `vocabulary_rows` emits that.

Two rules make the round trip safe to repeat:

* **An unchanged row is not written at all.** Exporting and re-importing
  without edits is a no-op. Without that, one accidental re-upload would stamp
  16,806 rows as `manual` and freeze the whole archive against every automatic
  correction that comes later.
* **Validation is all-or-nothing.** A single bad category value rejects the
  file rather than applying the good half, because a partly-applied
  spreadsheet is the one state nobody can reason about afterwards.

A row a person edits becomes `category_source = manual`, which outranks every
other source: `--reclassify` skips it, `review_categories` skips it, and the
AI path is never asked about it again. That is the intent — a human decision
is the last word.
"""

from __future__ import annotations

import csv
import io
from typing import Iterable, Iterator

from django.db.models import Count
from django.utils import timezone

from .categories import CategorySource, TenderCategory
from .consulting import classify_audience
from .models import TenderNotice
from .subcategories import ConsultingSubcategory

#: Columns of the awards export. `notice_id` is the key the import reads back;
#: `category` and `subcategory` are the two a person edits. Everything else is
#: there to make the row judgeable without opening the site — you cannot
#: classify "Lot 2" from its id alone.
EXPORT_COLUMNS = (
    "notice_id",
    "category",
    "subcategory",
    "title",
    "project_name",
    "country",
    "procurement_group",
    "procurement_method_code",
    "supplier_name",
    "supplier_country",
    "contract_price",
    "currency",
    "award_date",
    "category_source",
    "on_panel",
    "source_url",
)

#: The only columns the import reads. Everything else in the file is ignored on
#: purpose: a spreadsheet round trip mangles dates and long numbers, and none
#: of those fields are a person's to correct here.
IMPORT_COLUMNS = ("notice_id", "category", "subcategory")

VOCABULARY_COLUMNS = ("kind", "value", "label", "applies_to", "notices")

_VALID_CATEGORIES = {value for value, _ in TenderCategory.choices}
_VALID_SUBCATEGORIES = {value for value, _ in ConsultingSubcategory.choices}

#: Sources a corrected row may replace. `manual` is absent because a person has
#: already spoken for it — a later upload that repeats the same value changes
#: nothing anyway (unchanged rows are skipped), and one that contradicts it
#: should be a deliberate act, not a side effect of re-uploading a stale file.
OVERWRITABLE = (CategorySource.RULES, CategorySource.AGENT, CategorySource.AI, "")


class ImportError_(ValueError):
    """A file that cannot be applied, with the line that made it so."""


# -- out ------------------------------------------------------------------


def award_rows_csv(queryset, *, limit: int | None = None) -> Iterator[str]:
    """The export, one rendered CSV line at a time.

    A generator because the whole archive is 16,806 rows: building the file in
    memory to hand it to the response would hold the entire thing twice. The
    caller narrows — in the admin that is whatever the operator filtered and
    selected, which beats any set of query parameters this module could invent.
    """
    writer = csv.writer(_LineBuffer())
    yield writer.writerow(EXPORT_COLUMNS)

    rows = queryset.iterator(chunk_size=1000)
    for index, notice in enumerate(rows):
        if limit is not None and index >= limit:
            return
        award = getattr(notice, "award", None)
        yield writer.writerow([
            notice.notice_id,
            notice.category,
            notice.subcategory,
            " ".join((notice.bid_description or "").split()),
            " ".join((notice.project_name or "").split()),
            notice.country,
            notice.procurement_group,
            notice.procurement_method_code,
            getattr(award, "supplier_name", "") or "",
            getattr(award, "supplier_country", "") or "",
            getattr(award, "contract_price", "") or "",
            getattr(award, "currency", "") or "",
            getattr(award, "award_date", "") or "",
            notice.category_source,
            "yes" if getattr(award, "supplier_name", "") else "no",
            notice.source_url,
        ])


def vocabulary_rows_csv() -> Iterator[str]:
    """Every legal value, with what it means and how many notices hold it.

    This is the second sheet. It is a vocabulary and not a related table
    because there is no related table — see the module docstring.
    """
    writer = csv.writer(_LineBuffer())
    yield writer.writerow(VOCABULARY_COLUMNS)

    counts = dict(
        TenderNotice.objects.filter(notice_type="Contract Award")
        .values_list("category")
        .annotate(n=Count("notice_id"))
    )
    for value, label in TenderCategory.choices:
        yield writer.writerow(
            ["direction", value, label, "any notice", counts.get(value, 0)]
        )

    sub_counts = dict(
        TenderNotice.objects.filter(
            notice_type="Contract Award", category=TenderCategory.CONSULTING
        )
        .values_list("subcategory")
        .annotate(n=Count("notice_id"))
    )
    for value, label in ConsultingSubcategory.choices:
        yield writer.writerow([
            "sub-direction",
            value,
            label,
            # Stated on every row because it is the rule people get wrong:
            # a sub-direction on a supply contract is a claim the rest of the
            # code does not expect to read, and the import clears it.
            "consulting only — cleared on every other direction",
            sub_counts.get(value, 0),
        ])


class _LineBuffer:
    """A file-like object that returns what was written instead of storing it.

    The standard trick for streaming `csv` output: `csv.writer` needs
    something with `write`, and this makes `writerow` return the rendered line
    so the generator above can yield it.
    """

    def write(self, value: str) -> str:
        return value


# -- in -------------------------------------------------------------------


def parse_corrections(text: str) -> list[dict[str, str]]:
    """Read an edited export, or raise with the line that is wrong.

    Every row is validated before any of them is applied. Unknown columns are
    ignored rather than rejected so a person can keep their own working notes
    in the file.
    """
    reader = csv.DictReader(io.StringIO(text))
    if reader.fieldnames is None:
        raise ImportError_("The file is empty.")

    missing = [c for c in ("notice_id", "category") if c not in reader.fieldnames]
    if missing:
        raise ImportError_(
            f"Missing column(s): {', '.join(missing)}. "
            f"Expected at least {', '.join(IMPORT_COLUMNS)}."
        )

    corrections: list[dict[str, str]] = []
    seen: set[str] = set()
    for number, row in enumerate(reader, start=2):  # row 1 is the header
        notice_id = (row.get("notice_id") or "").strip()
        if not notice_id:
            continue  # a blank line at the end of a spreadsheet is not an error
        if notice_id in seen:
            raise ImportError_(f"line {number}: {notice_id} appears twice")
        seen.add(notice_id)

        category = (row.get("category") or "").strip().lower()
        if category not in _VALID_CATEGORIES:
            raise ImportError_(
                f"line {number}: {category!r} is not a direction. "
                f"See /api/awards/categories.csv"
            )
        subcategory = (row.get("subcategory") or "").strip().lower()
        if subcategory and subcategory not in _VALID_SUBCATEGORIES:
            raise ImportError_(
                f"line {number}: {subcategory!r} is not a sub-direction. "
                f"See /api/awards/categories.csv"
            )
        corrections.append(
            {"notice_id": notice_id, "category": category, "subcategory": subcategory}
        )

    if not corrections:
        raise ImportError_("The file has a header but no rows.")
    return corrections


def apply_corrections(corrections: Iterable[dict[str, str]]) -> dict[str, int]:
    """Write the rows that differ, and only those.

    Returns counts rather than a list: the caller is a person who wants to know
    the upload landed, and naming 16,806 unchanged ids back at them is noise.
    """
    wanted = {row["notice_id"]: row for row in corrections}
    notices = {
        n.notice_id: n for n in TenderNotice.objects.filter(notice_id__in=wanted)
    }

    counts = {"changed": 0, "unchanged": 0, "protected": 0, "unknown": 0}
    for notice_id, row in wanted.items():
        notice = notices.get(notice_id)
        if notice is None:
            counts["unknown"] += 1
            continue

        category = row["category"]
        # A sub-direction only exists inside Consulting. Carrying one on any
        # other direction would put a value in the API that `similar.py` reads
        # as a claim about a trade the contract is not in.
        subcategory = row["subcategory"] if category == TenderCategory.CONSULTING else ""

        if (notice.category, notice.subcategory) == (category, subcategory):
            # Not written at all — see the module docstring. This is what makes
            # re-uploading an unedited export harmless.
            counts["unchanged"] += 1
            continue
        if notice.category_source not in OVERWRITABLE:
            counts["protected"] += 1
            continue

        notice.category = category
        notice.subcategory = subcategory
        notice.category_source = CategorySource.MANUAL
        # No confidence: a person did not compute one, and a number nothing
        # can justify does not belong in the API.
        notice.category_confidence = None
        notice.subcategory_confidence = None
        notice.category_rationale = "corrected in the CSV export"
        notice.category_updated_at = timezone.now()
        notice.consulting_audience = classify_audience(
            category=category,
            procurement_method_code=notice.procurement_method_code,
            procurement_method_name=notice.procurement_method_name,
        ).audience
        notice.save(update_fields=[
            "category", "subcategory", "category_source",
            "category_confidence", "subcategory_confidence",
            "category_rationale", "category_updated_at",
            "consulting_audience", "updated_at",
        ])
        counts["changed"] += 1

    return counts
