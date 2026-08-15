"""Choosing which model serves a web-search lookup.

Two providers can do this job and neither is required, so the selection rules
carry real weight: pick the wrong one and the enrichment either bills an
account the operator did not mean to use, or silently does nothing.

The rule tested here is that ``auto`` prefers Gemini — not because it is the
better model but because its free tier includes the search, which is what makes
this feature runnable at no cost. An explicit ``AI_PROVIDER`` always wins, even
when it selects a provider with no key: answering "you asked for Gemini, and
Gemini is not configured" is honest, whereas quietly falling through to Claude
is a surprise on someone's bill.
"""

from __future__ import annotations

from unittest.mock import patch

from django.test import SimpleTestCase, override_settings

from apps.tenders.services.ai import providers
from apps.tenders.services.ai.client import AIUnavailable

WITH_GEMINI = {"API_KEY": "gem-key", "MODEL": "gemini-2.5-flash", "TIMEOUT": 60}
NO_GEMINI = {"API_KEY": "", "MODEL": "gemini-2.5-flash", "TIMEOUT": 60}


def anthropic(enabled=True, key="sk-test"):
    return {
        "ENABLED": enabled, "API_KEY": key, "MODEL": "claude-opus-5",
        "TIMEOUT": 60, "MAX_RETRIES": 1,
        "ENRICH_EFFORT": "low", "ENRICH_MAX_TOKENS": 500, "ENRICH_MAX_SEARCHES": 3,
        "ENRICH_BATCH_SIZE": 10,
    }


class SelectionTests(SimpleTestCase):
    @override_settings(AI_PROVIDER="auto", GEMINI=WITH_GEMINI, ANTHROPIC=anthropic())
    def test_auto_prefers_the_free_provider_when_both_are_configured(self):
        self.assertEqual(providers.active_provider(), providers.GEMINI)

    @override_settings(AI_PROVIDER="auto", GEMINI=NO_GEMINI, ANTHROPIC=anthropic())
    def test_auto_never_reaches_for_the_paid_provider(self):
        # The Anthropic key is bought for compliance extraction. `auto` used to
        # read it as consent to run a quarter-hourly web-search job as well.
        self.assertEqual(providers.active_provider(), "")

    @override_settings(AI_PROVIDER="anthropic", GEMINI=WITH_GEMINI, ANTHROPIC=anthropic())
    def test_an_explicit_choice_is_honoured_over_the_free_one(self):
        self.assertEqual(providers.active_provider(), providers.ANTHROPIC)

    @override_settings(AI_PROVIDER="gemini", GEMINI=NO_GEMINI, ANTHROPIC=anthropic())
    def test_an_explicit_choice_without_a_key_does_not_fall_through(self):
        # Falling back to Claude here would bill an account the operator
        # explicitly steered away from.
        self.assertEqual(providers.active_provider(), "")
        self.assertFalse(providers.search_enabled())

    @override_settings(AI_PROVIDER="auto", GEMINI=NO_GEMINI, ANTHROPIC=anthropic(key=""))
    def test_no_keys_means_no_provider(self):
        self.assertEqual(providers.active_provider(), "")

    @override_settings(
        AI_PROVIDER="auto", GEMINI=WITH_GEMINI, ANTHROPIC=anthropic(enabled=False)
    )
    def test_the_master_switch_turns_gemini_off_too(self):
        # AI_ENABLED=false must mean no model runs, whichever key is present.
        self.assertEqual(providers.active_provider(), "")


class DispatchTests(SimpleTestCase):
    @override_settings(AI_PROVIDER="auto", GEMINI=NO_GEMINI, ANTHROPIC=anthropic(key=""))
    def test_asking_with_nothing_configured_raises_for_the_caller_to_catch(self):
        with self.assertRaises(AIUnavailable):
            providers.search_answer(system="s", prompt="p")

    @override_settings(AI_PROVIDER="gemini", GEMINI=WITH_GEMINI, ANTHROPIC=anthropic())
    def test_gemini_is_asked_to_ground_its_answer_in_a_search(self):
        with patch.object(providers, "_get_gemini") as client:
            client.return_value.models.generate_content.return_value = type(
                "R", (), {"text": "TITLE: Senior Economist"}
            )()
            answer = providers.search_answer(system="sys", prompt="who?")

        self.assertEqual(answer, "TITLE: Senior Economist")
        config = client.return_value.models.generate_content.call_args.kwargs["config"]
        self.assertEqual(config.system_instruction, "sys")
        # Without the grounding tool the model answers about a named person
        # from memory, which is the guessing the prompt forbids.
        self.assertTrue(config.tools[0].google_search is not None)

    @override_settings(AI_PROVIDER="anthropic", GEMINI=NO_GEMINI, ANTHROPIC=anthropic())
    def test_claude_answers_from_its_text_blocks_only(self):
        blocks = [
            type("B", (), {"type": "web_search_tool_result", "text": "raw results"})(),
            type("B", (), {"type": "text", "text": "TITLE: Senior Economist"})(),
        ]
        response = type("R", (), {"stop_reason": "end_turn", "content": blocks})()

        with patch.object(providers, "get_client") as client:
            client.return_value.messages.create.return_value = response
            answer = providers.search_answer(system="sys", prompt="who?")

        self.assertEqual(answer, "TITLE: Senior Economist")

    @override_settings(AI_PROVIDER="anthropic", GEMINI=NO_GEMINI, ANTHROPIC=anthropic())
    def test_a_refusal_is_an_empty_answer_not_an_error(self):
        response = type("R", (), {"stop_reason": "refusal", "content": []})()
        with patch.object(providers, "get_client") as client:
            client.return_value.messages.create.return_value = response
            self.assertEqual(providers.search_answer(system="s", prompt="p"), "")
