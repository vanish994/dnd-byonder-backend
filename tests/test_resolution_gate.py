import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from game.orchestrator import GameOrchestrator
from game.resolution_gate import ResolutionGateDecision
from game.contracts import GameTurnRequest
from services.gemini_mj import GeminiMJClient, GeminiMJError


class ResolutionGateTests(unittest.TestCase):
    def test_contract_rejects_mechanical_values_and_state_fields(self):
        with self.assertRaises(ValueError):
            ResolutionGateDecision.model_validate({
                "schema_version": "resolution-gate-v1",
                "requires_resolution": True,
                "resolution": {"type": "skill_check", "skill": "stealth", "dc": 10},
            })
        with self.assertRaises(ValueError):
            ResolutionGateDecision.model_validate({
                "schema_version": "resolution-gate-v1",
                "requires_resolution": True,
                "resolution": {"type": "skill_check", "skill": "stealth"},
                "success": True,
            })

    def test_gemini_interpreter_requires_structured_json(self):
        models = Mock()
        models.generate_content.return_value = SimpleNamespace(text='{"schema_version":"resolution-gate-v1","requires_resolution":true,"resolution":{"type":"skill_check","skill":"stealth"}}')

        interpreter = GeminiMJClient(
            api_key="gemini-secret",
            model="gemini-test",
            client=SimpleNamespace(models=models),
        )
        result = interpreter.interpret(
            campaign_id="campaign",
            state={"scene": {"type": "exploration"}},
            player_input="Passo furtivamente pelo guarda.",
        )
        self.assertEqual(result["schema_version"], "resolution-gate-v1")
        self.assertTrue(result["requires_resolution"])
        self.assertEqual(result["resolution"], {"type": "skill_check", "skill": "stealth"})
        config = models.generate_content.call_args.kwargs["config"]
        self.assertEqual(config.response_mime_type, "application/json")
        self.assertIn("response_json_schema", config.__dict__)
        self.assertNotIn("dc", result["resolution"])
        self.assertNotIn("success", result)
        self.assertNotIn("hp", result)

    def test_invalid_gemini_json_fails_closed(self):
        interpreter = GeminiMJClient(
            api_key="gemini-secret",
            model="gemini-test",
            client=SimpleNamespace(models=SimpleNamespace(generate_content=Mock(return_value=SimpleNamespace(text='{"success":true}')))),
        )
        with self.assertRaises(GeminiMJError):
            interpreter.interpret(campaign_id="c", state={}, player_input="x")

    def test_trivial_intent_does_not_call_rule_engine(self):
        narrator = Mock()
        narrator.narrate.return_value = "Você atravessa o corredor vazio sem pressa."
        resolve = Mock()
        orchestrator = GameOrchestrator(
            narrator,
            resolve_action=resolve,
            interpret_intent=lambda **_kwargs: {
                "schema_version": "resolution-gate-v1",
                "requires_resolution": False,
            },
        )
        response = orchestrator.turn(GameTurnRequest(
            campaign_id="c",
            state={"scene": {"type": "exploration", "available_actions": []}},
            player_input="Ando pelo corredor vazio.",
        ))
        resolve.assert_not_called()
        self.assertFalse(response.rule_resolution["resolution_gate"]["requires_resolution"])
        self.assertEqual(response.rule_resolution["status"], "needs_rule_validation")

    def test_resolution_intent_is_bound_by_backend_before_rule_engine(self):
        narrator = Mock()
        narrator.narrate.return_value = "Você passa sem ser percebido."
        resolve = Mock(return_value={
            "schema_version": "rule-resolution-v1",
            "status": "resolved",
            "action": {"type": "skill_check", "skill": "stealth"},
            "outcome": {"success": True},
        })
        orchestrator = GameOrchestrator(
            narrator,
            resolve_action=resolve,
            interpret_intent=lambda **_kwargs: {
                "schema_version": "resolution-gate-v1",
                "requires_resolution": True,
                "resolution": {"type": "skill_check", "skill": "stealth"},
            },
        )
        authorized = {
            "type": "skill_check",
            "skill": "stealth",
            "dc": 12,
            "character_id": "character-1",
        }
        response = orchestrator.turn(GameTurnRequest(
            campaign_id="c",
            state={"character": {"id": "character-1"}, "scene": {"type": "exploration", "available_actions": [authorized]}},
            player_input="Passo pelo guarda.",
            available_actions=[authorized],
        ))
        resolve.assert_called_once_with(authorized, response.state)
        self.assertTrue(response.rule_resolution["resolution_gate"]["requires_resolution"])

    def test_resolution_intent_not_in_snapshot_acknowledges_without_rolling(self):
        narrator = Mock()
        resolve = Mock()
        orchestrator = GameOrchestrator(
            narrator,
            resolve_action=resolve,
            interpret_intent=lambda **_kwargs: {
                "schema_version": "resolution-gate-v1",
                "requires_resolution": True,
                "resolution": {"type": "skill_check", "skill": "stealth"},
            },
        )
        response = orchestrator.turn(GameTurnRequest(
            state={"scene": {"type": "exploration", "available_actions": [
                {"type": "skill_check", "skill": "perception", "dc": 12},
            ]}},
            player_input="Passo sem fazer barulho.",
        ))
        self.assertEqual(response.rule_resolution["status"], "needs_rule_validation")
        self.assertIn("not authorized", response.rule_resolution["reason"])
        resolve.assert_not_called()
        narrator.narrate.assert_not_called()


if __name__ == "__main__":
    unittest.main()
