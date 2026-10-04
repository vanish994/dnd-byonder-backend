import unittest
from unittest.mock import Mock

from game.contracts import GameTurnRequest
from game.orchestrator import GameOrchestrator, InvalidGameAction
from services.narrator import NarratorError


class GameOrchestratorTests(unittest.TestCase):
    def setUp(self):
        self.narrator = Mock()
        self.narrator.narrate.return_value = "A cena continua."
        self.resolve_action = Mock(
            return_value={
                "schema_version": "rule-resolution-v1",
                "resolution_id": "res-1",
                "status": "resolved",
                "action": {"type": "ability_check", "ability": "strength"},
                "check": {"ability": "strength", "dc": 15, "modifier": 3},
                "rolls": [{"type": "d20", "result": 14}],
                "outcome": {"total": 17, "success": True},
                "rules_used": ["ability_check.mvp.v1"],
            }
        )
        self.orchestrator = GameOrchestrator(
            self.narrator,
            resolve_action=self.resolve_action,
        )

    def test_free_text_does_not_call_rule_engine(self):
        request = GameTurnRequest(state={"hp": 12}, player_input="Eu tento abrir a porta.")
        response = self.orchestrator.turn(request)

        self.resolve_action.assert_not_called()
        self.assertEqual(response.rule_resolution["status"], "needs_rule_validation")
        self.narrator.narrate.assert_called_once()
        self.assertEqual(response.state, {"hp": 12})

    def test_valid_structured_action_is_forwarded_unchanged(self):
        action = {"type": "ability_check", "ability": "strength", "dc": 15, "modifier": 3}
        request = GameTurnRequest(
            campaign_id="campaign_123",
            state={"hp": 12},
            player_input="Eu arrombo a porta.",
            action=action,
        )
        response = self.orchestrator.turn(request)

        self.resolve_action.assert_called_once_with(action, {"hp": 12})
        self.assertEqual(response.rule_resolution["schema_version"], "rule-resolution-v1")
        self.assertEqual(response.rule_resolution["outcome"]["success"], True)
        self.assertEqual(response.narration_status, "available")
        sent_resolution = self.narrator.narrate.call_args.kwargs["rule_resolution"]
        self.assertEqual(sent_resolution, response.rule_resolution)
        self.assertNotIn("facts_resolvidos", response.rule_resolution)

    def test_invalid_action_does_not_reach_narrator(self):
        self.resolve_action.side_effect = ValueError("invalid")
        request = GameTurnRequest(
            player_input="Eu faço algo.",
            action={"type": "unsupported"},
        )
        with self.assertRaises(InvalidGameAction):
            self.orchestrator.turn(request)
        self.narrator.narrate.assert_not_called()

    def test_input_state_is_not_mutated(self):
        state = {"character": {"hp": 12}}
        request = GameTurnRequest(state=state, player_input="Olho ao redor.")
        response = self.orchestrator.turn(request)
        self.assertEqual(state, {"character": {"hp": 12}})
        self.assertEqual(response.state, state)

    def test_narrator_failure_preserves_mechanics_and_returns_unavailable(self):
        self.narrator.narrate.side_effect = NarratorError("temporary provider failure")
        request = GameTurnRequest(
            campaign_id="campaign_123",
            state={"hp": 12},
            player_input="Eu ataco.",
            action={"type": "attack", "attack_bonus": 5, "target_ac": 15},
            available_actions=[{"type": "attack"}],
        )

        response = self.orchestrator.turn(request)

        self.assertEqual(response.narration_status, "unavailable")
        self.assertIn("temporariamente indisponível", response.narration)
        self.assertEqual(response.rule_resolution, self.resolve_action.return_value)
        self.assertEqual(response.state, {"hp": 12})
        self.assertEqual(response.available_actions, [{"type": "attack"}])
        self.resolve_action.assert_called_once_with(request.action, {"hp": 12})

    def test_narrator_failure_does_not_resolve_again(self):
        self.narrator.narrate.side_effect = NarratorError("timeout")
        request = GameTurnRequest(
            player_input="Eu ataco.",
            action={"type": "attack", "attack_bonus": 5, "target_ac": 15},
        )

        response = self.orchestrator.turn(request)

        self.assertEqual(response.rule_resolution["rolls"], [{"type": "d20", "result": 14}])
        self.resolve_action.assert_called_once_with(request.action, {})

    def test_provider_failures_preserve_resolution(self):
        request = GameTurnRequest(
            player_input="Eu ataco.",
            action={"type": "attack", "attack_bonus": 5, "target_ac": 15},
        )

        for provider_error in ("HTTP 503", "HTTP 429", "timeout"):
            with self.subTest(provider_error=provider_error):
                self.narrator.narrate.reset_mock()
                self.narrator.narrate.side_effect = NarratorError(provider_error)
                self.resolve_action.reset_mock()
                response = self.orchestrator.turn(request)

                self.assertEqual(response.narration_status, "unavailable")
                self.assertEqual(response.rule_resolution["rolls"], [{"type": "d20", "result": 14}])
                self.resolve_action.assert_called_once_with(request.action, {})


if __name__ == "__main__":
    unittest.main()
