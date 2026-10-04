import json
import unittest

import httpx

from services.mimo_narrator import MimoNarratorClient, MimoNarratorError


class MimoNarratorTests(unittest.TestCase):
    def make_client(self, handler):
        return MimoNarratorClient(
            "https://dnd-mimo-narrator.onrender.com",
            "mimo-v2.6-flash",
            "secret-token",
            transport=httpx.MockTransport(handler),
        )

    def test_payload_and_headers_for_resolved_turn(self):
        captured = {}

        def handler(request):
            captured["headers"] = dict(request.headers)
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"choices": [{"message": {"content": "A porta cede."}}]})

        resolution = {
            "schema_version": "rule-resolution-v1",
            "status": "resolved",
            "resolution_id": "res-1",
            "action": {"type": "ability_check", "ability": "strength"},
            "check": {"ability": "strength", "dc": 15, "modifier": 3},
            "rolls": [{"type": "d20", "result": 14}],
            "outcome": {"total": 17, "success": True},
            "rules_used": ["ability_check.mvp.v1"],
        }
        result = self.make_client(handler).narrate(
            campaign_id="campaign_123",
            state={"scene": "porta"},
            player_input="Eu arrombo a porta.",
            rule_resolution=resolution,
        )

        self.assertEqual(result, "A porta cede.")
        self.assertEqual(captured["headers"]["authorization"], "Bearer secret-token")
        self.assertEqual(captured["body"]["stream"], False)
        content = captured["body"]["messages"][0]["content"]
        self.assertIn("\"resolution_id\":\"res-1\"", content)
        self.assertIn("<FATOS_RESOLVIDOS>", content)

    def test_unresolved_turn_sends_empty_facts(self):
        captured = {}

        def handler(request):
            captured["body"] = json.loads(request.content)
            return httpx.Response(200, json={"choices": [{"message": {"content": "A porta permanece diante de você."}}]})

        resolution = {
            "schema_version": "rule-resolution-v1",
            "status": "needs_rule_validation",
            "reason": "not bound",
        }
        self.make_client(handler).narrate(
            campaign_id="campaign_123",
            state={},
            player_input="Eu tento abrir a porta.",
            rule_resolution=resolution,
        )
        content = captured["body"]["messages"][0]["content"]
        self.assertIn("<FATOS_RESOLVIDOS>{}</FATOS_RESOLVIDOS>", content)

    def test_invalid_response_and_http_error_are_fail_closed(self):
        invalid = self.make_client(lambda request: httpx.Response(200, json={}))
        with self.assertRaises(MimoNarratorError) as raised:
            invalid.narrate(campaign_id="c", state={}, player_input="x", rule_resolution={})
        self.assertNotIn("secret-token", str(raised.exception))

        failed = self.make_client(lambda request: httpx.Response(503))
        with self.assertRaises(MimoNarratorError):
            failed.narrate(campaign_id="c", state={}, player_input="x", rule_resolution={})


if __name__ == "__main__":
    unittest.main()
