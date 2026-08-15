"""Whether a keyword actually occurs in a text, rather than inside a word.

Both classifiers score weighted keyword tables, and both used to ask
``phrase in haystack``. On a normalised string that is a substring test, and a
substring test cannot tell a word from the middle of a longer one. Measured
over the 11,771 mirrored consulting notices, three keywords did nearly all the
damage:

======== ================== =========================================
Keyword  False hits         Word it was hiding inside
======== ================== =========================================
formation 3,785             **in**\\ formation (3,594 of them)
gis       3,137             re\\ **gis**\\ try, re\\ **gis**\\ tration, le\\ **gis**\\ lation
erp       2,088             ent\\ **erp**\\ rise, int\\ **erp**\\ ersonal
event       203             pr\\ **event**\\ ion
audit        40             **audit**\\ orium
======== ================== =========================================

So every notice containing the word *information* scored towards Training,
because ``formation`` is the French for it; every notice mentioning a
*registry* scored towards IT advisory, because of GIS. That is not a rounding
error in the classifier, it is most of the classifier's input on those two
sub-directions.

The fix is a **leading word boundary and a whitelisted suffix**, and both
halves are load-bearing:

* The leading boundary is what kills all five rows above.
* The trailing whitelist keeps the inflections that are real matches —
  ``audits``, ``auditor``, ``architecture``, ``digitalization``. Without it,
  correcting the false hits would cost about as many true ones, which is what
  a plain ``\\b…\\b`` match was measured doing.

Multi-word phrases are left as substring tests: two or more words cannot
collide with the inside of a single one, and requiring a boundary at both ends
of a phrase would lose ``supply and delivery of`` inside a longer sentence for
no benefit.
"""

from __future__ import annotations

import re

#: Endings a keyword may grow without becoming a different word. Deliberately
#: a whitelist and not `\w*`: `audit` + `orium` is a lecture hall, and it was
#: putting furniture contracts in front of vendors reading an audit tender.
_SUFFIXES = (
    "s", "es", "ed", "ing", "ings", "ion", "ions", "or", "ors", "er", "ers",
    "ure", "ures", "al", "ally", "ization", "izations", "isation", "isations",
    "ing", "ment", "ments",
)

_SUFFIX_GROUP = "(?:%s)?" % "|".join(sorted(set(_SUFFIXES), key=len, reverse=True))

#: Compiled patterns, keyed by phrase. The tables are module constants and the
#: classifier runs over tens of thousands of notices in one pass, so compiling
#: each phrase once is the difference between a repair run of minutes and one
#: of hours.
_PATTERNS: dict[str, re.Pattern[str]] = {}


def contains(phrase: str, haystack: str) -> bool:
    """Whether ``phrase`` occurs in ``haystack`` as a word, not inside one.

    ``haystack`` is expected to be normalised already — lowercased, with
    punctuation reduced to spaces — which is what both callers' ``_normalise``
    produces.
    """
    if " " in phrase:
        # A phrase cannot hide inside a single word.
        return phrase in haystack
    return _pattern(phrase).search(haystack) is not None


def _pattern(phrase: str) -> re.Pattern[str]:
    pattern = _PATTERNS.get(phrase)
    if pattern is None:
        pattern = re.compile(rf"\b{re.escape(phrase)}{_SUFFIX_GROUP}\b")
        _PATTERNS[phrase] = pattern
    return pattern
