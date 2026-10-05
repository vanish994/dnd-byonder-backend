import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from services.gemini_mj import GeminiMJClient, GeminiMJError


class GeminiMJTests(unittest.TestCase):
    def make_client(self, response=None, error=None):
        models = Mock()
        if error is not None:
            models.generate_content.side_effect = error
        else:
            models.generate_content.return_value = response
        return GeminiMJClient(
            api_key="gemini-secret",
            model="gemini-test",
            timeout_seconds=2,
            client=SimpleNamespace(models=models),
        ), models

    def test_interpret_uses_generate_content_json_schema(self):
        response = SimpleNamespace(text='{"schema_version":"resolution-gate-v1","requires_resolution":true,"resolution":{"type":"skill_check","skill":"stealth"}}')
        client, models = self.make_client(response=response)

        result = client.interpret(
            campaign_id="campaign",
            state={"scene": {"type": "exploration"}},
            player_input="Passo furtivamente pelo guarda.",
        )

        models.generate_content.assert_called_once()
        call = models.generate_content.call_args.kwargs
        self.assertEqual(call["model"], "gemini-test")
        self.assertEqual(call["config"].response_mime_type, "application/json")
        self.assertIn("response_json_schema", call["config"].__dict__)
        self.assertEqual(result["resolution"], {"type": "skill_check", "skill": "stealth"})
        self.assertNotIn("dc", result)
        self.assertNotIn("success", result)

    def test_narrate_returns_text_only(self):
        client, _ = self.make_client(response=SimpleNamespace(text="Você avança em silêncio."))
        result = client.narrate(
            campaign_id="campaign",
            state={"scene": {"type": "exploration"}},
            player_input="Passo furtivamente.",
            rule_resolution={"schema_version": "rule-resolution-v1", "status": "resolved"},
        )
        self.assertEqual(result, "Você avança em silêncio.")

    def test_invalid_response_fails_closed(self):
        client, _ = self.make_client(response=SimpleNamespace(text='{"success":true}'))
        with self.assertRaises(GeminiMJError):
            client.interpret(campaign_id="campaign", state={}, player_input="x")

    def test_api_error_fails_closed(self):
        client, _ = self.make_client(error=RuntimeError("key=AIza-secret"))
        with self.assertRaises(GeminiMJError):
            client.narrate(
                campaign_id="campaign", state={}, player_input="x",
                rule_resolution={"schema_version": "rule-resolution-v1", "status": "needs_rule_validation"},
            )


if __name__ == "__main__":
    unittest.main()
