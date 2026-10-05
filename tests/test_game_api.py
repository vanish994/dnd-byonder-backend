import unittest
from unittest.mock import patch

from fastapi import HTTPException

import rule_engine.app as api


class GameApiTests(unittest.TestCase):
    def setUp(self):
        self.previous = {
            "api_key": api.API_KEY,
            "groq_key": api.GROQ_API_KEY,
            "groq_model": api.GROQ_MODEL,
            "groq_base_url": api.GROQ_BASE_URL,
        }
        api.API_KEY = "backend-secret"
        api.GROQ_API_KEY = "groq-secret"
        api.GROQ_MODEL = "llama-test"
        api.GROQ_BASE_URL = "https://api.groq.com/openai/v1"
        self.addCleanup(self.restore)

    def restore(self):
        api.API_KEY = self.previous["api_key"]
        api.GROQ_API_KEY = self.previous["groq_key"]
        api.GROQ_MODEL = self.previous["groq_model"]
        api.GROQ_BASE_URL = self.previous["groq_base_url"]

    @patch.object(api, "GameOrchestrator")
    def test_turn_returns_response_and_preserves_contract(self, orchestrator_cls):
        orchestrator_cls.return_value.turn.return_value = {
            "campaign_id": "campaign_123",
            "narration": "A porta se abre.",
            "narration_status": "available",
            "rule_resolution": {
                "schema_version": "rule-resolution-v1",
                "status": "needs_rule_validation",
                "reason": "not bound",
            },
            "state": {},
            "available_actions": [],
        }
        result = api.game_turn(
            api.GameTurnRequest(campaign_id="campaign_123", player_input="Eu abro a porta."),
            "backend-secret",
        )
        self.assertEqual(result["rule_resolution"]["schema_version"], "rule-resolution-v1")
        self.assertNotIn("facts_resolvidos", result)

    def test_invalid_backend_key_is_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            api.game_turn(api.GameTurnRequest(player_input="x"), "wrong")
        self.assertEqual(raised.exception.status_code, 401)

    @patch.object(api, "resolve_game_action")
    def test_missing_groq_configuration_uses_fallback_and_preserves_rule_resolution(self, resolve_action):
        api.GROQ_API_KEY = ""
        resolution = {
            "schema_version": "rule-resolution-v1",
            "status": "resolved",
            "action": {"type": "attack"},
            "outcome": {"hit": True, "damage": 5},
        }
        resolve_action.return_value = resolution

        result = api.game_turn(
            api.GameTurnRequest(
                campaign_id="campaign_123",
                player_input="Eu ataco.",
                action={"type": "attack"},
            ),
            "backend-secret",
        )

        self.assertEqual(result.narration_status, "unavailable")
        self.assertEqual(result.narration, "Seu ataque atinge o alvo e causa 5 de dano.")
        self.assertEqual(result.rule_resolution, resolution)
        resolve_action.assert_called_once()

    def test_invalid_groq_runtime_configuration_uses_local_narration_fallback(self):
        with patch.dict("os.environ", {"GROQ_TIMEOUT_SECONDS": "not-a-number"}):
            result = api.game_turn(
                api.GameTurnRequest(player_input="Aguardo.", action=None),
                "backend-secret",
            )

        self.assertEqual(result.narration_status, "unavailable")
        self.assertEqual(result.narration, "A cena aguarda uma resolução mecânica antes de avançar.")

    def test_groq_configuration_builds_groq_narrator(self):
        with patch.object(api, "GroqNarratorClient") as narrator_cls:
            api.build_game_orchestrator()
        narrator_cls.assert_called_once()
        kwargs = narrator_cls.call_args.kwargs
        self.assertEqual(kwargs["api_key"], "groq-secret")
        self.assertEqual(kwargs["model"], "llama-test")
        self.assertEqual(kwargs["base_url"], "https://api.groq.com/openai/v1")


if __name__ == "__main__":
    unittest.main()
