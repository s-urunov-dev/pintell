"""Hand a batch of notices to a reader, and write back what it decided.

The keyword rules and the metered AI path both classify a notice *from its own
text alone*. This command exists for the third case: a reader that can hold a
few hundred notices at once, notice that four of them are lots of the same
project, and place them accordingly. It is deliberately two halves — `--dump`
emits a batch, `--apply` writes decisions back — because the reader sits
between them and is not a function this process can call.

**The sub-direction is written directly, and that is the whole point.**
`apply_classification` derives it with `subcategories.classify_sub`, so an
answer that came from reading the notice would be overwritten by the same
keyword table that got it wrong. Here both fields come from the file.

Rows already decided by a human (`manual`) or paid for (`ai`) are never
dumped and never overwritten. A row this command writes carries
`category_source = agent`, which also keeps `--reclassify` off it — the rules
must not undo a decision made by reading.

No confidence is recorded. The rules compute one from keyword share and the
AI path returns one; a reader has neither, and inventing a number that no
formula produced would put a figure in the API that nothing can justify.
"""

from __future__ import annotations

import json
from pathlib import Path

from django.core.management.base import BaseCommand, CommandError
from django.db.models import Q
from django.utils import timezone

from apps.tenders.categories import CategorySource, TenderCategory
from apps.tenders.consulting import classify_audience
from apps.tenders.models import ContractAward, TenderNotice
from apps.tenders.subcategories import ConsultingSubcategory

#: Sources this command is allowed to overwrite. A human decision and a paid
#: call both outrank a reader; a reader may revise its own earlier answer.
OVERWRITABLE = (CategorySource.RULES, CategorySource.AGENT, "")

#: Sources still waiting to be read. `AGENT` is absent on purpose: a row this
#: command has already looked at is done, whether the answer changed or not.
#: Without that, agreeing with the rules left the row exactly as it was and the
#: next dump handed back the same notices — 180 of the first 400.
UNREAD = (CategorySource.RULES, "")

#: What makes a row worth re-reading. Everything else the rules and the source
#: code already agree on, and spending a reader's attention there would mostly
#: risk changing answers that are right.
SUSPECT = (
    Q(category_confidence__lt=0.55)
    | Q(category=TenderCategory.UNKNOWN)
    | Q(category=TenderCategory.CONSULTING, subcategory__in=["other", ""])
    | Q(category_rationale__contains="overruled")
)

_VALID_CATEGORIES = {value for value, _ in TenderCategory.choices}
_VALID_SUBCATEGORIES = {value for value, _ in ConsultingSubcategory.choices}


class Command(BaseCommand):
    help = "Dump notices for review, or apply reviewed categories from a file."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--dump", type=int, default=0, help="Notices to emit.")
        parser.add_argument(
            "--tier", type=int, default=1, choices=(1, 2, 3),
            help="1: suspect and shown to vendors. 2: suspect, never shown. "
                 "3: shown but not suspect.",
        )
        parser.add_argument("--apply", default="", help="JSONL file of decisions.")
        parser.add_argument(
            "--status", action="store_true", help="How much has been reviewed."
        )

    def handle(self, *args, **options):
        if options["status"]:
            self._status()
            return
        if options["apply"]:
            self._apply(Path(options["apply"]))
            return
        if options["dump"]:
            self._dump(options["dump"], options["tier"])
            return
        raise CommandError("Pass --dump N, --apply FILE, or --status.")

    # -- reading out ------------------------------------------------------

    def _queue(self, tier: int):
        """Award notices worth a read, most consequential first.

        Tier 1 is the set that can actually reach a vendor: a notice whose
        award never parsed a supplier is not in any competitor panel, so a
        wrong direction on it costs nothing today.
        """
        shown = ContractAward.objects.exclude(supplier_name="").values_list(
            "notice_id", flat=True
        )
        queryset = TenderNotice.objects.filter(
            notice_type="Contract Award", category_source__in=UNREAD
        )
        if tier == 1:
            return queryset.filter(SUSPECT).filter(notice_id__in=shown)
        if tier == 2:
            return queryset.filter(SUSPECT).exclude(notice_id__in=shown)
        return queryset.filter(notice_id__in=shown).exclude(SUSPECT)

    def _dump(self, limit: int, tier: int) -> None:
        rows = self._queue(tier).order_by("notice_id")[:limit]
        for notice in rows:
            self.stdout.write(_line(notice))

    # -- writing back -----------------------------------------------------

    def _apply(self, path: Path) -> None:
        if not path.exists():
            raise CommandError(f"No such file: {path}")

        decisions = {}
        for number, raw in enumerate(path.read_text().splitlines(), start=1):
            raw = raw.strip()
            if not raw:
                continue
            try:
                row = json.loads(raw)
            except json.JSONDecodeError as exc:
                raise CommandError(f"{path}:{number}: {exc}") from exc
            _validate(row, f"{path}:{number}")
            decisions[row["id"]] = row

        notices = {
            n.notice_id: n
            for n in TenderNotice.objects.filter(notice_id__in=decisions)
        }

        counters = {"changed": 0, "confirmed": 0, "protected": 0, "missing": 0}
        for notice_id, row in decisions.items():
            notice = notices.get(notice_id)
            if notice is None:
                counters["missing"] += 1
                continue
            if notice.category_source not in OVERWRITABLE:
                # A human or a paid call decided this after the dump was taken.
                counters["protected"] += 1
                continue

            category = row["category"]
            # A sub-direction only exists inside Consulting, and a value on any
            # other direction would be a claim the rest of the code does not
            # expect to read.
            subcategory = row.get("subcategory", "") if category == "consulting" else ""
            # Agreeing with the rules is still a review, and it is recorded as
            # one: the row's direction now rests on a reader who checked it,
            # and it leaves the queue instead of coming back next dump.
            key = "changed" if (notice.category, notice.subcategory) != (category, subcategory) \
                else "confirmed"
            counters[key] += 1

            notice.category = category
            notice.subcategory = subcategory
            notice.category_source = CategorySource.AGENT
            notice.category_confidence = None
            notice.subcategory_confidence = None
            notice.category_rationale = row.get("why", "")[:500]
            notice.category_updated_at = timezone.now()
            # Derived from the selection method, never from the direction, so
            # it stays correct however the direction was decided.
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

        self.stdout.write(
            f"changed={counters['changed']} confirmed={counters['confirmed']} "
            f"protected={counters['protected']} missing={counters['missing']}"
        )

    # -- coverage ---------------------------------------------------------

    def _status(self) -> None:
        awards = TenderNotice.objects.filter(notice_type="Contract Award")
        reviewed = awards.filter(category_source=CategorySource.AGENT).count()
        self.stdout.write(f"award notices     : {awards.count()}")
        self.stdout.write(f"reviewed by agent : {reviewed}")
        for tier in (1, 2, 3):
            self.stdout.write(f"  tier {tier} remaining : {self._queue(tier).count()}")


def _line(notice: TenderNotice) -> str:
    """One notice, small enough that hundreds fit in a single read.

    The body is left out. It is a results table on an award notice — the
    companies and their prices — and says far less about what was bought than
    the title does, at ten times the length.
    """
    title = " ".join((notice.bid_description or "").split())[:220]
    project = " ".join((notice.project_name or "").split())[:90]
    current = notice.category + (f"/{notice.subcategory}" if notice.subcategory else "")
    return (
        f"{notice.notice_id}|{current}|{notice.procurement_group or '--'}"
        f"|{notice.procurement_method_code or '--'}|{title} :: {project}"
    )


def _validate(row: dict, where: str) -> None:
    if "id" not in row:
        raise CommandError(f"{where}: no id")
    if row.get("category") not in _VALID_CATEGORIES:
        raise CommandError(f"{where}: bad category {row.get('category')!r}")
    sub = row.get("subcategory", "")
    if sub and sub not in _VALID_SUBCATEGORIES:
        raise CommandError(f"{where}: bad subcategory {sub!r}")
