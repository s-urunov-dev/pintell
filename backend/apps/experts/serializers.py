"""Serializers for the expert directory.

Two rules shape what leaves this module, and both are about the boundary
between a curated directory and an extracted requirement.

**Signal terms never ship.** They are the vocabulary the directory searches
itself with — "logframe", "entitlement matrix" — and they read, on a page, like
a statement about what a tender demands. They are not: nobody wrote them into
any tender. Leaving them out of the API is what keeps a browsing aid from
becoming a claim (D20, and docs/OPEN-QUESTIONS.md Q15's original worry).

**An expert is a name and a public link, and that is all there is to serialise.**
There is no contact field to omit because there is no contact field to store.
"""

from __future__ import annotations

from rest_framework import serializers

from .models import Expert, ExpertType


class ExpertTypeSerializer(serializers.ModelSerializer):
    """One role, with the family it sits under.

    ``family`` is flattened to a slug and a name rather than nested: every
    consumer wants to group by it or label it, and neither needs a second
    object to unwrap.
    """

    family = serializers.SlugRelatedField(
        source="parent", slug_field="slug", read_only=True
    )
    family_name = serializers.CharField(source="parent.name", read_only=True, default="")
    expert_count = serializers.IntegerField(read_only=True)

    class Meta:
        model = ExpertType
        fields = ("slug", "name", "family", "family_name", "expert_count")


class ExpertFamilySerializer(serializers.ModelSerializer):
    """A family with its roles — the shape the browsing UI groups by."""

    roles = ExpertTypeSerializer(source="children", many=True, read_only=True)

    class Meta:
        model = ExpertType
        fields = ("slug", "name", "roles")


class ExpertSerializer(serializers.ModelSerializer):
    """One person in the directory.

    ``roles`` is the full role objects rather than slugs: the list UI shows a
    person's roles as labels next to their name, and a client that received
    slugs would have to hold the whole taxonomy to render one row.
    """

    roles = ExpertTypeSerializer(source="types", many=True, read_only=True)

    class Meta:
        model = Expert
        fields = ("id", "full_name", "linkedin_url", "roles")
