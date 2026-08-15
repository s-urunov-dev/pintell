"""AI-assisted enrichment.

Two jobs the deterministic pipeline cannot do well:

* reading a notice document and deciding what kind of work it is
  (:mod:`.classification`), and
* finding the winning company's website from its name
  (:mod:`.enrichment`).

Both are strictly optional: without ``ANTHROPIC_API_KEY`` the classifier falls
back to the keyword rules and website discovery is skipped, so the whole
product still works — just with less coverage.
"""

from .client import AIUnavailable, ai_enabled, get_client  # noqa: F401
