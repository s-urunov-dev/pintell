"""Mirror the documents the notices link to, by hand.

    python manage.py harvest_documents --discover          # register links only
    python manage.py harvest_documents --fetch 50          # fetch a batch
    python manage.py harvest_documents --all               # discover, then fetch
    python manage.py harvest_documents --status            # corpus report
    python manage.py harvest_documents --sample 20         # what was reached

``--status`` is the interesting one: it answers, over the whole corpus rather
than a hand-checked sample, how many of the linked documents are actually
public, how many sit behind a sign-in wall, and how many are scans with no text
layer. Those three numbers decide what the extraction pipeline can be built on.
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.tenders.models import HarvestedDocument
from apps.tenders.services.harvest import (
    corpus_report,
    discover_from_notices,
    discover_from_project_documents,
    harvest_pending,
)


class Command(BaseCommand):
    help = "Discover and mirror the TOR / bidding documents that notices link to."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--all", action="store_true", help="Discover, then fetch.")
        parser.add_argument(
            "--discover", action="store_true",
            help="Register links found in notice bodies (cheap, no network).",
        )
        parser.add_argument("--fetch", type=int, default=0, help="Documents to fetch.")
        parser.add_argument(
            "--discover-limit", type=int, default=500,
            help="Notices to read when discovering.",
        )
        parser.add_argument("--status", action="store_true", help="Print the corpus report.")
        parser.add_argument(
            "--sample", type=int, default=0,
            help="Show N harvested documents with their outcome.",
        )

    def handle(self, *args, **options):
        if options["status"]:
            self._print_status()
            return
        if options["sample"]:
            self._print_sample(options["sample"])
            return

        run_all = options["all"]
        discover = options["discover"] or run_all
        fetch_limit = options["fetch"] or (settings.HARVEST["BATCH_SIZE"] if run_all else 0)

        if not (discover or fetch_limit):
            self.stdout.write(self.style.WARNING(
                "Nothing to do — pass --all, --discover or --fetch N."
            ))
            return

        if discover:
            limit = options["discover_limit"]
            self.stdout.write(f"Reading up to {limit} notice bodies for document links…")
            stats = discover_from_notices(limit=limit)
            discover_from_project_documents(limit=limit, stats=stats)
            self.stdout.write(f"  new links registered: {stats.discovered}")

        if fetch_limit:
            if not settings.HARVEST["ENABLED"]:
                self.stdout.write(self.style.WARNING(
                    "  harvesting is disabled — set HARVEST_ENABLED=true."
                ))
            else:
                self.stdout.write(f"Fetching up to {fetch_limit} documents…")
                stats = harvest_pending(limit=fetch_limit)
                self.stdout.write(
                    f"  attempted={stats.attempted} fetched={stats.fetched} "
                    f"access_denied={stats.access_denied} unreachable={stats.unreachable} "
                    f"no_text={stats.no_text} skipped={stats.skipped} "
                    f"deduplicated={stats.deduplicated}"
                )
                for message in stats.errors[:5]:
                    self.stdout.write(self.style.WARNING(f"  - {message}"))

        self.stdout.write("")
        self._print_status()

    def _print_status(self) -> None:
        report = corpus_report()
        total = report["total"]

        def share(count: int) -> str:
            return f" ({count / total:.0%})" if total else ""

        self.stdout.write("Linked document corpus")
        self.stdout.write(f"  registered       : {total:,}")
        self.stdout.write(f"  not tried yet    : {report['pending']:,}")
        self.stdout.write("")
        self.stdout.write("Outcomes")
        self.stdout.write(f"  fetched + read   : {report['fetched']:,}{share(report['fetched'])}")
        self.stdout.write(
            f"  behind a wall    : {report['access_denied']:,}{share(report['access_denied'])}"
        )
        self.stdout.write(
            f"  unreachable      : {report['unreachable']:,}{share(report['unreachable'])}"
        )
        self.stdout.write(
            f"  stored, no text  : {report['no_text']:,}{share(report['no_text'])}"
        )
        self.stdout.write(f"  refused          : {report['skipped']:,}{share(report['skipped'])}")
        self.stdout.write("")
        # The number the extraction pipeline is planned against: of the links
        # actually tried, how many produced a readable document.
        rate = report["reachable_rate"]
        self.stdout.write(
            f"  reachable rate   : {rate:.1%}" if rate is not None
            else "  reachable rate   : (nothing tried yet)"
        )
        self.stdout.write(
            f"  scans (no text)  : {report['scanned']:,}"
            "   <- if this is large, OCR is on the critical path"
        )
        avg = report["avg_chars"]
        self.stdout.write(
            f"  avg chars/doc    : {avg:,.0f}" if avg else "  avg chars/doc    : —"
        )
        self.stdout.write("")
        self.stdout.write("By kind")
        labels = dict(HarvestedDocument.Kind.choices)
        for kind, count in sorted(report["by_kind"].items(), key=lambda kv: -kv[1]):
            self.stdout.write(f"  {labels.get(kind, kind):<28}: {count:,}")

    def _print_sample(self, limit: int) -> None:
        rows = HarvestedDocument.objects.exclude(
            status=HarvestedDocument.Status.PENDING
        ).order_by("-last_attempt_at")[:limit]

        if not rows:
            self.stdout.write("Nothing harvested yet — run --all first.")
            return

        for document in rows:
            self.stdout.write(
                f"[{document.status:<13}] {document.kind:<11} "
                f"{document.text_chars:>7,} chars  {document.url[:80]}"
            )
            if document.last_error:
                self.stdout.write(f"                 {document.last_error[:100]}")
