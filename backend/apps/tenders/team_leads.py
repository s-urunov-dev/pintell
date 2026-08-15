"""What we can say about one World Bank team lead, and what we deliberately cannot.

:class:`~apps.tenders.models.TeamLeadProfile` stores what a search found about a
person. This module turns that into a page: the published professional facts,
plus the part no search is needed for — **which projects they lead here, and
which tenders came out of those projects**. That second half is the useful one.
It is derived entirely from data already mirrored in this database, it is exact
rather than inferred, and it answers the question a bidder actually has: "what
else is this person behind, and is any of it open right now?"

**The line this module holds.**

Only professional, employer-published information is assembled: job title,
unit, duty station, a World Bank work address, public professional URLs (Bank
pages, publications, a LinkedIn profile if one surfaced), and — for staff who
write on the Bank's blog — the biography and official portrait from the Bank's
own author page (see :mod:`apps.tenders.services.bank_pages`).

The portrait is in scope because of *who published it and why*: the Bank put it
online, under its own byline, to identify this person in their professional
capacity. A photograph lifted from a personal social account is a different
thing entirely and is not collected, and neither are personal accounts,
messaging handles or personal phone numbers. These are named private
individuals rather than public figures, and assembling their personal presence
onto one page is a dossier however public each fragment is. The professional
half is what a bidder legitimately needs; the personal half only creates
exposure for the person and liability for whoever published it.

The identifier is the person's folded name (see ``contacts.name_key``), which
contains spaces. URLs use a hyphenated form of it and :func:`slug_from_url`
converts back, so ``/api/team-leads/mohini-kak/`` is the address of a row whose
primary key is ``mohini kak``.
"""

from __future__ import annotations

from typing import Any

from django.db.models import F

from .models import ProjectProfile, TeamLeadProfile, TenderNotice
from .services.ai.people import name_slug


def slug_from_url(value: str) -> str:
    """``mohini-kak`` → ``mohini kak``, the stored primary key."""
    return " ".join((value or "").replace("-", " ").split()).lower()


def url_slug(profile: TeamLeadProfile) -> str:
    """The stored key as it appears in a URL."""
    return profile.slug.replace(" ", "-")


def _projects_for(profile: TeamLeadProfile) -> list[ProjectProfile]:
    """Every mirrored project naming this person as a team lead.

    ``team_lead`` is a comma-separated string upstream, so this filters on the
    name and then confirms each hit by folding it — otherwise "Anna Sukhova"
    would match a project led by "Anna Sukhovaya".
    """
    candidates = ProjectProfile.objects.exclude(team_lead="").filter(
        team_lead__icontains=profile.name.split()[-1]
    )
    return [
        project
        for project in candidates
        if any(name_slug(part) == profile.slug for part in project.team_lead.split(","))
    ]


def profile_payload(profile: TeamLeadProfile) -> dict[str, Any]:
    """One team lead, their projects, and the tenders those projects issued."""
    projects = _projects_for(profile)
    project_ids = [project.project_id for project in projects]

    notices = list(
        TenderNotice.objects.filter(project_id__in=project_ids)
        .order_by("-deadline_date", "-notice_date")[:50]
    ) if project_ids else []

    open_now = [n for n in notices if n.is_open]

    return {
        "id": url_slug(profile),
        "name": profile.name,
        # --- published professional facts -------------------------------
        "title": profile.title,
        "unit": profile.unit,
        "country_office": profile.country_office,
        "organization": "World Bank",
        "work_email": profile.work_email,
        "email_source": profile.email_source,
        "email_confirmed": profile.is_email_confirmed,
        "email_confidence": profile.email_confidence,
        "profile_url": profile.profile_url,
        "links": profile.links,
        "summary": profile.summary,
        # From the Bank's own author page: their wording, their portrait.
        "bank_page_url": profile.bank_page_url,
        "bio": profile.bio,
        "photo_url": profile.photo_url,
        "checked_at": profile.checked_at,
        "source": profile.source,
        # --- what this database knows without asking anyone --------------
        "projects": [
            {
                "project_id": project.project_id,
                "name": project.name,
                "country": project.country,
                "status": project.status,
                "implementing_agency": project.implementing_agency,
                "total_amount_display": project.total_amount_display,
                "project_url": project.project_url,
            }
            for project in projects
        ],
        "stats": {
            "projects": len(projects),
            "notices": len(notices),
            "open_notices": len(open_now),
        },
        "notices": [
            {
                "id": notice.notice_id,
                "title": notice.bid_description or notice.project_name,
                "country": notice.country,
                "notice_type": notice.notice_type,
                "category": notice.category,
                "deadline_date": notice.deadline_date,
                "is_open": notice.is_open,
                "project_id": notice.project_id,
            }
            for notice in notices
        ],
    }


def roster(*, search: str = "") -> list[dict[str, Any]]:
    """The team leads worth listing: enriched first, then the rest by name."""
    queryset = TeamLeadProfile.objects.all()
    if search:
        queryset = queryset.filter(name__icontains=search)

    return [
        {
            "id": url_slug(profile),
            "name": profile.name,
            "title": profile.title,
            "unit": profile.unit,
            "country_office": profile.country_office,
            "work_email": profile.work_email,
            "email_confirmed": profile.is_email_confirmed,
            "links": len(profile.links),
            # Says whether a lookup has run at all, which is the difference
            # between "nothing was found" and "nothing was looked for".
            "enriched": profile.checked_at is not None,
        }
        # Postgres sorts NULLs first on DESC, which would bury every enriched
        # profile under the ones nobody has looked up yet.
        for profile in queryset.order_by(F("checked_at").desc(nulls_last=True), "name")
    ]
