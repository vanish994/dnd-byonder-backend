import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from services.gemini_narrator import (
    NARRATOR_SYSTEM_INSTRUCTION,
    GeminiNarratorClient,
    GeminiNarratorError,
)


class GeminiNarratorTests(unittest.TestCase):
    def make_client(self, response=None, side_effect=None):
        client = Mock()
        if side_effect is not None:
            client.models.generate_content.side_effect = side_effect
        else:
            client.models.generate_content.return_value = response or SimpleNamespace(text="A porta cede.")
        return client, GeminiNarratorClient(
            api_key="gemini-secret",
            model="gemini-test-model",
            timeout_seconds=2,
            client=client,
        )

    def test_valid_text_uses_official_generate_content_and_narrative_instruction(self):
        sdk_client, narrator = self.make_client()
        resolution = {
            "schema_version": "rule-resolution-v1",
            "status": "resolved",
            "action": {"type": "ability_check", "ability": "strength"},
            "rolls": [{"type": "d20", "result": 15}],
            "outcome": {"total": 18, "success": True},
        }

        result = narrator.narrate(
            campaign_id="campaign_123",
            state={"scene": "porta"},
            player_input="Eu arrombo a porta.",
            rule_resolution=resolution,
            available_actions=[{"type": "ability_check"}],
        )

        self.assertEqual(result, "A porta cede.")
        sdk_client.models.generate_content.assert_called_once()
        call = sdk_client.models.generate_content.call_args.kwargs
        self.assertEqual(call["model"], "gemini-test-model")
        self.assertIn("campaign_123", call["contents"])
        self.assertIn("\"status\":\"resolved\"", call["contents"])
        self.assertIn("Nunca role dados", call["config"].system_instruction)
        self.assertIn("rule-resolution-v1", NARRATOR_SYSTEM_INSTRUCTION)
        self.assertNotIn("gemini-secret", call["contents"])

    def test_needs_rule_validation_sends_no_mechanical_facts(self):
        sdk_client, narrator = self.make_client()
        resolution = {"schema_version": "rule-resolution-v1", "status": "needs_rule_validation"}

        narrator.narrate(
            campaign_id="campaign_123",
            state={},
            player_input="Ataco o inimigo.",
            rule_resolution=resolution,
        )

        content = sdk_client.models.generate_content.call_args.kwargs["contents"]
        self.assertIn("<FATOS_RESOLVIDOS>{}</FATOS_RESOLVIDOS>", content)
        self.assertNotIn("ability_check", content)

    def test_sdk_error_is_fail_closed_without_secret(self):
        _, narrator = self.make_client(side_effect=RuntimeError("provider failed gemini-secret"))
        with self.assertRaises(GeminiNarratorError) as raised:
            narrator.narrate(campaign_id="c", state={}, player_input="x", rule_resolution={})
        self.assertEqual(str(raised.exception), "Gemini narrator request failed")
        self.assertNotIn("gemini-secret", str(raised.exception))

    def test_empty_response_is_rejected(self):
        _, narrator = self.make_client(response=SimpleNamespace(text="  "))
        with self.assertRaises(GeminiNarratorError):
            narrator.narrate(campaign_id="c", state={}, player_input="x", rule_resolution={})

    def test_missing_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            GeminiNarratorClient(api_key="", model="gemini-test-model")
        with self.assertRaises(ValueError):
            GeminiNarratorClient(api_key="secret", model="")


if __name__ == "__main__":
    unittest.main()
