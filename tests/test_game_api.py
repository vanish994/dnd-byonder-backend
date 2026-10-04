import unittest
from unittest.mock import patch

from fastapi import HTTPException

import rule_engine.app as api
from game.orchestrator import NarrationError


class GameApiTests(unittest.TestCase):
    def setUp(self):
        self.previous = {
            "api_key": api.API_KEY,
            "base": api.MIMO_BASE_URL,
            "model": api.MIMO_MODEL,
            "mimo_key": api.MIMO_API_KEY,
            "provider": api.NARRATOR_PROVIDER,
            "gemini_key": api.GEMINI_API_KEY,
        }
        api.API_KEY = "backend-secret"
        api.MIMO_BASE_URL = "https://mimo.example"
        api.MIMO_MODEL = "mimo-v2.6-flash"
        api.MIMO_API_KEY = "mimo-secret"
        api.NARRATOR_PROVIDER = "mimo"
        api.GEMINI_API_KEY = "gemini-secret"
        self.addCleanup(self.restore)

    def restore(self):
        api.API_KEY = self.previous["api_key"]
        api.MIMO_BASE_URL = self.previous["base"]
        api.MIMO_MODEL = self.previous["model"]
        api.MIMO_API_KEY = self.previous["mimo_key"]
        api.NARRATOR_PROVIDER = self.previous["provider"]
        api.GEMINI_API_KEY = self.previous["gemini_key"]

    @patch.object(api, "GameOrchestrator")
    def test_turn_returns_response_and_preserves_contract(self, orchestrator_cls):
        orchestrator_cls.return_value.turn.return_value = {
            "campaign_id": "campaign_123",
            "narration": "A porta se abre.",
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

    def test_missing_mimo_configuration_is_503(self):
        api.MIMO_API_KEY = ""
        with self.assertRaises(HTTPException) as raised:
            api.game_turn(api.GameTurnRequest(player_input="x"), "backend-secret")
        self.assertEqual(raised.exception.status_code, 503)

    def test_missing_gemini_configuration_is_503(self):
        api.NARRATOR_PROVIDER = "gemini"
        api.GEMINI_API_KEY = ""
        with self.assertRaises(HTTPException) as raised:
            api.game_turn(api.GameTurnRequest(player_input="x"), "backend-secret")
        self.assertEqual(raised.exception.status_code, 503)

    @patch.object(api, "build_game_orchestrator")
    def test_mimo_failure_preserves_rule_resolution(self, build_orchestrator):
        resolution = {
            "schema_version": "rule-resolution-v1",
            "status": "resolved",
            "resolution_id": "res-1",
        }
        build_orchestrator.return_value.turn.side_effect = NarrationError(
            "MiMo narrator unavailable", resolution
        )
        with self.assertRaises(HTTPException) as raised:
            api.game_turn(api.GameTurnRequest(player_input="x"), "backend-secret")
        self.assertEqual(raised.exception.status_code, 502)
        self.assertEqual(raised.exception.detail["rule_resolution"], resolution)


if __name__ == "__main__":
    unittest.main()
