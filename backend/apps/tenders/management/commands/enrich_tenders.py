"""Run the enrichment pipeline by hand.

    python manage.py enrich_tenders --all
    python manage.py enrich_tenders --classify 50 --no-ai      # rules only
    python manage.py enrich_tenders --classify 30000 --classify-archive --no-ai
    python manage.py enrich_tenders --projects 10
    python manage.py enrich_tenders --awards 200
    python manage.py enrich_tenders --websites 5               # AI + web search
    python manage.py enrich_tenders --team-leads 10            # AI + web search
    python manage.py enrich_tenders --status
"""

from __future__ import annotations

from django.conf import settings
from django.core.management.base import BaseCommand

from apps.tenders.categories import TenderCategory
from apps.tenders.models import (
    ContractAward,
    ProjectDocument,
    ProjectProfile,
    TeamLeadProfile,
    TenderNotice,
)
from apps.tenders.services.ai.classification import classify_pending, reclassify_by_rules
from apps.tenders.services.ai.client import ai_enabled, classification_enabled
from apps.tenders.services.ai.providers import active_provider
from apps.tenders.services.ai.enrichment import enrich_pending_awards
from apps.tenders.services.ai.people import enrich_pending_team_leads
from apps.tenders.services.awards import parse_pending_awards
from apps.tenders.services.projects import select_pending_projects, sync_pending_projects


class Command(BaseCommand):
    help = "Classify tenders, mirror project documents, parse awards, find supplier sites."

    def add_arguments(self, parser) -> None:
        parser.add_argument("--all", action="store_true", help="Run every step with defaults.")
        parser.add_argument("--classify", type=int, default=0, help="Notices to classify.")
        parser.add_argument(
            "--classify-archive", action="store_true",
            help="Classify the whole mirror, not just the open focus feed.",
        )
        parser.add_argument(
            "--reclassify", type=int, default=0,
            help="Re-read notices the rules already classified, after a rule change.",
        )
        parser.add_argument("--projects", type=int, default=0, help="Projects to mirror.")
        parser.add_argument("--awards", type=int, default=0, help="Award notices to parse.")
        parser.add_argument("--websites", type=int, default=0, help="Supplier sites to find.")
        parser.add_argument(
            "--team-leads", type=int, default=0,
            help="World Bank team leads to look up.",
        )
        parser.add_argument(
            "--no-ai", action="store_true",
            help="Use the keyword rules only; never call the model.",
        )
        parser.add_argument("--status", action="store_true", help="Print coverage and exit.")

    def handle(self, *args, **options):
        if options["status"]:
            self._print_status()
            return

        run_all = options["all"]
        use_ai = None if not options["no_ai"] else False
        config = settings.ANTHROPIC

        classify_limit = options["classify"] or (config["CLASSIFY_BATCH_SIZE"] if run_all else 0)
        project_limit = options["projects"] or (
            settings.PROJECTS["BATCH_SIZE"] if run_all else 0
        )
        award_limit = options["awards"] or (500 if run_all else 0)
        website_limit = options["websites"] or (config["ENRICH_BATCH_SIZE"] if run_all else 0)
        lead_limit = options["team_leads"] or (config["ENRICH_BATCH_SIZE"] if run_all else 0)

        # `--reclassify` is not in `--all`: it is a repair run after a rule
        # change, not a step of the routine enrichment, and it rewrites rows
        # that already have an answer.
        reclassify_limit = options["reclassify"]

        if not any((classify_limit, project_limit, award_limit, website_limit,
                    lead_limit, reclassify_limit)):
            self.stdout.write(self.style.WARNING(
                "Nothing to do — pass --all or one of --classify/--reclassify/"
                "--projects/--awards/--websites/--team-leads."
            ))
            return

        if classify_limit:
            # The default scope is the focus feed, which is what keeps AI spend
            # proportional to product value. That default also means the
            # archive was never classified at all: on the deployed mirror it
            # left 25,407 of 25,431 notices at `unknown`, because only 24 were
            # open at any one time. Contract Awards are never in the feed by
            # construction — they are closed and not an opportunity type — so
            # nothing that reads a competitor's direction could work until
            # this switch existed.
            archive = options["classify_archive"]
            queryset = TenderNotice.objects.all() if archive else None
            scope = "the whole mirror" if archive else "the focus feed"
            self.stdout.write(f"Classifying up to {classify_limit} notices from {scope}…")
            result = classify_pending(
                limit=classify_limit, use_ai=use_ai, queryset=queryset
            )
            self.stdout.write(
                f"  classified={result['classified']} "
                f"(rules={result['by_rules']}, ai={result['by_ai']}), "
                f"still unknown={result['unknown']}"
            )

        if reclassify_limit:
            # Separate from --classify because it answers a different question:
            # not "what is still unread" but "what did the old rules get
            # wrong". Rules only, so it costs nothing and can run over the
            # whole mirror.
            self.stdout.write(
                f"Re-reading up to {reclassify_limit} rule-classified notices…"
            )
            result = reclassify_by_rules(limit=reclassify_limit)
            self.stdout.write(
                f"  re-read={result['seen']} changed={result['changed']}"
            )

        if project_limit:
            self.stdout.write(f"Mirroring up to {project_limit} projects…")
            pending = select_pending_projects(
                TenderNotice.objects.focus()
                .exclude(project_id="")
                .values_list("project_id", flat=True)
                .distinct(),
                limit=project_limit,
            )
            queue = pending.as_dict()
            self.stdout.write(
                f"  queue: new={queue['new']} retry={queue['retry']} stale={queue['stale']}"
            )
            stats = sync_pending_projects(pending.ids)
            self.stdout.write(
                f"  projects={stats.projects_updated}/{stats.projects_seen} "
                f"documents=+{stats.documents_created} esrs={stats.esrs_found} "
                f"failed={stats.failed}"
            )
            for message in stats.errors[:5]:
                self.stdout.write(self.style.WARNING(f"  - {message}"))

        if award_limit:
            self.stdout.write(f"Parsing up to {award_limit} contract awards…")
            result = parse_pending_awards(limit=award_limit)
            self.stdout.write(f"  parsed={result['parsed']} skipped={result['skipped']}")

        if website_limit:
            # Gates on the web-search provider, not on Anthropic: this step
            # runs on Gemini's free tier too.
            if not active_provider():
                self.stdout.write(self.style.WARNING(
                    "  website lookup needs a web-search provider — "
                    "set GEMINI_API_KEY (free tier) or ANTHROPIC_API_KEY (skipped)."
                ))
            else:
                self.stdout.write(f"Looking up up to {website_limit} supplier websites…")
                result = enrich_pending_awards(limit=website_limit)
                self.stdout.write(
                    f"  checked={result['checked']} found={result['found']} "
                    f"skipped={result['skipped']}"
                )

        if lead_limit:
            # Unlike the others this one degrades instead of skipping: with no
            # key it still derives the staff-pattern address, which is most of
            # what tier 3 can offer.
            self.stdout.write(f"Looking up up to {lead_limit} team leads…")
            result = enrich_pending_team_leads(limit=lead_limit)
            self.stdout.write(
                f"  checked={result['checked']} enriched={result['found']} "
                f"skipped={result['skipped']}"
            )

        self.stdout.write("")
        self._print_status()

    def _print_status(self) -> None:
        focus = TenderNotice.objects.focus()
        total_focus = focus.count()
        classified = focus.exclude(category=TenderCategory.UNKNOWN).count()
        awards = ContractAward.objects.count()
        with_site = ContractAward.objects.exclude(supplier_website="").count()

        self.stdout.write("Focus feed (open opportunities in the focus region)")
        self.stdout.write(f"  notices          : {total_focus:,}")
        self.stdout.write(
            f"  classified       : {classified:,}"
            + (f" ({classified / total_focus:.0%})" if total_focus else "")
        )
        self.stdout.write("Projects")
        self.stdout.write(f"  profiles mirrored: {ProjectProfile.objects.count():,}")
        self.stdout.write(f"  documents        : {ProjectDocument.objects.count():,}")
        self.stdout.write(
            f"  with ESRS        : {ProjectProfile.objects.exclude(esrs_pdf_url='').count():,}"
        )
        self.stdout.write(
            f"  due for refresh  : {ProjectProfile.objects.stale().count():,}"
        )
        self.stdout.write(
            "  failing          : "
            f"{ProjectProfile.objects.filter(error_count__gt=0).count():,}"
        )
        leads = TeamLeadProfile.objects.count()
        self.stdout.write("Team leads")
        self.stdout.write(f"  profiles         : {leads:,}")
        self.stdout.write(
            "  with work e-mail : "
            f"{TeamLeadProfile.objects.exclude(work_email='').count():,}"
        )
        # Reported apart from the focus feed, and not derived from it. Reading
        # only the feed is what let the archive sit unclassified unnoticed:
        # the feed is a few dozen rows and was always at 100%.
        archive = TenderNotice.objects.count()
        archive_classified = TenderNotice.objects.exclude(
            category=TenderCategory.UNKNOWN
        ).count()
        self.stdout.write("Whole mirror")
        self.stdout.write(f"  notices          : {archive:,}")
        self.stdout.write(
            f"  classified       : {archive_classified:,}"
            + (f" ({archive_classified / archive:.0%})" if archive else "")
        )

        self.stdout.write("Contract awards")
        self.stdout.write(f"  parsed           : {awards:,}")
        self.stdout.write(f"  supplier website : {with_site:,}")
        self.stdout.write("")
        # Two lines, because a key alone no longer means the classifier runs:
        # an operator reading "AI features: enabled" and then seeing every
        # category come back from rules would be looking for a bug that is a
        # setting (AI_CLASSIFY_ENABLED, see config/settings.py).
        self.stdout.write(
            f"API key    : {'present' if ai_enabled() else 'absent (rules only)'}"
        )
        self.stdout.write(
            f"Classifier : {'on' if classification_enabled() else 'off (AI_CLASSIFY_ENABLED)'}"
        )
        # The two web-search features can run on a provider the classifier
        # cannot use, so their status is reported separately.
        provider = active_provider()
        self.stdout.write(
            f"Web search  : {provider or 'no provider (set GEMINI_API_KEY — free tier)'}"
        )
