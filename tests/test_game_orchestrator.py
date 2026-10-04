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
        self.assertEqual(response.state["hp"], 12)
        self.assertIn("narrative_context", response.state)

    def test_narrator_failure_on_free_text_keeps_needs_validation_status(self):
        self.narrator.narrate.side_effect = NarratorError("HTTP 503")
        request = GameTurnRequest(player_input="Observo o ambiente.")

        response = self.orchestrator.turn(request)

        self.assertEqual(response.rule_resolution["status"], "needs_rule_validation")
        self.assertEqual(response.narration_status, "unavailable")
        self.assertEqual(response.narration, "A cena aguarda uma resolução mecânica antes de avançar.")
        self.assertEqual(response.state["narrative_context"]["recent_dialogue"][-1]["speaker"], "mestre")

    def test_valid_structured_action_is_forwarded_unchanged(self):
        action = {"type": "ability_check", "ability": "strength", "dc": 15, "modifier": 3}
        request = GameTurnRequest(
            campaign_id="campaign_123",
            state={"hp": 12},
            player_input="Eu arrombo a porta.",
            action=action,
        )
        response = self.orchestrator.turn(request)

        self.resolve_action.assert_called_once()
        self.assertEqual(self.resolve_action.call_args.args[0], action)
        self.assertEqual(self.resolve_action.call_args.args[1]["hp"], 12)
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
        self.assertEqual(response.state["character"], state["character"])
        self.assertIn("narrative_context", response.state)

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
        self.assertEqual(response.narration, "Sua tentativa é bem-sucedida.")
        self.assertEqual(response.rule_resolution, self.resolve_action.return_value)
        self.assertEqual(response.state["hp"], 12)
        self.assertIn("narrative_context", response.state)
        self.assertEqual(response.available_actions, [{"type": "attack"}])
        self.resolve_action.assert_called_once()
        self.assertEqual(self.resolve_action.call_args.args[0], request.action)

    def test_narrator_failure_does_not_resolve_again(self):
        self.narrator.narrate.side_effect = NarratorError("timeout")
        request = GameTurnRequest(
            player_input="Eu ataco.",
            action={"type": "attack", "attack_bonus": 5, "target_ac": 15},
        )

        response = self.orchestrator.turn(request)

        self.assertEqual(response.rule_resolution["rolls"], [{"type": "d20", "result": 14}])
        self.resolve_action.assert_called_once()
        self.assertEqual(self.resolve_action.call_args.args[0], request.action)

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
                self.resolve_action.assert_called_once()
                self.assertEqual(self.resolve_action.call_args.args[0], request.action)

    def test_combat_available_actions_are_derived_from_state(self):
        resolution = self.resolve_action.return_value

        def resolve_with_combat_state(action, state):
            state["combat"] = {
                "active": True,
                "available_actions": [{"type": "end_turn"}],
            }
            return resolution

        orchestrator = GameOrchestrator(
            self.narrator,
            resolve_action=resolve_with_combat_state,
        )
        response = orchestrator.turn(
            GameTurnRequest(
                player_input="Eu termino o turno.",
                action={"type": "end_turn", "actor_id": "player"},
                available_actions=[{"type": "attack"}],
            )
        )

        self.assertEqual(response.available_actions, [{"type": "end_turn"}])
        self.assertEqual(
            self.narrator.narrate.call_args.kwargs["available_actions"],
            [{"type": "end_turn"}],
        )

    def test_narrative_context_survives_between_turns_without_becoming_mechanical_authority(self):
        first = self.orchestrator.turn(GameTurnRequest(
            campaign_id="campaign_123",
            state={"scene": {"id": "intro", "title": "A clareira"}},
            player_input="Eu observo as árvores.",
        ))
        self.resolve_action.reset_mock()
        second = self.orchestrator.turn(GameTurnRequest(
            campaign_id="campaign_123",
            state=first.state,
            player_input="Procuro o mesmo ruído.",
        ))

        context = second.state["narrative_context"]
        self.assertEqual(context["schema_version"], "narrative-context-v1")
        self.assertEqual(len(context["recent_events"]), 2)
        self.assertEqual(context["recent_dialogue"][0]["text"], "Eu observo as árvores.")
        self.assertEqual(context["recent_dialogue"][-1]["text"], "A cena continua.")
        self.assertNotIn("hp", context["recent_events"][0])
        self.assertEqual(second.rule_resolution["status"], "needs_rule_validation")


if __name__ == "__main__":
    unittest.main()
