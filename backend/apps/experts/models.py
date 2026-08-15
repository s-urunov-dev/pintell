"""The people a vendor can put forward, and the taxonomy of what they do.

Consulting tenders do not only ask what a firm has done — they name the experts
the assignment needs: a Team Leader, a Resettlement Specialist, an Auditor. A
vendor missing one of them cannot submit at all, and today goes looking for that
person somewhere else. This directory is where they look instead.

It sits beside the compliance stack rather than inside it. What a tender demands
is *extracted* and must carry a verbatim quote (DECISIONS.md D4); who is
available to satisfy the demand is a directory a human maintains. Mixing the two
would put unquoted, human-entered rows in the same table as evidence, which is
exactly the confusion the grounding rule exists to prevent.

Two modelling decisions are worth stating, because the obvious alternative was
rejected in each case.

**The taxonomy is one self-referential table, not two.** A family
("Environmental and social") and a role inside it ("Gender Specialist") differ
only in whether anything sits above them, and every question asked of one is
asked of the other: name it, list its signal terms, count the experts under it.
A second model would restate all of that to encode a distinction a nullable
``parent`` already carries, and every listing would become a union of two
queries. Django's ``ContentType`` framework — the other thing "one table for
both levels" can mean — is for pointing a relation at *different models*; here
both levels are the same model, so it would add a join and a layer of
indirection for nothing.

**The tree is exactly two levels deep, and that is enforced rather than
assumed.** Allowing a role under a role would break the property the shape is
for: every expert hangs off a leaf, so the experts in a family are one join
away, not a recursive walk. ``clean()`` and ``save()`` both refuse it, because
loading data is not always a form submission.
"""

from __future__ import annotations

from django.core.exceptions import ValidationError
from django.db import models

from .linkedin import normalise_profile_url


class ExpertTypeQuerySet(models.QuerySet):
    def families(self) -> "ExpertTypeQuerySet":
        """The top level — the five headings a vendor browses first."""
        return self.filter(parent__isnull=True)

    def roles(self) -> "ExpertTypeQuerySet":
        """The leaves — the only rows an expert may be tagged with."""
        return self.filter(parent__isnull=False)


class ExpertType(models.Model):
    """One node of the expertise taxonomy: a family, or a role inside one.

    The content — five families, the roles under them and the signal terms of
    each — is the CEO's list, and it ships as a fixture rather than as rows a
    migration creates, so that reloading it after an edit is one command and
    not a data migration nobody dares re-run::

        python manage.py loaddata expert_types

    ``slug`` is the primary key precisely so that command is idempotent: the
    fixture names its rows, so loading it twice updates the same 41 rows
    instead of appending a second copy of the taxonomy.
    """

    #: Stable identifier, and the fixture's key. Not an integer: the taxonomy is
    #: referenced from prompts, tests and (later) the front end, and a name that
    #: reads is worth more there than a number that sorts.
    slug = models.SlugField(max_length=80, primary_key=True)

    name = models.CharField(max_length=160)

    #: ``NULL`` for a family. PROTECT rather than CASCADE: deleting a family
    #: that still has roles under it would take a slice of the directory's
    #: vocabulary with it, and the experts tagged with those roles would lose
    #: what they are. Empty the family first, deliberately.
    parent = models.ForeignKey(
        "self",
        null=True,
        blank=True,
        on_delete=models.PROTECT,
        related_name="children",
        limit_choices_to={"parent__isnull": True},
        help_text="Leave empty for a top-level family.",
    )

    #: What a tender writer says when they mean this role — "logframe" for M&E,
    #: "entitlement matrix" for resettlement. Stored as written in the source
    #: list, ranges included ("ESS1-ESS10"), because expanding them would be
    #: this module inventing vocabulary it was handed.
    #:
    #: These are **search and browsing vocabulary, not evidence**. Nothing here
    #: ever reaches a compliance verdict: a verdict needs a quote from the
    #: notice, and a term in this column is a quote from nothing.
    signal_terms = models.JSONField(
        default=list,
        blank=True,
        help_text="Terms that signal this role in a tender. Search aid only — "
        "never an input to a compliance verdict.",
    )

    #: The source list has an order that carries meaning — "deyarli har bir
    #: tenderda bor" is why project management is first — and alphabetical
    #: sorting would throw it away. Ties fall back to the name.
    position = models.PositiveSmallIntegerField(default=0)

    # No created_at / updated_at here, unlike every other table in the project.
    # These rows are shipped vocabulary, not events: they arrive with the
    # fixture, and "when was ESS7 added" is a question the git history answers
    # better than a column would. It also keeps `loaddata` honest — a raw
    # fixture load does not fill auto-timestamps, so the columns would have had
    # to be written into the fixture by hand and would then be fiction.
    objects = ExpertTypeQuerySet.as_manager()

    class Meta:
        verbose_name = "expert type"
        verbose_name_plural = "expert types"
        ordering = ["position", "name"]
        indexes = [models.Index(fields=["parent", "position"])]

    def __str__(self) -> str:
        return self.name

    @property
    def is_family(self) -> bool:
        return self.parent_id is None

    def clean(self) -> None:
        super().clean()
        self._assert_two_levels()

    def save(self, *args, **kwargs):
        # Also checked here, not only in clean(): fixtures, shells and data
        # migrations never call full_clean(), and a third level created that way
        # would be found much later, by a listing that silently lost a role.
        self._assert_two_levels()
        return super().save(*args, **kwargs)

    def _assert_two_levels(self) -> None:
        if self.parent_id is None:
            return
        if self.parent_id == self.slug:
            raise ValidationError({"parent": "A type cannot be its own family."})
        parent = self.parent if self.parent_id else None
        if parent is not None and parent.parent_id is not None:
            raise ValidationError(
                {"parent": "The taxonomy is two levels deep: pick a family, not a role."}
            )


class Expert(models.Model):
    """One person the directory can point a vendor at.

    Deliberately thin. What is stored is what the person publishes about their
    professional life anyway — their name, their public profile link, and the
    roles they work as. No contact details, no rates, no CV: those are things
    the expert would have to consent to us holding, and none of them is needed
    for the question this table answers, which is *who exists and where do I
    reach them publicly*.
    """

    full_name = models.CharField(max_length=200, db_index=True)

    #: Canonicalised on the way in (see ``linkedin.py``) so the same person
    #: pasted four ways is one row. Blank is allowed — an expert can be known to
    #: the team before their profile link is; the uniqueness rule below is
    #: written to tolerate that.
    linkedin_url = models.URLField(max_length=300, blank=True)

    #: Roles only. A person is "a Gender Specialist", never "an Environmental
    #: and social" — the family is reached through the role. Many-to-many
    #: because holding two roles is the norm in this market, not the exception,
    #: and a single foreign key would force the same person into two rows and
    #: hand vendors the duplicate problem the directory exists to remove.
    types = models.ManyToManyField(
        ExpertType,
        related_name="experts",
        blank=True,
        limit_choices_to={"parent__isnull": False},
    )

    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = "expert"
        verbose_name_plural = "experts"
        ordering = ["full_name"]
        constraints = [
            # Unique where present, unconstrained where absent. A plain
            # ``unique=True`` would make the second expert without a link fail
            # to save, because in SQL every empty string equals every other one.
            models.UniqueConstraint(
                fields=["linkedin_url"],
                condition=~models.Q(linkedin_url=""),
                name="expert_unique_linkedin_url",
            )
        ]

    def __str__(self) -> str:
        return self.full_name

    def clean(self) -> None:
        super().clean()
        self.full_name = self.full_name.strip()
        # Normalising in clean() as well as save() is what makes the admin
        # report a bad link against the field instead of raising mid-save.
        self.linkedin_url = normalise_profile_url(self.linkedin_url)

    def save(self, *args, **kwargs):
        self.full_name = self.full_name.strip()
        self.linkedin_url = normalise_profile_url(self.linkedin_url)
        return super().save(*args, **kwargs)
