"""Country groups used by the aggregation filters.

The first stage of the product targets CIS countries plus Afghanistan. The
group is defined here (not in the database) because it is a business decision,
not upstream data — and because the same group must be applied identically by
the sync, the API filters and the UI.

Names must match the World Bank's ``project_ctry_name`` values exactly; that
is what both the upstream query parameter and the local column contain.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True, slots=True)
class Country:
    name: str
    flag: str
    note: str = ""


# CIS members + Afghanistan. Flags are UI sugar; `name` is the matching key.
CIS_PLUS_COUNTRIES: tuple[Country, ...] = (
    Country("Armenia", "🇦🇲"),
    Country("Azerbaijan", "🇦🇿"),
    Country("Belarus", "🇧🇾"),
    Country("Kazakhstan", "🇰🇿"),
    Country("Kyrgyz Republic", "🇰🇬", "Upstream spells Kyrgyzstan as 'Kyrgyz Republic'."),
    Country(
        "Moldova",
        "🇲🇩",
        "Winding down participation but still formally on record.",
    ),
    Country("Russian Federation", "🇷🇺", "Upstream spells Russia as 'Russian Federation'."),
    Country("Tajikistan", "🇹🇯"),
    Country("Uzbekistan", "🇺🇿"),
    Country("Afghanistan", "🇦🇫", "Not a CIS member; included by product decision."),
)

# Alternative spellings seen upstream, mapped to the canonical entry above.
COUNTRY_ALIASES: dict[str, str] = {
    "kyrgyzstan": "Kyrgyz Republic",
    "russia": "Russian Federation",
    "republic of moldova": "Moldova",
}

COUNTRY_GROUPS: dict[str, dict[str, object]] = {
    "cis_plus": {
        "label": "CIS & Afghanistan",
        "description": "CIS member states plus Afghanistan — the first-stage focus region.",
        "countries": [c.name for c in CIS_PLUS_COUNTRIES],
    },
}

DEFAULT_COUNTRY_GROUP = "cis_plus"


def group_countries(group: str) -> list[str]:
    """Country names in ``group`` (empty list when the group is unknown)."""
    entry = COUNTRY_GROUPS.get((group or "").strip().lower())
    if not entry:
        return []
    return list(entry["countries"])  # type: ignore[arg-type]


def canonical_country(name: str) -> str:
    """Resolve an alternative spelling to the canonical upstream name."""
    cleaned = (name or "").strip()
    return COUNTRY_ALIASES.get(cleaned.lower(), cleaned)


def is_in_group(country: str, group: str = DEFAULT_COUNTRY_GROUP) -> bool:
    return canonical_country(country) in set(group_countries(group))
