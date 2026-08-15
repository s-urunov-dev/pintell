"""Serializers for the compliance API.

The validation here is deliberately **shape-only**. It refuses payloads the
decision engine cannot evaluate — a nested object where a number belongs, a
collection that is not a list of records — and refuses nothing else. It does
not know that turnover is money, that a certificate expires, or which keys a
World Bank criterion will ask for, because none of that is settled: the
requirement taxonomy is still being derived from the gold set (see the comment
on ``TenderRequirement.key``), and a serializer that enforced a guessed field
list would reject profiles that later turn out to be correct.

Two normalisations are applied, and both come from the engine's own contract.

**A blank value is dropped, not stored.** ``Portfolio`` treats an absent key
and a key set to ``None`` as the same state — not declared — and the engine
turns that into ``UNKNOWN`` rather than a failure. An empty string is a third
spelling of the same thing that the engine does *not* recognise: ``""`` is a
declared value, and it compares as unknown against every threshold forever. A
form field left blank must land on the state the engine understands.

**Numbers stay as the client sent them.** ``_number`` in the engine already
parses ``"12,000,000"``, and re-typing values here would mean two parsers that
can disagree about the same input.
"""

from __future__ import annotations

from typing import Any

from rest_framework import serializers

from .models import TenderExpertPosition, TenderRequirement, VendorProfile

#: JSON types the engine can compare. Lists and objects are excluded because no
#: comparator in ``expressions._compare`` can produce anything but ``UNKNOWN``
#: for them — storing one would create a value that is permanently unreadable
#: while looking, in the UI, like an answered question.
_SCALAR_TYPES = (str, int, float, bool)

#: Matches ``TenderRequirement.key``. A portfolio key and a requirement key are
#: the same taxonomy read from two sides, so they get the same ceiling.
_MAX_KEY_LENGTH = 64


def _is_scalar(value: Any) -> bool:
    return value is None or isinstance(value, _SCALAR_TYPES)


def _blank(value: Any) -> bool:
    """Whether a declared value carries no information.

    ``False`` and ``0`` are real declarations and must survive this: a bidder
    who states zero completed contracts has answered the question, and the
    engine distinguishes that from silence.
    """
    return value is None or (isinstance(value, str) and not value.strip())


def _check_key(key: Any, where: str) -> str:
    if not isinstance(key, str) or not key.strip():
        raise serializers.ValidationError(f"{where}: keys must be non-empty strings.")
    if len(key) > _MAX_KEY_LENGTH:
        raise serializers.ValidationError(
            f"{where}: key '{key[:20]}…' is longer than {_MAX_KEY_LENGTH} characters."
        )
    return key


class VendorProfileSerializer(serializers.ModelSerializer):
    """What one vendor declares about itself.

    A partially filled profile is a normal state, not an invalid one — that is
    the whole reason ``UNKNOWN`` exists — so every declaration field is
    optional and only ``name`` is required.
    """

    class Meta:
        model = VendorProfile
        fields = (
            "id",
            "name",
            "country",
            "scalars",
            "collections",
            "consented_at",
            "created_at",
            "updated_at",
        )
        read_only_fields = ("id", "created_at", "updated_at")

    def validate_scalars(self, value: Any) -> dict[str, Any]:
        """Single declared values: ``{"annual_turnover_avg": 12000000}``."""
        if not isinstance(value, dict):
            raise serializers.ValidationError("Expected an object of key → value.")

        cleaned: dict[str, Any] = {}
        for key, item in value.items():
            _check_key(key, "scalars")
            if not _is_scalar(item):
                raise serializers.ValidationError(
                    f"scalars.{key}: expected a number, string or boolean."
                )
            if _blank(item):
                # Not an error — the field was simply left empty. Dropping the
                # key is what makes the engine say "not established yet".
                continue
            cleaned[key] = item
        return cleaned

    def validate_collections(self, value: Any) -> dict[str, list[dict[str, Any]]]:
        """Record sets a count or an ``exists`` runs over.

        An empty list is kept rather than dropped. "I have no completed
        contracts" is a declaration the engine reads as a genuine failure,
        while an absent key is a question still open — the two must not be
        collapsed here.
        """
        if not isinstance(value, dict):
            raise serializers.ValidationError("Expected an object of entity → records.")

        cleaned: dict[str, list[dict[str, Any]]] = {}
        for entity, records in value.items():
            _check_key(entity, "collections")
            if not isinstance(records, list):
                raise serializers.ValidationError(
                    f"collections.{entity}: expected a list of records."
                )

            rows: list[dict[str, Any]] = []
            for index, record in enumerate(records):
                if not isinstance(record, dict):
                    raise serializers.ValidationError(
                        f"collections.{entity}[{index}]: expected an object."
                    )
                row: dict[str, Any] = {}
                for field, item in record.items():
                    _check_key(field, f"collections.{entity}[{index}]")
                    if not _is_scalar(item):
                        raise serializers.ValidationError(
                            f"collections.{entity}[{index}].{field}: "
                            "expected a number, string or boolean."
                        )
                    if _blank(item):
                        continue
                    row[field] = item
                # A record with nothing in it is dropped: it cannot match any
                # filter, and counting it would inflate a portfolio with rows
                # that say nothing.
                if row:
                    rows.append(row)
            cleaned[entity] = rows
        return cleaned


class TenderRequirementSerializer(serializers.ModelSerializer):
    """One extracted requirement, without a bidder.

    Serves the "what does this tender ask for" view, which is useful before a
    profile exists. ``grounding`` is exposed rather than hidden: a requirement
    the verifier has not checked yet is a weaker claim than a verified one, and
    the reader is entitled to know which they are looking at.
    """

    source_document = serializers.SerializerMethodField()
    label = serializers.SerializerMethodField()

    class Meta:
        model = TenderRequirement
        fields = (
            "id",
            "key",
            "label",
            "importance",
            "layer",
            "expression",
            "applies_to",
            "is_mandatory",
            "evidence_quote",
            "source",
            "source_document",
            "grounding",
            "created_at",
        )
        read_only_fields = fields

    def get_label(self, obj: TenderRequirement) -> str:
        """The criterion named in the reader's language.

        One field rather than three, and resolved here rather than in the
        client, because the choice is the same one the assessment endpoint makes
        (``views._parse``) and two places choosing it differently would show a
        vendor the criterion in one language on the tender page and another on
        their assessment.

        The context key is optional: a caller that does not set it gets English,
        which is what every source document is written in and therefore the only
        safe default for a serializer that might be rendered outside a request.
        """
        return obj.label_for(self.context.get("lang", "en"))


    # The run that produced the row is not nested here. An assessment carries
    # it inline per requirement (``views._parse``), and a second, differently
    # shaped copy of the same provenance is how two representations of one
    # record start to disagree.

    def get_source_document(self, obj: TenderRequirement) -> dict | None:
        """Where the quote can be checked, when it came from a mirrored file.

        The identity, the original URL and the harvest state — nothing more.
        The stored text is not inlined: a bidding document runs to tens of
        thousands of characters and would dominate a payload whose job is to
        explain a verdict. ``status`` is included because a link that has since
        died is still worth showing as the provenance of a requirement, and the
        reader should be able to tell that is what they are looking at.
        """
        document = obj.source_document
        if document is None:
            return None
        return {
            "id": document.pk,
            "url": document.url,
            "kind": document.kind,
            "status": document.status,
        }


class TenderExpertPositionSerializer(serializers.ModelSerializer):
    """One expert position a tender asks for, with the role it files under.

    Shaped to be read next to a verdict without being mistaken for one. It
    carries the same provenance fields as a requirement — layer, quote,
    grounding — because it was read the same way and deserves the same
    scepticism, and it carries no verdict field at all because nothing here is
    evaluated (D20).

    ``role`` is null when the directory's taxonomy has no row for the position.
    That is a real answer and the client must render it: the tender is asking
    for someone we cannot file, and ``title`` still says who.
    """

    role = serializers.SlugRelatedField(slug_field="slug", read_only=True)
    role_name = serializers.CharField(source="role.name", read_only=True, default="")
    family = serializers.CharField(
        source="role.parent.slug", read_only=True, default=""
    )

    class Meta:
        model = TenderExpertPosition
        fields = (
            "id",
            "title",
            "role",
            "role_name",
            "family",
            "count",
            "is_mandatory",
            "layer",
            "evidence_quote",
            "source",
            "grounding",
        )
        read_only_fields = fields


class DeclarationWriteSerializer(serializers.Serializer):
    """One switch on the vendor's assessment page.

    ``satisfied`` allows null on purpose, and it is a third state rather than a
    sloppy boolean: *yes*, *no*, and *withdraw the answer I gave*. Without the
    third, a vendor who ticked a box by mistake could only correct it to "no",
    which is a claim they did not intend to make and which the engine would then
    treat as a definite failure.
    """

    requirement_id = serializers.IntegerField()
    satisfied = serializers.BooleanField(allow_null=True)
