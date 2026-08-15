"""Reducing a LinkedIn profile link to the one string that identifies a person.

The same expert reaches us spelled several ways — pasted from the browser bar,
from the mobile app's share sheet, from a CV. All of these are one profile:

    linkedin.com/in/Jane-Doe
    https://www.linkedin.com/in/jane-doe/
    https://uz.linkedin.com/in/jane-doe?originalSubdomain=uz
    https://www.linkedin.com/in/jane-doe/?trk=public_profile_browsemap

A uniqueness rule over the raw text would accept every one of them and give the
directory four Jane Does, which is the failure a directory cannot afford: a
vendor searching for one expert must not have to decide which of four rows is
the real one. So the link is canonicalised before it is stored, and the
uniqueness rule sits on the canonical form.

What is dropped, and why it is safe to drop:

* **The country subdomain.** ``uz.linkedin.com`` is the same profile served in
  another interface language, not another person.
* **Query string and fragment.** They carry where the link was copied from
  (``trk``, ``originalSubdomain``), never which profile it points at.
* **Letter case in the path.** Vanity slugs are issued lowercase and resolve
  case-insensitively, so ``/in/Jane-Doe`` and ``/in/jane-doe`` are one page.

What is *not* done here: nothing is fetched. This module never asks LinkedIn
whether the profile exists — it has no network side, and a link that 404s is a
data-quality problem for a human to notice, not an import that fails.
"""

from __future__ import annotations

from urllib.parse import urlsplit

from django.core.exceptions import ValidationError

#: The canonical host. Country subdomains and the bare apex fold into it.
CANONICAL_HOST = "www.linkedin.com"

#: Path prefixes that address a person. ``/in/`` is what LinkedIn issues today;
#: ``/pub/`` is the older form, still resolving, and still on older CVs. Company
#: pages (``/company/``) and posts are rejected — this table is about people,
#: and a company link in a person's row is a mistake worth reporting early.
PERSON_PREFIXES = ("in", "pub")


def normalise_profile_url(raw: str) -> str:
    """Return the canonical form of ``raw``, or ``""`` if it is empty.

    Raises :class:`~django.core.exceptions.ValidationError` when the link is
    not a LinkedIn profile — the one case where degrading quietly would be
    wrong, because the alternative is storing a link nobody can act on under a
    field that promises one they can.
    """
    text = (raw or "").strip()
    if not text:
        return ""

    # Users paste "linkedin.com/in/…" as often as the full URL; without a
    # scheme urlsplit reads the whole thing as a path and the host check below
    # would reject a perfectly good link.
    if "://" not in text:
        text = f"https://{text}"

    parts = urlsplit(text)
    host = parts.netloc.lower()
    if "@" in host:  # user:pass@host — never legitimate here
        raise ValidationError("Not a usable LinkedIn address.")
    host = host.split(":", 1)[0]

    if host != "linkedin.com" and not host.endswith(".linkedin.com"):
        raise ValidationError("Expected a link on linkedin.com.")

    segments = [segment for segment in parts.path.split("/") if segment]
    if len(segments) < 2 or segments[0].lower() not in PERSON_PREFIXES:
        raise ValidationError(
            "Expected a personal profile link, e.g. linkedin.com/in/jane-doe."
        )

    # Only the prefix and the slug are kept. Anything LinkedIn appends after
    # them ("/details/experience", "/recent-activity") describes a view of the
    # profile, not the profile.
    prefix, slug = segments[0].lower(), segments[1].lower()
    return f"https://{CANONICAL_HOST}/{prefix}/{slug}"
