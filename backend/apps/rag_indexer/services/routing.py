"""Which model answers, and what it is actually shown.

Two cost levers that have nothing to do with retrieval, kept in one module
because they are the same decision seen from either end: what the request
costs is the model it goes to multiplied by the tokens it carries.

**The router is rules, not a model.** A classifier in front of the classifier
would add a metered call, a failure mode and a second thing to explain to every
question — to decide something a dozen words can decide. So this reads the
question with the same kind of word tables `apps/tenders/keywords.py` uses, and
carries the lesson that module paid for (D39): a keyword matches as a **word**,
never as a substring. `bahola` inside `baholanmagan` is not a request to
evaluate anything, and `risk` inside `brisk` is not a risk question.

**The default is the capable tier.** Every unrecognised question goes deep.
That is the opposite of how a cost optimisation is usually written, and it is
the only defensible direction here: a lookup answered by the deep model costs a
few cents, and an analysis answered by the cheap one costs a wrong-shaped
answer that the citation schema cannot catch — the model still cites real
passages, it just reads them worse.

**The fast tier is empty until a human sets it.** `AI_CHAT_MODEL_FAST` has no
default (settings), so out of the box this module routes every question to the
same model the chat already used and changes nothing. That is because the cost
of getting it wrong is already measured: the deployed server ran the whole chat
on Haiku and the Uzbek came back ungrammatical — "zarorat",
"savdo-sotuvchi shartlari" — a quality the citation schema cannot check and no
prompt can fix. So the tier is opt-in, `claude-sonnet-5` is the recommended
value rather than the cheapest one that answers, and the gold set is where the
question is actually settled.

**Compression is whitespace and markup, never words.** What reaches the model
is the passage with its runs of spaces collapsed, its leftover tags removed and
its repeated title dropped — not a summary, not a rewrite, and never a figure
touched. The reader's copy is untouched: the front end renders the *stored*
payload, and the highlight is located by character offsets into the canonical
text (D32), so anything this module did to the prompt would put a yellow box in
the wrong place if it reached them. It does not: compression happens at the
call site and travels no further.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass
from typing import Any, Sequence

from django.conf import settings

logger = logging.getLogger(__name__)

#: Words that make a question a *reading* rather than a *lookup*, in the three
#: languages the chat answers in (D47). Comparison, judgement, causation and
#: risk: each asks the model to hold several passages against each other, which
#: is the work the capable tier is for.
#:
#: **Inflected forms are listed, not stemmed.** Uzbek agglutinates and Russian
#: conjugates, and a stemmer for either is a dependency and a second thing to
#: be wrong. So the imperative and the infinitive are both written out, and a
#: form nobody listed is simply not matched — which routes to the deep tier,
#: the default anyway. Missing a signal costs a few cents; matching a substring
#: of an unrelated word costs an answer (D39).
DEEP_WORDS = {
    # English
    "compare", "comparison", "evaluate", "evaluation", "assess", "assessment",
    "analyse", "analyze", "analysis", "eligibility", "eligible", "qualify",
    "qualifies", "risk", "risks", "why", "difference", "differences", "trend",
    "trends", "recommend", "recommendation", "implication", "implications",
    "range", "typical", "typically", "average", "summarise", "summarize",
    # Uzbek (latin)
    "taqqosla", "taqqoslang", "solishtir", "solishtiring", "baho", "baholang",
    "tahlil", "xavf", "xavflar", "nega", "nima uchun", "farq", "farqi",
    "tavsiya", "odatda", "oʻrtacha", "ortacha", "muvofiq", "mos",
    # Russian
    "сравнить", "сравните", "сравнение", "оценить", "оцените", "оценка",
    "анализ", "проанализировать", "проанализируйте", "риск", "риски",
    "почему", "разница", "различия", "рекомендация", "порекомендуйте",
    "обычно", "средний", "соответствует", "соответствие", "диапазон",
}

#: Words that make a question a lookup: one field, one row, one answer. These
#: only *reach* the fast tier when nothing in `DEEP_WORDS` also fired — a
#: question can ask for a deadline and a comparison in one breath, and the
#: harder half decides.
FAST_WORDS = {
    # English
    "contact", "email", "e-mail", "phone", "telephone", "address", "deadline",
    "closing", "date", "when", "who", "where", "country", "reference", "id",
    "title", "name", "language", "currency",
    # Uzbek (latin)
    "aloqa", "pochta", "email", "telefon", "manzil", "muddat", "muddati",
    "sana", "qachon", "kim", "kimning", "qayerda", "davlat", "raqam", "nomi",
    # Russian
    "контакт", "почта", "электронная", "телефон", "адрес", "срок", "дедлайн",
    "дата", "когда", "кто", "где", "страна", "номер", "название",
}

#: Words above which a question is treated as complex whatever else it says.
#: A reader who writes three lines is not asking for a phone number, and the
#: cost of reading them with the capable model is one question's worth.
LONG_QUESTION_WORDS = 25

#: Tokens of a question, folded. Word characters only, so `TRIP-CS-01` becomes
#: `trip`, `cs`, `01` — which is right here: the router is deciding how hard
#: the *question* is, and the reference code is handled by retrieval (D58).
_WORD_RE = re.compile(r"[^\W_]+", re.UNICODE)

#: Markup a borrower's notice body carries into the prompt. Removed rather than
#: escaped: a `<p>` costs tokens and tells the model nothing, and the reader's
#: copy — where paragraph boundaries still matter — is a different string.
_TAG_RE = re.compile(r"<[^>]{1,200}>")
#: Everything whitespace except a newline — paragraph structure is the one
#: piece of layout worth its tokens, and `\s+` would flatten a document into a
#: wall. Unicode-aware, so the non-breaking spaces a borrower's HTML is full of
#: collapse together with the ordinary ones.
_NEW_WHITESPACE_RE = re.compile(r"[^\S\n]+")
#: Zero-width characters: not whitespace, not visible, and scattered through
#: any table a PDF extractor has been near. Removed rather than collapsed.
_ZERO_WIDTH_RE = re.compile("[​-‏﻿]")
_BLANK_LINES_RE = re.compile(r"\n{3,}")
#: Control characters that survive a bad PDF extraction. They are invisible to
#: a reader and they are tokens to a model.
_CONTROL_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")


@dataclass(frozen=True)
class Route:
    """Which model this question goes to, and why."""

    tier: str
    model: str
    effort: str
    #: The rule that decided it. Reported on the answer and logged, because a
    #: routing decision nobody can reconstruct is a routing decision nobody
    #: can measure — and the whole claim of the tiering is that it is cheaper
    #: *without* being worse.
    reason: str

    def as_dict(self) -> dict[str, Any]:
        return {"tier": self.tier, "model": self.model, "reason": self.reason}


def route(question: str, *, notice_id: str = "", history: int = 0) -> Route:
    """Pick a tier for one question.

    ``notice_id`` and ``history`` are context, not overrides. A question scoped
    to one tender is *usually* a lookup and a question in a long thread is
    usually a follow-up, but neither is enough on its own — the words decide,
    and these only break the tie.
    """
    config = settings.ANTHROPIC
    deep_model = config["CHAT_MODEL_DEEP"] or config["CHAT_MODEL"] or config["MODEL"]
    fast_model = config["CHAT_MODEL_FAST"]

    words = {word.casefold() for word in _WORD_RE.findall(question or "")}
    reason = _classify(question, words, notice_id=notice_id, history=history)
    wants_fast = reason.startswith("lookup")

    # No fast tier configured means one tier, and the answer is unchanged from
    # before this module existed. Reported honestly rather than as a "deep"
    # decision the router did not make.
    if wants_fast and not fast_model:
        return Route(
            tier="deep",
            model=deep_model,
            effort=config["CHAT_EFFORT_DEEP"] or config["CHAT_EFFORT"],
            reason=f"{reason}:no_fast_model",
        )
    if wants_fast:
        return Route(
            tier="fast",
            model=fast_model,
            effort=config["CHAT_EFFORT_FAST"] or config["CHAT_EFFORT"],
            reason=reason,
        )
    return Route(
        tier="deep",
        model=deep_model,
        effort=config["CHAT_EFFORT_DEEP"] or config["CHAT_EFFORT"],
        reason=reason,
    )


def _classify(
    question: str, words: set[str], *, notice_id: str, history: int
) -> str:
    """The rule that fires, in the order the rules are allowed to fire.

    Order is the whole of the logic and it runs hardest-first: any signal of
    analysis wins, then length, then a lookup word, then the scope tie-break.
    Anything left is unrecognised and goes deep, which is the default the
    module docstring argues for.
    """
    if words & DEEP_WORDS:
        return "analysis:word"
    if len(_WORD_RE.findall(question or "")) > LONG_QUESTION_WORDS:
        return "analysis:length"
    if words & FAST_WORDS:
        # A general lookup still reads across notices — "what are the
        # deadlines" is a list, and a list of eight is not a field lookup. The
        # cheap tier gets it only when the reader is standing on one tender.
        return "lookup:word" if notice_id else "analysis:unscoped"
    if notice_id and history == 0:
        return "lookup:scoped_opener"
    return "analysis:default"


# -- context compression ----------------------------------------------------
def compress(text: str, *, max_chars: int | None = None, title: str = "") -> str:
    """One passage, as the prompt should carry it.

    Four things happen and no fifth: markup is dropped, whitespace runs
    collapse, control characters go, and a title the passage already opens with
    is not printed twice. Words, figures, currency codes and punctuation are
    untouched — the model is asked to quote figures exactly as the source
    writes them, and a compressor that normalised `USD 22.4 million` would make
    that instruction unfollowable.

    Truncation is on a word boundary and only when the passage is over budget.
    A cut passage keeps an ellipsis so the model can see it was cut rather than
    reading a sentence that stops mid-clause as the source's own wording.
    """
    limit = max_chars or settings.RAG["PASSAGE_MAX_CHARS"]
    cleaned = _TAG_RE.sub(" ", text or "")
    cleaned = _CONTROL_RE.sub(" ", cleaned)
    cleaned = _ZERO_WIDTH_RE.sub("", cleaned)
    cleaned = _NEW_WHITESPACE_RE.sub(" ", cleaned)
    cleaned = _BLANK_LINES_RE.sub("\n\n", cleaned)
    cleaned = "\n".join(line.strip() for line in cleaned.splitlines()).strip()

    if title:
        cleaned = _drop_leading_title(cleaned, title)

    if len(cleaned) <= limit:
        return cleaned
    head = cleaned[:limit].rsplit(" ", 1)[0].rstrip(" ,;:-")
    return f"{head} …"


def _drop_leading_title(text: str, title: str) -> str:
    """Remove the title from the head of a passage that repeats it.

    The prompt already names the tender beside every passage (`_passages`), and
    the first chunk of a notice body is very often that same line again. Two
    copies cost tokens and give the model a second, subtly different spelling
    of one fact to reconcile.

    Compared case-folded and whitespace-normalised, because the two copies come
    from different columns and differ by exactly that.
    """
    head = " ".join(title.split()).casefold()
    if not head or len(head) < 12:
        return text
    body = text.lstrip()
    if body[: len(head)].casefold() == head:
        return body[len(head) :].lstrip(" .:-—–\n")
    return text


def compressed_passages(hits: Sequence[Any]) -> list[str]:
    """Every hit's content, compressed, in order. A convenience for the caller."""
    return [
        compress(
            str(hit.payload.get("content", "")),
            title=str(hit.payload.get("title") or ""),
        )
        for hit in hits
    ]
