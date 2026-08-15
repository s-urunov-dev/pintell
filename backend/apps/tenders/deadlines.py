"""Turn a notice's deadline into an exact moment in time.

Upstream gives the two halves separately and neither is enough on its own:

* ``deadline_date`` — a date, stored at midnight UTC
* ``deadline_time`` — free text like ``"17:00"``, with **no time zone**

"17:00" is local to whoever published the notice, so reading it as UTC is
wrong by up to twelve hours. On a tender site that is not a cosmetic bug: a
countdown that says "5 hours left" after the deadline has passed sends someone
to prepare a bid they can no longer submit.

So the zone is derived from the notice's country. For most countries that is
exact. Russia and Kazakhstan span several zones, so the capital's zone is used
and ``approximate`` is set — the UI says so rather than implying a precision
the data does not have.

When the country is unknown or the time cannot be parsed, this returns
``None`` rather than guessing. Callers fall back to showing whole days.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, time
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

# Upstream country name -> IANA zone. Keys are matched case-insensitively
# against `TenderNotice.country`, which carries the World Bank's own spelling.
COUNTRY_TIMEZONES: dict[str, str] = {
    # -- focus region ------------------------------------------------------
    "afghanistan": "Asia/Kabul",
    "armenia": "Asia/Yerevan",
    "azerbaijan": "Asia/Baku",
    "belarus": "Europe/Minsk",
    "kyrgyz republic": "Asia/Bishkek",
    "kyrgyzstan": "Asia/Bishkek",
    "moldova": "Europe/Chisinau",
    "republic of moldova": "Europe/Chisinau",
    "tajikistan": "Asia/Dushanbe",
    "uzbekistan": "Asia/Tashkent",
    # -- wider archive, single-zone ----------------------------------------
    "albania": "Europe/Tirane",
    "bangladesh": "Asia/Dhaka",
    "bulgaria": "Europe/Sofia",
    "cambodia": "Asia/Phnom_Penh",
    "china": "Asia/Shanghai",
    "colombia": "America/Bogota",
    "egypt, arab republic of": "Africa/Cairo",
    "ethiopia": "Africa/Addis_Ababa",
    "georgia": "Asia/Tbilisi",
    "ghana": "Africa/Accra",
    "india": "Asia/Kolkata",
    "iraq": "Asia/Baghdad",
    "jordan": "Asia/Amman",
    "kenya": "Africa/Nairobi",
    "morocco": "Africa/Casablanca",
    "mozambique": "Africa/Maputo",
    "nepal": "Asia/Kathmandu",
    "nigeria": "Africa/Lagos",
    "pakistan": "Asia/Karachi",
    "peru": "America/Lima",
    "philippines": "Asia/Manila",
    "romania": "Europe/Bucharest",
    "rwanda": "Africa/Kigali",
    "senegal": "Africa/Dakar",
    "sri lanka": "Asia/Colombo",
    "sudan": "Africa/Khartoum",
    "tanzania": "Africa/Dar_es_Salaam",
    "turkey": "Europe/Istanbul",
    "türkiye": "Europe/Istanbul",
    "uganda": "Africa/Kampala",
    "ukraine": "Europe/Kyiv",
    "vietnam": "Asia/Ho_Chi_Minh",
    "yemen, republic of": "Asia/Aden",
    "zambia": "Africa/Lusaka",
}

# Countries spanning more than one zone: the capital's zone is a best guess,
# never a fact. Everything here is reported as `approximate`.
MULTI_ZONE_COUNTRIES: dict[str, str] = {
    "russian federation": "Europe/Moscow",
    "russia": "Europe/Moscow",
    "kazakhstan": "Asia/Almaty",
    "indonesia": "Asia/Jakarta",
    "brazil": "America/Sao_Paulo",
    "congo, democratic republic of": "Africa/Kinshasa",
}

# "17:00", "17:00:00", "5:00 PM", "17.00", "1700 hours"
_TIME_RE = re.compile(
    r"(?P<hour>\d{1,2})\s*[:.]?\s*(?P<minute>\d{2})?\s*(?P<meridiem>am|pm)?",
    re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class ResolvedDeadline:
    """A deadline pinned to a real instant, plus how sure we are of it."""

    at: datetime
    timezone: str
    #: True when the country spans several zones and we used the capital's.
    approximate: bool
    #: The local wall-clock time we parsed, e.g. "17:00". Empty when the
    #: notice published no time and we defaulted to end of day.
    local_time: str


def timezone_for_country(country: str) -> tuple[str, bool] | None:
    """``(IANA zone, approximate)`` for ``country``, or ``None`` if unknown."""
    key = (country or "").strip().lower()
    if not key:
        return None
    if key in COUNTRY_TIMEZONES:
        return COUNTRY_TIMEZONES[key], False
    if key in MULTI_ZONE_COUNTRIES:
        return MULTI_ZONE_COUNTRIES[key], True
    return None


def parse_local_time(raw: str) -> time | None:
    """Read upstream's free-text time. ``None`` when it carries no clock."""
    text = (raw or "").strip()
    if not text:
        return None

    match = _TIME_RE.search(text)
    if not match:
        return None

    try:
        hour = int(match.group("hour"))
    except (TypeError, ValueError):
        return None
    minute = int(match.group("minute") or 0)

    meridiem = (match.group("meridiem") or "").lower()
    if meridiem == "pm" and hour < 12:
        hour += 12
    elif meridiem == "am" and hour == 12:
        hour = 0

    if not (0 <= hour <= 23 and 0 <= minute <= 59):
        return None
    return time(hour=hour, minute=minute)


def resolve_deadline(
    deadline_date, deadline_time: str, country: str
) -> ResolvedDeadline | None:
    """Combine date + local time + country into one UTC-aware instant.

    Returns ``None`` when there is no date, or when the country is not one we
    can place on the map — the caller then shows whole days instead of a
    clock, which is the honest fallback.
    """
    if not deadline_date:
        return None

    zone = timezone_for_country(country)
    if zone is None:
        return None
    zone_name, approximate = zone

    try:
        tzinfo = ZoneInfo(zone_name)
    except (ZoneInfoNotFoundError, ValueError):  # pragma: no cover - bad table entry
        return None

    clock = parse_local_time(deadline_time)
    # No published time means the whole day is still open, so the deadline is
    # the end of it — not midnight, which would cut the last day off entirely.
    local_label = clock.strftime("%H:%M") if clock else ""
    clock = clock or time(hour=23, minute=59)

    local_moment = datetime.combine(deadline_date.date(), clock, tzinfo=tzinfo)
    return ResolvedDeadline(
        at=local_moment.astimezone(ZoneInfo("UTC")),
        timezone=zone_name,
        approximate=approximate,
        local_time=local_label,
    )


def resolve_deadline_instant(deadline_date, deadline_time: str, country: str):
    """The instant to *store*, which always exists when a date does.

    ``resolve_deadline`` returns ``None`` for a country not in the table,
    because a UI that cannot name the zone should show whole days rather than a
    clock it cannot justify. A stored column cannot take that way out: the
    query "is this still open" has to answer for every row, and a null would
    quietly drop the notice out of every open list.

    So the unknown-country case falls back to the end of the day in UTC. That
    is a guess, and it is deliberately the late one: for the zones this product
    serves, end-of-day UTC is after the real deadline, so the error shows a
    closed tender as briefly open rather than hiding an open one. Displaying a
    clock still requires ``resolve_deadline`` and still refuses to guess.
    """
    if not deadline_date:
        return None

    resolved = resolve_deadline(deadline_date, deadline_time, country)
    if resolved is not None:
        return resolved.at
    return datetime.combine(
        deadline_date.date(), time(hour=23, minute=59), tzinfo=ZoneInfo("UTC")
    )
