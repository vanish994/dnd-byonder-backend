import unittest
from unittest.mock import Mock, patch

from fastapi.testclient import TestClient

import rule_engine.app as api
from game.contracts import GameTurnRequest
from game.orchestrator import GameOrchestrator


SELECTION = {
    'name': 'Aria Loop',
    'class_id': 'fighter',
    'level': 1,
    'abilities': {
        'strength': 15,
        'dexterity': 14,
        'constitution': 13,
        'intelligence': 12,
        'wisdom': 10,
        'charisma': 8,
    },
    'skills': ['athletics', 'perception'],
    'weapon_id': 'longsword',
}


class SessionLoopTests(unittest.TestCase):
    def setUp(self):
        self.key = patch.object(api, 'API_KEY', 'loop-secret')
        self.key.start()
        self.addCleanup(self.key.stop)
        self.client = TestClient(api.app)
        self.headers = {'x-api-key': 'loop-secret'}

    def create(self):
        response = self.client.post('/v1/character/create', json=SELECTION, headers=self.headers)
        self.assertEqual(response.status_code, 200, response.text)
        return response.json()

    def test_creation_returns_server_owned_scene_and_actions(self):
        created = self.create()
        self.assertEqual(created['state']['scene']['id'], 'intro')
        self.assertEqual(created['state']['scene']['type'], 'exploration')
        self.assertEqual(created['state']['encounter']['id'], 'intro-ambush')
        self.assertEqual(len(created['available_actions']), 2)
        self.assertEqual(
            [action['type'] for action in created['available_actions']],
            ['ability_check', 'start_combat'],
        )
        self.assertEqual(created['available_actions'][1]['encounter_id'], 'intro-ambush')
        self.assertNotIn('hp', created['available_actions'][1])

    def test_ability_action_preserves_scene_and_updates_state(self):
        created = self.create()
        narrator = Mock()
        narrator.narrate.return_value = 'A luz revela pegadas recentes.'
        orchestrator = GameOrchestrator(narrator, resolve_action=api.resolve_game_action)
        action = created['available_actions'][0]
        with patch.object(api, 'roll_dice', return_value={'rolls': [15]}):
            response = orchestrator.turn(GameTurnRequest(
                campaign_id=created['campaign_id'],
                state=created['state'],
                player_input=action['player_input'],
                action=action,
                available_actions=created['available_actions'],
            ))
        self.assertEqual(response.rule_resolution['schema_version'], 'rule-resolution-v1')
        self.assertEqual(response.rule_resolution['status'], 'resolved')
        self.assertEqual(response.rule_resolution['check']['ability'], 'wisdom')
        self.assertEqual(response.state['scene']['last_action'], 'ability_check')
        self.assertEqual(response.available_actions, created['available_actions'])
        narrator.narrate.assert_called_once()
        self.assertEqual(
            narrator.narrate.call_args.kwargs['rule_resolution'],
            response.rule_resolution,
        )

    def test_start_combat_materializes_server_owned_encounter_and_returns_actions(self):
        created = self.create()
        narrator = Mock()
        narrator.narrate.return_value = 'O batedor surge entre as árvores.'
        orchestrator = GameOrchestrator(narrator, resolve_action=api.resolve_game_action)
        action = created['available_actions'][1]
        with patch.object(api, 'roll_dice', side_effect=[lambda upper: 19, lambda upper: 1]):
            # Resolve dice deterministically without trusting combat stats from the client.
            with patch.object(api, 'roll_dice', side_effect=[{'rolls': [20]}, {'rolls': [1]}]):
                response = orchestrator.turn(GameTurnRequest(
                    campaign_id=created['campaign_id'],
                    state=created['state'],
                    player_input=action['player_input'],
                    action=action,
                    available_actions=created['available_actions'],
                ))
        combat = response.state['combat']
        character_id = created['character']['id']
        self.assertEqual(response.rule_resolution['schema_version'], 'rule-resolution-v1')
        self.assertEqual(response.rule_resolution['status'], 'resolved')
        self.assertTrue(combat['active'])
        self.assertEqual(set(combat['combatants']), {character_id, 'goblin-scout'})
        self.assertEqual(combat['combatants']['goblin-scout']['max_hp'], 8)
        self.assertEqual([action['type'] for action in response.available_actions], ['move', 'attack', 'second_wind', 'end_turn'])
        self.assertTrue(all(action['actor_id'] == character_id for action in response.available_actions))


if __name__ == '__main__':
    unittest.main()
