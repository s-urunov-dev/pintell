"""One web-searching model, whichever one is configured.

The people and website lookups need the same capability — search the web, then
answer from what was found — and two providers offer it: Claude's server-side
web search tool, and Gemini with Google Search grounding. Neither is required;
whichever key is present is the one that runs.

Gemini is preferred by ``auto`` for a plain reason: its free tier includes the
grounding search, so the enrichment costs nothing to operate. Claude is the
better model and stays the explicit choice for anyone paying for it
(``AI_PROVIDER=anthropic``).

What makes them swappable is the answer format. Callers ask for plain labelled
lines rather than JSON or a tool schema, so nothing downstream —
``people._parse_answer`` in particular — knows or cares which model replied.
Both providers get the same system prompt and both are held to the same local
validation afterwards.
"""

from __future__ import annotations

import logging
import threading
from typing import Any

from django.conf import settings

from .client import AIUnavailable, ai_enabled, get_client

logger = logging.getLogger(__name__)

ANTHROPIC = "anthropic"
GEMINI = "gemini"

_lock = threading.Lock()
_gemini: Any = None


def gemini_enabled() -> bool:
    return bool(settings.ANTHROPIC["ENABLED"] and settings.GEMINI["API_KEY"])


def active_provider() -> str:
    """Which provider will actually serve a search, or "" if none can.

    Respects an explicit ``AI_PROVIDER`` even when that provider has no key —
    saying "use Gemini" and getting Claude instead would be a surprise worth
    avoiding, and the caller degrades cleanly either way.

    **`auto` never falls back to Claude, and that is the point.** It used to:
    with no Gemini key, the enrichment cycle — which runs every fifteen
    minutes and does web searches — would quietly start billing the moment an
    Anthropic key appeared for the *compliance* work. Paying for search is a
    decision worth typing out, so `auto` means "Gemini or nothing" and Claude
    requires `AI_PROVIDER=anthropic`.
    """
    choice = settings.AI_PROVIDER
    if choice == GEMINI:
        return GEMINI if gemini_enabled() else ""
    if choice == ANTHROPIC:
        return ANTHROPIC if ai_enabled() else ""

    return GEMINI if gemini_enabled() else ""


def search_enabled() -> bool:
    return bool(active_provider())


def _get_gemini():
    global _gemini

    if _gemini is None:
        with _lock:
            if _gemini is None:
                try:
                    from google import genai
                    from google.genai import types
                except ImportError as exc:  # pragma: no cover - packaging guard
                    raise AIUnavailable(
                        "The `google-genai` package is not installed."
                    ) from exc

                _gemini = genai.Client(
                    api_key=settings.GEMINI["API_KEY"],
                    # The SDK counts this in milliseconds; the setting is in
                    # seconds, like every other timeout in this project.
                    http_options=types.HttpOptions(
                        timeout=settings.GEMINI["TIMEOUT"] * 1000
                    ),
                )
                logger.info(
                    "Gemini client initialised (model=%s)", settings.GEMINI["MODEL"]
                )
    return _gemini


def reset_clients() -> None:
    """Drop cached clients (used by tests and after a settings change)."""
    global _gemini
    with _lock:
        _gemini = None


def _anthropic_search(system: str, prompt: str, max_searches: int) -> str:
    config = settings.ANTHROPIC
    response = get_client().messages.create(
        model=config["MODEL"],
        max_tokens=config["ENRICH_MAX_TOKENS"],
        system=system,
        output_config={"effort": config["ENRICH_EFFORT"]},
        tools=[
            {
                "type": "web_search_20260209",
                "name": "web_search",
                "max_uses": max_searches,
            }
        ],
        messages=[{"role": "user", "content": prompt}],
    )

    # A refusal is an answer — an empty one — not a failure to retry.
    if getattr(response, "stop_reason", "") == "refusal":
        logger.info("Provider declined the request.")
        return ""

    # Search results arrive as their own blocks; only the assistant's own text
    # is the answer.
    return "\n".join(
        block.text
        for block in getattr(response, "content", [])
        if getattr(block, "type", None) == "text"
    ).strip()


def _gemini_search(system: str, prompt: str) -> str:
    from google.genai import types

    client = _get_gemini()
    response = client.models.generate_content(
        model=settings.GEMINI["MODEL"],
        contents=prompt,
        config=types.GenerateContentConfig(
            system_instruction=system,
            # Grounding is the whole point of choosing Gemini here: without
            # this tool it answers about a named individual from memory, which
            # is exactly the guessing the prompts forbid.
            tools=[types.Tool(google_search=types.GoogleSearch())],
        ),
    )
    return (getattr(response, "text", "") or "").strip()


def search_answer(*, system: str, prompt: str, max_searches: int = 3) -> str:
    """Ask the configured provider a question it must search the web to answer.

    Returns the model's plain-text answer, or "" when it declined. Raises
    :class:`AIUnavailable` only when no provider is configured — every other
    failure is the caller's to catch, since enrichment is best-effort.
    """
    provider = active_provider()
    if provider == GEMINI:
        return _gemini_search(system, prompt)
    if provider == ANTHROPIC:
        return _anthropic_search(system, prompt, max_searches)

    raise AIUnavailable(
        "No web-search provider configured — set GEMINI_API_KEY (free tier) "
        "or ANTHROPIC_API_KEY."
    )
