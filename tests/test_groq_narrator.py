import json
import unittest
import httpx

from services.groq_narrator import GroqNarratorClient, GroqNarratorError


class GroqNarratorTests(unittest.TestCase):
    def make_client(self, handler):
        return GroqNarratorClient(
            api_key="groq-secret",
            model="llama-test",
            base_url="https://api.groq.com/openai/v1",
            timeout_seconds=2,
            transport=httpx.MockTransport(handler),
        )

    def test_success_uses_openai_compatible_payload(self):
        captured = {}

        def handler(request):
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"choices": [{"message": {"content": "A porta permanece diante de você."}}]})

        result = self.make_client(handler).narrate(
            campaign_id="campaign_123",
            state={"scene": "porta"},
            player_input="Eu observo a porta.",
            rule_resolution={"schema_version": "rule-resolution-v1", "status": "resolved"},
            available_actions=[{"type": "attack"}],
        )
        self.assertEqual(result, "A porta permanece diante de você.")
        self.assertEqual(captured["headers"]["authorization"], "Bearer groq-secret")
        self.assertEqual(captured["body"]["model"], "llama-test")
        self.assertFalse(captured["body"]["stream"])
        self.assertIn("rule-resolution-v1", captured["body"]["messages"][1]["content"])
        self.assertNotIn("groq-secret", json.dumps(captured["body"]))

    def test_http_429_is_fail_closed(self):
        with self.assertRaises(GroqNarratorError):
            self.make_client(lambda request: httpx.Response(429)).narrate(
                campaign_id="c", state={}, player_input="x", rule_resolution={}
            )

    def test_http_503_is_fail_closed(self):
        with self.assertRaises(GroqNarratorError):
            self.make_client(lambda request: httpx.Response(503)).narrate(
                campaign_id="c", state={}, player_input="x", rule_resolution={}
            )

    def test_timeout_is_fail_closed(self):
        def handler(request):
            raise httpx.ReadTimeout("upstream timeout", request=request)

        with self.assertRaises(GroqNarratorError):
            self.make_client(handler).narrate(campaign_id="c", state={}, player_input="x", rule_resolution={})

    def test_invalid_json_is_rejected(self):
        def handler(request):
            return httpx.Response(200, content=b"not-json", headers={"content-type": "application/json"})

        with self.assertRaises(GroqNarratorError):
            self.make_client(handler).narrate(campaign_id="c", state={}, player_input="x", rule_resolution={})

    def test_empty_content_is_rejected(self):
        with self.assertRaises(GroqNarratorError):
            self.make_client(lambda request: httpx.Response(200, json={"choices": [{"message": {"content": " "}}]})).narrate(
                campaign_id="c", state={}, player_input="x", rule_resolution={}
            )

    def test_missing_configuration_is_rejected(self):
        with self.assertRaises(ValueError):
            GroqNarratorClient(api_key="", model="llama-test")
        with self.assertRaises(ValueError):
            GroqNarratorClient(api_key="secret", model="")


if __name__ == "__main__":
    unittest.main()
