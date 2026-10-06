import unittest
from types import SimpleNamespace
from unittest.mock import Mock

from game.campaign import CampaignSetup
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

    def test_interpret_can_return_only_server_owned_adventure_intent(self):
        response = SimpleNamespace(text='{"schema_version":"resolution-gate-v1","requires_resolution":false,"adventure_action":{"type":"adventure_action","intent":"collect_bark_sample"}}')
        client, models = self.make_client(response=response)

        result = client.interpret(
            campaign_id="campaign",
            state={"scene": {"id": "redwood-grove-r3"}},
            player_input="Coleto uma amostra da casca.",
        )

        self.assertFalse(result["requires_resolution"])
        self.assertEqual(result["adventure_action"], {
            "type": "adventure_action",
            "intent": "collect_bark_sample",
        })
        schema = models.generate_content.call_args.kwargs["config"].response_json_schema
        self.assertFalse(schema["additionalProperties"])
        self.assertFalse(schema["properties"]["adventure_action"]["additionalProperties"])

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

    def test_campaign_seed_is_structured_and_contains_no_mechanics(self):
        response = SimpleNamespace(text='''{"schema_version":"campaign-seed-v1","title":"As Cinzas","premise":"Uma cidade pede ajuda.","opening_location":"Porto Velho","opening_description":"A chuva cai sobre os telhados.","initial_tension":"Uma carta desaparecida preocupa a guarda.","known_facts":["A carta existia."],"rumors":["Alguém viu uma luz."],"npcs":[{"name":"Iara","role":"guia","motivation":"proteger a cidade"}],"initial_objectives":["Encontrar a carta."],"opening_question":"O que você faz?"}''')
        client, models = self.make_client(response=response)
        seed = client.generate_campaign_seed(
            setup=CampaignSetup(campaign_name='Teste', setting_prompt='Uma cidade portuária.'),
            character={'name': 'Aria', 'class': {'id': 'wizard'}},
        )
        self.assertEqual(seed.schema_version, 'campaign-seed-v1')
        self.assertEqual(seed.opening_location, 'Porto Velho')
        self.assertEqual(models.generate_content.call_args.kwargs['config'].response_mime_type, 'application/json')

    def test_campaign_seed_rejects_mechanical_text(self):
        response = SimpleNamespace(text='''{"schema_version":"campaign-seed-v1","title":"Teste","premise":"A CD é 12.","opening_location":"Lugar","opening_description":"Uma porta.","initial_tension":"Perigo.","known_facts":[],"rumors":[],"npcs":[],"initial_objectives":[],"opening_question":"O que faz?"}''')
        client, _ = self.make_client(response=response)
        with self.assertRaises(GeminiMJError):
            client.generate_campaign_seed(
                setup=CampaignSetup(campaign_name='Teste', setting_prompt='Uma porta.'),
                character={'name': 'Aria'},
            )


if __name__ == "__main__":
    unittest.main()
