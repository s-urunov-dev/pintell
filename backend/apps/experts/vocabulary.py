"""The role vocabulary, in the form an extraction schema can constrain against.

The problem this solves is a matching problem that is much better avoided than
solved. A tender writes the position it needs in its own words — "Environmental
and Social Safeguards Expert", "Specialist in Involuntary Resettlement", "M&E
Officer" — and the directory files people under 36 fixed roles. Something has to
bridge the two.

The obvious bridge is fuzzy string matching after the fact, and it is the wrong
one: it is unbounded work, it fails silently on the phrasings it has not seen,
and every near-miss becomes a person shown for a role they do not hold. So the
bridge is moved *into the extraction call*: the role slugs go into the JSON
schema as an ``enum``, and the model is required to answer inside that
vocabulary or to say ``other``. What comes back is already a key in this
database — no matching step exists to be wrong.

Two consequences worth stating.

**``other`` is a real answer, and it is a measurement.** A tender asking for a
position none of the 36 covers is a gap in the CEO's list, not a failure of the
run: the title is kept verbatim, the role is left unset, and how often that
happens is exactly the number that says whether the taxonomy needs a 37th row.

**The vocabulary is read fresh, not cached in the process.** It is one small
indexed query against a 41-row table, made once per notice, next to an API call
that costs several orders of magnitude more. A process-level cache would buy
nothing measurable and would go stale the moment someone loads a corrected
fixture into a running worker.
"""

from __future__ import annotations

from .models import ExpertType

#: Bump when the shipped taxonomy changes in a way that could move extraction
#: results — a role added, removed, or renamed. It rides on ``ExtractionRun``'s
#: prompt version so a shift in what tenders appear to ask for is attributable
#: to the vocabulary having changed rather than to the model or the prompt.
VOCABULARY_VERSION = "x1"

#: What the model answers when no role in the taxonomy fits. Not a slug, and
#: deliberately not storable as one: it maps to *no* row, which is the honest
#: outcome. Chosen with a prefix that cannot collide with a real slug.
UNMAPPED = "__other__"


def role_slugs() -> list[str]:
    """Every role a tender's position may be classified as, plus ``UNMAPPED``.

    Ordered by the taxonomy's own ordering rather than alphabetically, so two
    runs build byte-identical schemas — a schema that reshuffles between calls
    would make the ablation compare two different questions.
    """
    slugs = list(
        ExpertType.objects.roles()
        .order_by("parent__position", "position", "slug")
        .values_list("slug", flat=True)
    )
    return [*slugs, UNMAPPED]


def resolve(slug: str | None) -> ExpertType | None:
    """The role row for a slug the model returned, or ``None``.

    ``None`` covers three cases that are all the same downstream — the model
    said ``other``, said nothing, or named a slug that has since been removed
    from the taxonomy. In each the position is kept with its verbatim title and
    no role attached, because a position we cannot file is still a position the
    tender asked for.
    """
    if not slug or slug == UNMAPPED:
        return None
    return ExpertType.objects.roles().filter(slug=slug).first()
