import json
import unittest

import httpx

from services.gemini_mj import GeminiMJClient, GeminiMJError


class GeminiMJTests(unittest.TestCase):
    def make_client(self, handler):
        return GeminiMJClient(
            api_key="gemini-secret",
            model="gemini-test",
            timeout_seconds=2,
            transport=httpx.MockTransport(handler),
        )

    def test_interpret_uses_generate_content_json_schema(self):
        captured = {}

        def handler(request):
            captured["url"] = str(request.url)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": json.dumps({
                "schema_version": "resolution-gate-v1",
                "requires_resolution": True,
                "resolution": {"type": "skill_check", "skill": "stealth"},
            })}]}}]})

        result = self.make_client(handler).interpret(
            campaign_id="campaign",
            state={"scene": {"type": "exploration"}},
            player_input="Passo furtivamente pelo guarda.",
        )
        self.assertIn("models/gemini-test:generateContent", captured["url"])
        self.assertEqual(captured["body"]["generationConfig"]["responseMimeType"], "application/json")
        self.assertIn("responseSchema", captured["body"]["generationConfig"])
        self.assertEqual(result["resolution"], {"type": "skill_check", "skill": "stealth"})
        self.assertNotIn("dc", result)
        self.assertNotIn("success", result)

    def test_narrate_returns_text_only(self):
        client = self.make_client(lambda request: httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": "Você avança em silêncio."}]}}]}
        ))
        result = client.narrate(
            campaign_id="campaign",
            state={"scene": {"type": "exploration"}},
            player_input="Passo furtivamente.",
            rule_resolution={"schema_version": "rule-resolution-v1", "status": "resolved"},
        )
        self.assertEqual(result, "Você avança em silêncio.")

    def test_invalid_response_fails_closed(self):
        client = self.make_client(lambda request: httpx.Response(
            200, json={"candidates": [{"content": {"parts": [{"text": '{"success":true}' }]}}]}
        ))
        with self.assertRaises(GeminiMJError):
            client.interpret(campaign_id="campaign", state={}, player_input="x")

    def test_api_error_is_redacted_and_fails_closed(self):
        client = self.make_client(lambda request: httpx.Response(429, text="key=AIza-secret"))
        with self.assertRaises(GeminiMJError):
            client.narrate(
                campaign_id="campaign", state={}, player_input="x",
                rule_resolution={"schema_version": "rule-resolution-v1", "status": "needs_rule_validation"},
            )


if __name__ == "__main__":
    unittest.main()
