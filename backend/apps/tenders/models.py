"""Database models for World Bank procurement notices."""

from __future__ import annotations

from datetime import timedelta

from django.conf import settings
from django.contrib.postgres.search import SearchVectorField
from django.db import models
from django.db.models import F
from django.db.models.functions import Upper
from django.utils import timezone

from .categories import CategorySource, TenderCategory
from .consulting import ConsultingAudience
from .deadlines import resolve_deadline_instant
from .subcategories import ConsultingSubcategory
from .regions import DEFAULT_COUNTRY_GROUP, group_countries


class TenderNoticeQuerySet(models.QuerySet):
    def open(self):
        """Notices whose submission deadline has not passed yet.

        Against ``deadline_at`` — the resolved instant — never against
        ``deadline_date``, which is midnight UTC of the closing day and would
        therefore close a tender the moment its last day began. See the field's
        own comment for what that cost.
        """
        return self.filter(deadline_at__gte=timezone.now())

    def with_deadline(self):
        return self.exclude(deadline_date__isnull=True)

    def bidding_open(self):
        """Opportunities a vendor can act on *right now*.

        Published, and not yet closed. The published half looks redundant —
        upstream is a publication feed — but a notice can carry a future
        ``notice_date``, and reading a tender's requirements before the
        borrower has announced it is not something to do automatically.

        This is the set the automatic extraction works through, which is why
        it is a queryset rather than a filter written at the call site: what
        counts as "active" must mean the same thing to the scheduler, to the
        operator console, and to anyone auditing the bill.
        """
        return self.actionable().filter(
            models.Q(notice_date__isnull=True)
            | models.Q(notice_date__lte=timezone.now().date())
        )

    def in_country_group(self, group: str = DEFAULT_COUNTRY_GROUP):
        countries = group_countries(group)
        return self.filter(country__in=countries) if countries else self.none()

    def actionable(self):
        """Notices a bidder can still act on.

        The product's first stage aggregates exactly these: an open submission
        deadline, and one of the two notice types that represent an actual
        opportunity (an award notice is history, not an opportunity).
        """
        return self.open().filter(notice_type__in=TenderNotice.OPPORTUNITY_TYPES)

    def focus(self, group: str = DEFAULT_COUNTRY_GROUP):
        """`actionable()` narrowed to the focus region."""
        return self.actionable().in_country_group(group)


class TenderNotice(models.Model):
    """One procurement notice, mirrored from the World Bank search API.

    The upstream ``id`` (e.g. ``OP00459233``) is stable and unique, so it is
    used directly as the primary key: re-syncing the same notice updates the
    same row instead of creating duplicates.
    """

    # The two notice types that represent a live opportunity. Award notices
    # and general procurement notices are excluded from the actionable feed.
    OPPORTUNITY_TYPES = (
        "Request for Expression of Interest",
        "Invitation for Bids",
    )

    # --- identity ---------------------------------------------------------
    notice_id = models.CharField(
        "notice id", max_length=64, primary_key=True,
        help_text="Upstream World Bank notice identifier.",
    )
    # Today every row comes from the World Bank. The column exists so other
    # IFIs (ADB, EBRD, AIIB…) can be mirrored into the same table later
    # without a migration of the primary key or the API contract.
    source = models.CharField(
        max_length=32, default="worldbank", db_index=True,
        help_text="Which IFI published this notice.",
    )

    # --- classification ---------------------------------------------------
    notice_type = models.CharField(max_length=255, blank=True, db_index=True)
    notice_status = models.CharField(max_length=64, blank=True)
    notice_language = models.CharField(max_length=64, blank=True)

    # --- dates ------------------------------------------------------------
    notice_date = models.DateField(null=True, blank=True, db_index=True)
    submission_date = models.DateTimeField(null=True, blank=True)
    #: The two halves upstream publishes: a date at midnight UTC, and a
    #: free-text local clock with no zone. Kept raw, exactly as received.
    deadline_date = models.DateTimeField(null=True, blank=True, db_index=True)
    deadline_time = models.CharField(max_length=32, blank=True)
    #: The two halves resolved into one instant, via ``deadlines.py``.
    #:
    #: Stored rather than computed, because the comparison that decides whether
    #: a tender is open has to happen in SQL. Comparing ``deadline_date``
    #: instead — midnight UTC of the closing day — closed every tender at the
    #: start of the day bidding actually ends: measured 2026-08-07, that hid 3
    #: of 27 genuinely open tenders, and the detail page was meanwhile counting
    #: down to the correct instant, so the list and the page disagreed.
    #:
    #: Null only when there is no date at all. An unknown country falls back to
    #: end of day UTC, which is late rather than early — the harm runs one way
    #: here, and showing a closed tender is cheaper than hiding an open one.
    deadline_at = models.DateTimeField(null=True, blank=True, db_index=True)

    # --- project ----------------------------------------------------------
    country = models.CharField(max_length=255, blank=True, db_index=True)
    # The raw upstream key, exactly as published. This is what the sync writes,
    # what ``?project_id=`` filters on, and what survives when no profile has
    # been mirrored — so it stays a plain column, not a relation.
    project_id = models.CharField(max_length=64, blank=True, db_index=True)
    project_name = models.TextField(blank=True)
    # The mirrored profile for that key, when there is one. Two deliberate
    # choices here:
    #
    # * ``db_constraint=False`` — notices almost always arrive before their
    #   project is mirrored, and a real constraint would make the notice sync
    #   depend on an unrelated upstream API being reachable first.
    # * the name ``project_ref``, not ``project`` — a field called ``project``
    #   takes the column ``project_id``, which the raw key above already owns.
    #
    # It is set only when the profile exists, so it never dangles: see
    # ``services.sync.upsert_payloads`` and ``services.projects.link_notices``.
    project_ref = models.ForeignKey(
        "ProjectProfile",
        null=True, blank=True,
        db_constraint=False,
        on_delete=models.SET_NULL,
        to_field="project_id",
        related_name="notices",
    )

    # --- bid --------------------------------------------------------------
    bid_reference_no = models.CharField(max_length=255, blank=True)
    bid_description = models.TextField(blank=True)

    # --- procurement method ----------------------------------------------
    procurement_group = models.CharField(max_length=32, blank=True)
    procurement_method_code = models.CharField(max_length=32, blank=True, db_index=True)
    procurement_method_name = models.CharField(max_length=255, blank=True)

    # --- contact ----------------------------------------------------------
    contact_name = models.CharField(max_length=255, blank=True)
    contact_organization = models.CharField(max_length=512, blank=True)
    contact_email = models.CharField(max_length=320, blank=True)
    contact_phone_no = models.CharField(max_length=128, blank=True)
    contact_address = models.TextField(blank=True)
    contact_country = models.CharField(max_length=255, blank=True)
    contact_web_url = models.CharField(max_length=512, blank=True)

    # --- direction (what kind of work this is) ----------------------------
    category = models.CharField(
        max_length=20, choices=TenderCategory.choices,
        default=TenderCategory.UNKNOWN, db_index=True,
        help_text="Tender direction a company subscribes to.",
    )
    category_source = models.CharField(
        max_length=10, choices=CategorySource.choices, blank=True,
        help_text="How the category was decided: rules, ai, agent, or manual.",
    )
    category_confidence = models.FloatField(null=True, blank=True)
    category_rationale = models.TextField(blank=True)
    category_updated_at = models.DateTimeField(null=True, blank=True)
    # Only Consulting is split further — it is the one direction broad enough
    # that a firm cannot act on it alone. Blank for every other category.
    subcategory = models.CharField(
        max_length=24, choices=ConsultingSubcategory.choices,
        default=ConsultingSubcategory.UNKNOWN, blank=True, db_index=True,
        help_text="Sub-direction within Consulting.",
    )
    subcategory_confidence = models.FloatField(null=True, blank=True)
    # The other axis of the same split: `subcategory` is what the work is,
    # this is who may answer it. Kept apart so "individual IT advisory" stays
    # a question the API can be asked — see `consulting.py`.
    consulting_audience = models.CharField(
        max_length=16, choices=ConsultingAudience.choices,
        default=ConsultingAudience.UNKNOWN, blank=True, db_index=True,
        help_text="Whether a consulting notice addresses firms or individuals.",
    )

    # --- notice body ------------------------------------------------------
    # Sanitised on write; this is the ONLY body field exposed by the API.
    notice_text_sanitized = models.TextField(blank=True)
    # Kept for auditing/re-sanitising. Never serialised to clients.
    notice_text_raw = models.TextField(blank=True)
    #: ``to_tsvector`` of the description and the body, stored rather than
    #: recomputed per query.
    #:
    #: The GIN index added in 0022 makes *matching* cheap and leaves *ranking*
    #: exactly as expensive as it was: a functional index stores nothing to
    #: rank with, so ``ts_rank`` re-parses a full notice body per matching row.
    #: Measured on the deployed corpus (D63), that was 652 ms for a 300-row
    #: sample, and the 300 was itself a bound concealing that a common query
    #: matches 6,744 notices. From this column the same ranking is 68 ms.
    #:
    #: **Maintained by a database trigger, never from Python** (migration
    #: 0024). Not a style preference: the sync writes through ``bulk_update``,
    #: which does not call ``save()`` — as the docstring on ``save`` below
    #: says about ``deadline_at``. A model hook would miss the one path every
    #: notice in the mirror actually arrives on.
    #:
    #: Null means "not yet backfilled", which is why reading it is behind
    #: ``RAG_LEXICAL_STORED_VECTOR``: a partly-filled column does not rank
    #: badly, it drops rows out of the match entirely.
    search_vector = SearchVectorField(
        null=True,
        editable=False,
        help_text=(
            "Maintained by a database trigger, never by application code — "
            "the sync writes through bulk_update, which calls no save(). "
            "Null means not yet backfilled."
        ),
    )

    # --- sync bookkeeping -------------------------------------------------
    content_hash = models.CharField(
        max_length=64, blank=True,
        help_text="SHA-256 of the upstream payload; used to skip no-op writes.",
    )
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)
    last_synced_at = models.DateTimeField(default=timezone.now)

    objects = TenderNoticeQuerySet.as_manager()

    class Meta:
        verbose_name = "tender notice"
        verbose_name_plural = "tender notices"
        # Some historical notices have no notice_date upstream; keep those last
        # instead of letting PostgreSQL's "NULLs first on DESC" surface them.
        ordering = [F("notice_date").desc(nulls_last=True), "-notice_id"]
        indexes = [
            models.Index(fields=["-notice_date"], name="tender_notice_date_desc_idx"),
            models.Index(fields=["deadline_date"], name="tender_deadline_idx"),
            models.Index(fields=["country", "-notice_date"], name="tender_country_date_idx"),
            models.Index(
                fields=["procurement_method_code", "-notice_date"],
                name="tender_method_date_idx",
            ),
            models.Index(fields=["notice_type"], name="tender_notice_type_idx"),
            # Drives the focus feed: region + type + deadline in one index.
            models.Index(
                fields=["country", "notice_type", "deadline_date"],
                name="tender_focus_idx",
            ),
            models.Index(fields=["category", "-notice_date"], name="tender_category_idx"),
            models.Index(fields=["subcategory", "-notice_date"], name="tender_subcat_idx"),
            models.Index(fields=["project_id"], name="tender_project_idx"),
            # --- case-insensitive filters ---------------------------------
            # The public filters are ``iexact`` (so ``?country=uzbekistan``
            # works), which compiles to ``UPPER(col) = UPPER(%s)`` — a
            # predicate no plain column index can serve. These match that
            # expression. Country is indexed alongside the default ordering
            # instead, which only PostgreSQL can express (NULLS LAST is not
            # allowed in a SQLite index): see migration 0006.
            models.Index(Upper("notice_type"), name="tender_type_upper_idx"),
            # ``?procurement_method=`` accepts either column, and an OR falls
            # back to a full scan unless both sides are indexed.
            models.Index(Upper("procurement_method_code"), name="tender_meth_upper_idx"),
            models.Index(Upper("procurement_method_name"), name="tender_methnm_upper_idx"),
            # The operator console lists notices by "last synced" by default.
            models.Index(fields=["-last_synced_at"], name="tender_synced_idx"),
        ]

    def __str__(self) -> str:
        label = self.bid_description or self.project_name or "(untitled notice)"
        return f"{self.notice_id} — {label[:70]}"

    def save(self, *args, **kwargs):
        """Derive ``deadline_at`` on the way in, always.

        The column is a cache of a pure function of three fields already on the
        row, so the one thing it must never be is stale or absent. Deriving it
        at the single write choke point means no caller has to remember — and
        "no caller has to remember" is the difference between a filter that is
        right and one that is right in the paths someone thought about.

        The sync writes through ``bulk_update``, which does not call this, and
        computes the same value in ``services/mapping.py``. That is not a
        second implementation: both call ``resolve_deadline_instant``.
        """
        self.deadline_at = resolve_deadline_instant(
            self.deadline_date, self.deadline_time, self.country
        )
        update_fields = kwargs.get("update_fields")
        if update_fields is not None:
            fields = set(update_fields)
            # Only when the save was already about the deadline. Adding it to
            # an unrelated partial save would widen someone else's write.
            if fields & {"deadline_date", "deadline_time", "country"}:
                kwargs["update_fields"] = fields | {"deadline_at"}
        return super().save(*args, **kwargs)

    @property
    def source_url(self) -> str:
        """Public World Bank page for this notice."""
        return settings.WORLDBANK["NOTICE_DETAIL_URL"].format(id=self.notice_id)

    @property
    def is_open(self) -> bool | None:
        """True/False when a deadline is known, None when it is not.

        Reads ``deadline_at`` so one notice answers this the same way the
        queryset does; ``deadline_date`` is the fallback for a row written
        before the column existed.
        """
        moment = self.deadline_at or self.deadline_date
        if moment is None:
            return None
        return moment >= timezone.now()

    @property
    def days_until_deadline(self) -> int | None:
        moment = self.deadline_at or self.deadline_date
        if moment is None:
            return None
        return (moment - timezone.now()).days

    @property
    def is_opportunity(self) -> bool:
        """True for the two notice types that represent a live opportunity."""
        return self.notice_type in self.OPPORTUNITY_TYPES

    @property
    def is_award(self) -> bool:
        return self.notice_type == "Contract Award"


class ProjectProfileQuerySet(models.QuerySet):
    """Selection of which mirrored projects are worth fetching again.

    Two separate reasons to re-fetch, kept apart because they need different
    orderings and different priorities: a healthy profile that has simply aged,
    and a failed one whose backoff has elapsed.
    """

    def stale(self, now=None):
        """Mirrored successfully, but old enough to be worth re-fetching.

        Rows with no ``fetched_at`` are included: either they predate this
        bookkeeping, or upstream returned nothing the first time.
        """
        now = now or timezone.now()
        cutoff = now - timedelta(days=settings.PROJECTS["REFRESH_DAYS"])
        return self.filter(error_count=0).filter(
            models.Q(fetched_at__isnull=True) | models.Q(fetched_at__lt=cutoff)
        )

    def retry_due(self, now=None):
        """Last attempt failed and the backoff window has passed."""
        return self.filter(
            error_count__gt=0, next_retry_at__lte=now or timezone.now()
        )


class ProjectProfile(models.Model):
    """Everything known about the parent project behind a notice.

    A notice's ``project_id`` (e.g. ``P167598``) is the key to the rest of the
    project: its documents, its financing, and its Environmental and Social
    Review Summary. Those live in two other World Bank APIs, so they are
    mirrored here once per project rather than re-fetched per notice.

    Mirrored once is not mirrored forever, though: see
    :class:`ProjectProfileQuerySet` for when a profile is fetched again.
    """

    project_id = models.CharField(max_length=32, primary_key=True)
    source = models.CharField(max_length=32, default="worldbank", db_index=True)

    name = models.TextField(blank=True)
    country = models.CharField(max_length=255, blank=True, db_index=True)
    status = models.CharField(max_length=64, blank=True)
    lending_instrument = models.CharField(max_length=255, blank=True)
    implementing_agency = models.TextField(blank=True)
    # The World Bank staff accountable for the project, upstream's
    # `teamleadname`. Comma-separated names only — no address is published —
    # but it answers "who at the Bank owns this?", which the notice's own
    # borrower-side contact does not.
    team_lead = models.TextField(blank=True)
    sectors = models.JSONField(default=list, blank=True)
    themes = models.JSONField(default=list, blank=True)

    # Financing — kept as text plus a parsed number, because upstream formats
    # amounts as "20,000,000" strings and occasionally omits them.
    total_amount_display = models.CharField(max_length=64, blank=True)
    total_amount_usd = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    commitment_amount_usd = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )

    board_approval_date = models.DateField(null=True, blank=True)
    closing_date = models.DateField(null=True, blank=True)

    # --- ESRS (Appraisal Environmental and Social Review Summary) ---------
    esrs_report_no = models.CharField(max_length=64, blank=True)
    esrs_date = models.DateField(null=True, blank=True)
    esrs_title = models.TextField(blank=True)
    esrs_pdf_url = models.URLField(max_length=1000, blank=True)
    esrs_page_url = models.URLField(max_length=1000, blank=True)

    documents_count = models.PositiveIntegerField(default=0)
    project_url = models.URLField(max_length=500, blank=True)
    # `fetched_at` is the last *successful* mirror; `last_attempt_at` moves on
    # every try. The gap between them is what marks a profile as broken.
    fetched_at = models.DateTimeField(null=True, blank=True)
    documents_fetched_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    last_error = models.TextField(blank=True)
    # Consecutive failures, reset to 0 by the first clean sync. Drives the
    # backoff below, so a permanently unknown project stops being retried
    # every cycle without ever being written off.
    error_count = models.PositiveIntegerField(default=0)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = ProjectProfileQuerySet.as_manager()

    class Meta:
        verbose_name = "project profile"
        verbose_name_plural = "project profiles"
        ordering = ["project_id"]
        indexes = [
            # The staleness sweep orders by oldest fetch first.
            models.Index(fields=["fetched_at"], name="projprof_fetched_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.project_id} — {(self.name or '')[:60]}"

    @property
    def has_esrs(self) -> bool:
        return bool(self.esrs_pdf_url or self.esrs_report_no)

    @property
    def is_stale(self) -> bool:
        """True when the mirror is old enough to be worth refreshing."""
        if self.fetched_at is None:
            return True
        age = timezone.now() - self.fetched_at
        return age >= timedelta(days=settings.PROJECTS["REFRESH_DAYS"])


class ProjectDocument(models.Model):
    """One published document belonging to a project.

    The title is what the user sees; ``pdf_url`` is what they click. Both come
    from the World Bank Documents & Reports API, keyed by project id.
    """

    guid = models.CharField(max_length=64, primary_key=True)
    project = models.ForeignKey(
        ProjectProfile, on_delete=models.CASCADE, related_name="documents"
    )
    title = models.TextField(blank=True)
    doc_type = models.CharField(max_length=255, blank=True, db_index=True)
    doc_date = models.DateField(null=True, blank=True)
    language = models.CharField(max_length=64, blank=True)
    pdf_url = models.URLField(max_length=1000, blank=True)
    text_url = models.URLField(max_length=1000, blank=True)
    page_url = models.URLField(max_length=1000, blank=True)
    fetched_at = models.DateTimeField(default=timezone.now)

    class Meta:
        verbose_name = "project document"
        verbose_name_plural = "project documents"
        ordering = ["-doc_date", "title"]
        indexes = [
            models.Index(fields=["project", "-doc_date"], name="projdoc_project_date_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.project_id}: {(self.title or self.guid)[:60]}"


class HarvestedDocumentQuerySet(models.QuerySet):
    """Which links are worth spending a request on next.

    Mirrors :class:`ProjectProfileQuerySet`: never-tried rows first, then the
    failures whose backoff has elapsed. Nothing is ever written off — a link
    that is unreachable today may simply be a borrower's server having a bad
    afternoon.
    """

    def fetchable(self):
        """Rows the harvester may spend a request on.

        A client-supplied file has no URL behind it — its ``url`` is a synthetic
        content address — so putting one in the queue would spend an attempt to
        learn it cannot be fetched, then schedule a retry to learn it again.
        The filter lives here rather than at the two call sites so a third one
        cannot forget it.
        """
        return self.filter(origin=HarvestedDocument.Origin.HARVESTED)

    def never_tried(self):
        return self.fetchable().filter(attempts=0)

    def retry_due(self, now=None):
        return self.fetchable().filter(
            attempts__gt=0,
            status__in=HarvestedDocument.RETRYABLE_STATUSES,
            next_retry_at__lte=now or timezone.now(),
        )

    def usable(self):
        """Documents that actually yielded text worth extracting from."""
        return self.filter(
            status=HarvestedDocument.Status.FETCHED,
            text_chars__gte=HarvestedDocument.MIN_USEFUL_CHARS,
        )


class HarvestedDocument(models.Model):
    """A document a notice points at, mirrored before the link dies.

    This exists because of a property of the source data rather than a
    preference: **the links do not last**. A notice's Terms of Reference is
    usually a Google Drive or borrower-hosted file, and once the tender closes
    the folder is taken down or made private. The notice text keeps the URL
    forever; the document behind it is gone within months.

    So the value here is time-dependent and cannot be recovered later: a
    document not mirrored today is one nobody can read next year. That is why
    harvesting runs from the first day, ahead of anything that consumes it.

    Identity is the **URL**, not the notice, because one TOR is routinely
    referenced by several notices of the same project — fetching it per notice
    would multiply the requests against a borrower's server for no new bytes.
    ``notices`` records who pointed at it.

    Storage is content-addressed by ``sha256``: two different URLs serving the
    same file collapse to one blob on disk, which happens often when a borrower
    re-uploads an unchanged document under a new link.
    """

    #: Below this a "successful" fetch produced nothing worth reading — a cover
    #: page, an error page that returned 200, or a scan with no text layer.
    MIN_USEFUL_CHARS = 200

    class Kind(models.TextChoices):
        TOR = "tor", "Terms of Reference"
        BIDDING = "bidding", "Bidding / tender document"
        PROJECT_DOC = "project_doc", "Project document"
        OTHER = "other", "Other linked document"

    class Origin(models.TextChoices):
        HARVESTED = "harvested", "Found via a link we mirrored"
        CLIENT_SUPPLIED = "client_supplied", "Supplied by a vendor"

    class Status(models.TextChoices):
        PENDING = "pending", "Not fetched yet"
        FETCHED = "fetched", "Fetched and parsed"
        # The host answered, but with a page rather than a file — almost always
        # a sign-in wall or a Drive "request access" screen. Distinguished from
        # a transport failure because it says something about the *product*
        # (how much of the corpus is actually reachable), not about the network.
        ACCESS_DENIED = "access_denied", "Reachable, but not public"
        UNREACHABLE = "unreachable", "Could not be fetched"
        # Bytes arrived and were stored, but no text came out: a scan, or a
        # format we cannot read. Kept rather than discarded — the file is on
        # disk and OCR can revisit it without re-fetching anything.
        NO_TEXT = "no_text", "Stored, but no text layer"
        SKIPPED = "skipped", "Rejected before fetching"

    #: Statuses worth another attempt later. ``ACCESS_DENIED`` is included on
    #: purpose: borrowers frequently open a folder up a few days after
    #: publishing it, and that is exactly the window this project lives in.
    RETRYABLE_STATUSES = (Status.UNREACHABLE, Status.ACCESS_DENIED, Status.PENDING)

    #: SHA-256 of the normalised URL. A hash rather than the URL itself because
    #: these routinely exceed any sane ``max_length`` for a primary key.
    url_hash = models.CharField(max_length=64, primary_key=True)
    url = models.TextField()

    kind = models.CharField(max_length=16, choices=Kind.choices, default=Kind.OTHER, db_index=True)
    status = models.CharField(
        max_length=16, choices=Status.choices, default=Status.PENDING, db_index=True
    )
    #: How this document reached us — which is a statement about *provenance*,
    #: not about storage, and the two must not be confused. A harvested
    #: document was published: the notice linked it and anyone could fetch it.
    #: A client-supplied one was not: no notice links it, and it exists here
    #: only because a vendor wrote to the contact the notice publishes, was
    #: sent the file, and passed it on (DECISIONS.md D17).
    #:
    #: Recorded because the difference decides things elsewhere. The harvest
    #: queue must never re-fetch an uploaded file — there is no URL to fetch —
    #: and a coverage figure that counts documents vendors supplied as
    #: documents we could reach would overstate what the mirror can do on its
    #: own.
    origin = models.CharField(
        max_length=16, choices=Origin.choices, default=Origin.HARVESTED, db_index=True
    )

    #: The sentence fragment that introduced the link in the notice body — what
    #: the borrower actually called it. See ``notice_links.extract_links``.
    link_context = models.TextField(blank=True)
    notices = models.ManyToManyField(
        TenderNotice, related_name="harvested_documents", blank=True
    )

    # --- what came back ---------------------------------------------------
    http_status = models.PositiveSmallIntegerField(null=True, blank=True)
    content_type = models.CharField(max_length=128, blank=True)
    byte_size = models.PositiveBigIntegerField(null=True, blank=True)
    #: Of the bytes, not the URL — this is the content-addressed storage key.
    sha256 = models.CharField(max_length=64, blank=True, db_index=True)
    stored_path = models.CharField(max_length=512, blank=True)

    # --- extracted text ---------------------------------------------------
    text = models.TextField(blank=True)
    text_chars = models.PositiveIntegerField(default=0)
    page_count = models.PositiveIntegerField(null=True, blank=True)
    #: ``None`` until a fetch happened. ``False`` is the answer that matters:
    #: it means a scanned document, which no amount of parsing will read.
    has_text_layer = models.BooleanField(null=True, blank=True)
    parser = models.CharField(max_length=16, blank=True)
    parse_error = models.TextField(blank=True)

    # --- bookkeeping ------------------------------------------------------
    attempts = models.PositiveSmallIntegerField(default=0)
    last_error = models.TextField(blank=True)
    fetched_at = models.DateTimeField(null=True, blank=True)
    last_attempt_at = models.DateTimeField(null=True, blank=True)
    next_retry_at = models.DateTimeField(null=True, blank=True, db_index=True)
    created_at = models.DateTimeField(auto_now_add=True, db_index=True)
    updated_at = models.DateTimeField(auto_now=True)

    objects = HarvestedDocumentQuerySet.as_manager()

    class Meta:
        verbose_name = "harvested document"
        verbose_name_plural = "harvested documents"
        ordering = ["-created_at"]
        indexes = [
            models.Index(fields=["status", "kind"], name="harvestdoc_status_kind_idx"),
            models.Index(fields=["-created_at"], name="harvestdoc_created_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.kind}:{self.status} {self.url[:70]}"

    @property
    def is_usable(self) -> bool:
        return (
            self.status == self.Status.FETCHED
            and self.text_chars >= self.MIN_USEFUL_CHARS
        )


class ContractAward(models.Model):
    """Structured award details parsed out of a Contract Award notice.

    Upstream publishes the winner, the price and the contract duration only as
    prose inside ``notice_text``. Parsing it into columns is what makes
    competitor analysis possible — who keeps winning, at what price, where.
    """

    notice = models.OneToOneField(
        TenderNotice, on_delete=models.CASCADE, related_name="award", primary_key=True
    )

    supplier_name = models.CharField(max_length=512, blank=True, db_index=True)
    supplier_reference = models.CharField(max_length=64, blank=True)
    supplier_address = models.TextField(blank=True)
    supplier_country = models.CharField(max_length=255, blank=True, db_index=True)

    # Wide enough for a name, not just a code. The main award template prints
    # an ISO code ("BDT"), but the Small Assignment one prints the currency's
    # name ("Kyrgyzstan Som"). Storing what upstream printed keeps this column
    # free of a names-to-ISO mapping nobody here has a source for; the front
    # end already renders a non-code as "NAME 1 234,56".
    currency = models.CharField(max_length=64, blank=True)
    bid_price_opening = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    evaluated_price = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )
    contract_price = models.DecimalField(
        max_digits=18, decimal_places=2, null=True, blank=True
    )

    award_date = models.DateField(null=True, blank=True)
    contract_duration = models.CharField(max_length=64, blank=True)

    # The three lists the notice publishes separately, kept separate here.
    # `supplier_*` above is the first entry of `awarded_bidders` flattened —
    # a joint venture wins as several firms, and the flat columns cannot say
    # so without either double-counting the contract or naming a company that
    # does not exist. Merging "evaluated" into "rejected" would be the same
    # mistake in the other direction: losing on price and being ruled
    # non-responsive are different facts about a competitor.
    awarded_bidders = models.JSONField(
        default=list, blank=True,
        help_text="Every firm named as an awardee — one entry unless it is a joint venture.",
    )
    evaluated_bidders = models.JSONField(
        default=list, blank=True,
        help_text="Bidders evaluated against the winner and not selected.",
    )
    rejected_bidders = models.JSONField(
        default=list, blank=True,
        help_text="Bidders rejected before evaluation, with the reason where published.",
    )

    # --- competitor enrichment (optional, AI-assisted) --------------------
    supplier_website = models.URLField(max_length=500, blank=True)
    supplier_website_source = models.CharField(max_length=32, blank=True)
    supplier_website_checked_at = models.DateTimeField(null=True, blank=True)

    parsed_at = models.DateTimeField(default=timezone.now)
    parser_version = models.PositiveSmallIntegerField(default=1)

    class Meta:
        verbose_name = "contract award"
        verbose_name_plural = "contract awards"
        ordering = ["-award_date"]
        indexes = [
            models.Index(fields=["supplier_name"], name="award_supplier_idx"),
            models.Index(fields=["-award_date"], name="award_date_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.notice_id} → {self.supplier_name or 'unknown supplier'}"


class TeamLeadProfile(models.Model):
    """What is known about one World Bank task team leader.

    The projects feed publishes team leads as bare names — no address, no
    title, no unit — which makes them the one contact tier a user cannot act
    on. This table is where that gap is filled in, once per person rather than
    once per notice: the same team lead runs several projects, and each of
    those projects issues many notices.

    Two deliberate limits on what is stored:

    * **Work addresses only.** The Bank's staff addresses follow a documented
      pattern, so a candidate can be derived from the name and then confirmed;
      anything else is a guess about a private individual and is not kept.
      ``email_source`` records which of the two happened, and the API never
      presents a derived address as a confirmed one.
    * **Public links, not scraped pages.** ``links`` holds URLs a search
      surfaced — a Bank staff page, a published paper, a conference bio. The
      pages behind them are never fetched or mirrored here.
    """

    class EmailSource(models.TextChoices):
        PATTERN = "pattern", "Derived from the staff address pattern"
        VERIFIED = "verified", "Found published on a Bank page"

    #: Normalised name (see ``apps.tenders.contacts._normalise_name``), so the
    #: same person spelled slightly differently across two projects collapses
    #: to one row instead of being enriched twice.
    slug = models.CharField(max_length=160, primary_key=True)
    name = models.CharField(max_length=255, db_index=True)

    title = models.CharField(max_length=255, blank=True)
    unit = models.CharField(
        max_length=255, blank=True,
        help_text="Global Practice or department, as published.",
    )
    country_office = models.CharField(max_length=255, blank=True)

    work_email = models.CharField(max_length=320, blank=True)
    email_source = models.CharField(max_length=16, choices=EmailSource.choices, blank=True)
    email_confidence = models.FloatField(null=True, blank=True)

    profile_url = models.URLField(max_length=500, blank=True)
    links = models.JSONField(
        default=list, blank=True,
        help_text="Public URLs found for this person: [{url, kind, label}].",
    )
    summary = models.TextField(blank=True)

    # --- the Bank's own staff page (first-party, no search involved) -------
    # For staff who write on the Bank's blog there is an author page carrying
    # their official title, a full biography and a portrait — published by the
    # employer, about the employee, in their professional capacity. It beats
    # anything a search returns and costs one HTTP GET.
    bank_page_url = models.URLField(max_length=500, blank=True)
    bio = models.TextField(
        blank=True,
        help_text="Biography as published on the World Bank author page.",
    )
    #: Referenced, never copied: the portrait stays hosted by the Bank, so it
    #: remains theirs to change or withdraw.
    photo_url = models.URLField(max_length=700, blank=True)
    bank_page_checked_at = models.DateTimeField(null=True, blank=True)

    source = models.CharField(max_length=32, blank=True)
    checked_at = models.DateTimeField(null=True, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "team lead profile"
        verbose_name_plural = "team lead profiles"
        ordering = ["name"]

    def __str__(self) -> str:
        return self.name

    @property
    def is_email_confirmed(self) -> bool:
        """Whether the address was seen published, rather than derived."""
        return self.email_source == self.EmailSource.VERIFIED


class BackfillPartition(models.Model):
    """One resumable slice of the historical archive.

    The upstream search engine caps the offset (``os``) at 100 000 rows per
    query, while the full archive holds ~413 000 notices. A single unfiltered
    walk therefore cannot reach the older material, so history is pulled in
    partitions — one per country (``project_ctry_name``), each comfortably
    under the cap — plus one unfiltered partition for the newest 100 000.

    Each row is a checkpoint: ``next_offset`` is where the next slice resumes,
    so a restart, a crash, or a rate-limited window never loses progress.
    """

    class Kind(models.TextChoices):
        RECENT = "recent", "Recent (unfiltered)"
        COUNTRY = "country", "By country"
        COUNTRY_METHOD = "country_method", "By country + procurement method"

    class Status(models.TextChoices):
        PENDING = "pending", "Pending"
        RUNNING = "running", "Running"
        COMPLETED = "completed", "Completed"
        # Upstream reports more rows than the offset cap can reach: the
        # partition was split into finer ones instead.
        SUBDIVIDED = "subdivided", "Subdivided"
        FAILED = "failed", "Failed"

    key = models.CharField(
        max_length=255, unique=True,
        help_text="Stable identifier, e.g. 'recent' or 'country:India'.",
    )
    kind = models.CharField(max_length=20, choices=Kind.choices, default=Kind.COUNTRY)
    label = models.CharField(max_length=255, blank=True)
    filters = models.JSONField(
        default=dict, blank=True,
        help_text="Upstream query parameters that define this partition.",
    )

    status = models.CharField(max_length=16, choices=Status.choices, default=Status.PENDING)
    next_offset = models.PositiveIntegerField(default=0)
    upstream_total = models.PositiveIntegerField(null=True, blank=True)

    pages_done = models.PositiveIntegerField(default=0)
    pages_failed = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    unchanged_count = models.PositiveIntegerField(default=0)

    last_error = models.TextField(blank=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)
    started_at = models.DateTimeField(null=True, blank=True)
    finished_at = models.DateTimeField(null=True, blank=True)

    class Meta:
        verbose_name = "backfill partition"
        verbose_name_plural = "backfill partitions"
        ordering = ["kind", "key"]
        indexes = [
            models.Index(fields=["status", "kind"], name="backfill_status_kind_idx"),
        ]

    def __str__(self) -> str:
        return f"{self.key} [{self.status}] {self.progress_percent:.0f}%"

    @property
    def reachable_total(self) -> int | None:
        """Rows this partition can actually reach, given the offset cap."""
        if self.upstream_total is None:
            return None
        return min(self.upstream_total, settings.WORLDBANK["MAX_OFFSET"])

    @property
    def progress_percent(self) -> float:
        target = self.reachable_total
        if not target:
            return 100.0 if self.status == self.Status.COMPLETED else 0.0
        return min(100.0, round(self.next_offset / target * 100, 1))

    @property
    def is_done(self) -> bool:
        return self.status in {self.Status.COMPLETED, self.Status.SUBDIVIDED}


class SyncRun(models.Model):
    """Audit trail for each sync execution — visible in the Django admin."""

    class Status(models.TextChoices):
        RUNNING = "running", "Running"
        SUCCESS = "success", "Success"
        PARTIAL = "partial", "Partial (some pages failed)"
        FAILED = "failed", "Failed"

    started_at = models.DateTimeField(default=timezone.now, db_index=True)
    finished_at = models.DateTimeField(null=True, blank=True)
    status = models.CharField(max_length=16, choices=Status.choices, default=Status.RUNNING)
    trigger = models.CharField(
        max_length=32, default="celery-beat",
        help_text="What started this run: celery-beat, manual, startup…",
    )

    pages_requested = models.PositiveIntegerField(default=0)
    pages_fetched = models.PositiveIntegerField(default=0)
    pages_failed = models.PositiveIntegerField(default=0)
    notices_seen = models.PositiveIntegerField(default=0)
    created_count = models.PositiveIntegerField(default=0)
    updated_count = models.PositiveIntegerField(default=0)
    unchanged_count = models.PositiveIntegerField(default=0)
    skipped_count = models.PositiveIntegerField(default=0)
    # Notices the run fetched and then declined to store because their country
    # is outside the focus group (settings.INGEST_FOCUS_ONLY). Separate from
    # `skipped_count`: that one means upstream sent something unusable, this
    # one means we chose not to keep it.
    out_of_scope_count = models.PositiveIntegerField(default=0)
    upstream_total = models.BigIntegerField(null=True, blank=True)
    error_message = models.TextField(blank=True)

    class Meta:
        verbose_name = "sync run"
        verbose_name_plural = "sync runs"
        ordering = ["-started_at"]

    def __str__(self) -> str:
        return f"Sync {self.started_at:%Y-%m-%d %H:%M} [{self.status}]"

    @property
    def duration_seconds(self) -> float | None:
        if not self.finished_at:
            return None
        return round((self.finished_at - self.started_at).total_seconds(), 2)
