"""Anthropic client wiring shared by the AI features."""

from __future__ import annotations

import logging
import threading
from typing import Any

from django.conf import settings

logger = logging.getLogger(__name__)

_lock = threading.Lock()
_client: Any = None


class AIUnavailable(RuntimeError):
    """Raised when an AI feature is requested but cannot run.

    Callers are expected to catch this and degrade — never to propagate it to
    a user-facing request.
    """


def ai_enabled() -> bool:
    """True when AI features are switched on and a credential is present."""
    config = settings.ANTHROPIC
    return bool(config["ENABLED"] and config["API_KEY"])


def classification_enabled() -> bool:
    """Whether the *classifier* may spend money, which is a separate question.

    ``ai_enabled`` answers "is there a usable credential"; this answers "was
    this particular consumer asked for". They were the same test until the
    compliance work gave the same key a second consumer, and merging them made
    enabling extraction silently start a scheduled job over the whole mirror.
    A key is permission to spend, not an instruction to.
    """
    return ai_enabled() and bool(settings.ANTHROPIC.get("CLASSIFY_ENABLED"))


def get_client():
    """Return a lazily-built, process-wide Anthropic client."""
    global _client

    if not ai_enabled():
        raise AIUnavailable(
            "AI features are disabled — set AI_ENABLED=true and ANTHROPIC_API_KEY."
        )

    if _client is None:
        with _lock:
            if _client is None:
                try:
                    import anthropic
                except ImportError as exc:  # pragma: no cover - packaging guard
                    raise AIUnavailable(
                        "The `anthropic` package is not installed."
                    ) from exc

                _client = anthropic.Anthropic(
                    api_key=settings.ANTHROPIC["API_KEY"],
                    timeout=settings.ANTHROPIC["TIMEOUT"],
                    max_retries=settings.ANTHROPIC["MAX_RETRIES"],
                )
                logger.info(
                    "Anthropic client initialised (model=%s)",
                    settings.ANTHROPIC["MODEL"],
                )

    return _client


def reset_client() -> None:
    """Drop the cached client (used by tests and by settings changes)."""
    global _client
    with _lock:
        _client = None
