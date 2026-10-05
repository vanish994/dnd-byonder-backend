import os
import unittest
import uuid
from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import patch

from fastapi import HTTPException
from pydantic import ValidationError

import rule_engine.app as api
from game.orchestrator import GameOrchestrator
from services.narrator import UnavailableNarratorProvider

SESSION_TOKEN = 'session-token-for-tests-with-strong-entropy-0001'
IDEMPOTENCY_KEY = 'turn-key-for-tests-00000001'


class FakeCampaignStore:
    def __init__(self, state=None):
        self.state = deepcopy(state or {'scene': {'available_actions': []}})
        self.last_call = None

    def commit_turn(self, **kwargs):
        self.last_call = kwargs
        result = kwargs['execute_turn'](deepcopy(self.state), 'campaign-123')
        payload = result.model_dump(mode='json') if hasattr(result, 'model_dump') else result
        return {
            **payload,
            'campaign_id': 'campaign-123',
            'session_id': str(kwargs['session_id']),
            'revision': kwargs['expected_revision'] + 1,
        }


class GameApiTests(unittest.TestCase):
    def setUp(self):
        self.previous = {
            'api_key': api.API_KEY,
            'gemini_key': api.GEMINI_API_KEY,
            'gemini_model': api.GEMINI_MODEL,
            'gemini_base_url': api.GEMINI_BASE_URL,
        }
        api.API_KEY = 'backend-secret'
        api.GEMINI_API_KEY = ''
        api.GEMINI_MODEL = ''
        api.GEMINI_BASE_URL = ''
        self.addCleanup(self.restore)

    def restore(self):
        api.API_KEY = self.previous['api_key']
        api.GEMINI_API_KEY = self.previous['gemini_key']
        api.GEMINI_MODEL = self.previous['gemini_model']
        api.GEMINI_BASE_URL = self.previous['gemini_base_url']

    def turn_request(self, **overrides):
        payload = {
            'session_id': str(uuid.uuid4()),
            'expected_revision': 0,
            'player_input': 'Eu observo a clareira.',
            'action': None,
        }
        payload.update(overrides)
        return api.SessionTurnRequest(**payload)

    def call_turn(self, body, store=None):
        if store is not None:
            with patch.object(api, 'get_campaign_store', return_value=store):
                return api.game_turn(
                    body,
                    x_api_key='backend-secret',
                    x_request_id='request-test',
                    x_session_token=SESSION_TOKEN,
                    idempotency_key=IDEMPOTENCY_KEY,
                )
        return api.game_turn(
            body,
            x_api_key='backend-secret',
            x_request_id='request-test',
            x_session_token=SESSION_TOKEN,
            idempotency_key=IDEMPOTENCY_KEY,
        )

    def test_turn_loads_canonical_state_through_store_and_preserves_resolution_contract(self):
        store = FakeCampaignStore({'scene': {'available_actions': [{'type': 'ability_check'}]}})
        result = self.call_turn(self.turn_request(), store)

        self.assertEqual(result['campaign_id'], 'campaign-123')
        self.assertEqual(result['revision'], 1)
        self.assertEqual(result['session_id'], store.last_call['session_id'].__str__())
        self.assertNotIn('state', store.last_call['request_payload'])
        self.assertEqual(result['rule_resolution']['schema_version'], 'rule-resolution-v1')
        self.assertEqual(result['narration_status'], 'unavailable')

    def test_client_cannot_construct_turn_with_state_campaign_or_available_actions(self):
        for extra in (
            {'state': {'character': {'current_hp': 999999}}},
            {'campaign_id': 'forged-campaign'},
            {'available_actions': [{'type': 'attack'}]},
        ):
            with self.subTest(extra=extra), self.assertRaises(ValidationError):
                self.turn_request(**extra)

    def test_turn_rejects_client_attack_bonus_and_damage_before_rolling(self):
        store = FakeCampaignStore({'scene': {'available_actions': []}})
        request = self.turn_request(
            player_input='Eu ataco.',
            action={
                'type': 'attack',
                'actor_id': 'player',
                'target_id': 'goblin-1',
                'attack_bonus': 999999,
                'damage': {'dice': '1d8', 'modifier': 3},
            },
        )

        with patch.object(api, 'roll_dice') as roll_dice:
            with self.assertRaises(HTTPException) as raised:
                self.call_turn(request, store)
            self.assertEqual(raised.exception.status_code, 422)
            roll_dice.assert_not_called()

    def test_turn_rejects_mechanical_action_not_emitted_by_the_snapshot(self):
        store = FakeCampaignStore({'scene': {'available_actions': [
            {'type': 'skill_check', 'skill': 'perception', 'dc': 12, 'character_id': 'hero'},
        ]}})
        request = self.turn_request(action={
            'type': 'skill_check', 'skill': 'perception', 'dc': 1, 'character_id': 'hero',
        })

        with patch.object(api, 'roll_dice') as roll_dice:
            with self.assertRaises(HTTPException) as raised:
                self.call_turn(request, store)
        self.assertEqual(raised.exception.status_code, 422)
        roll_dice.assert_not_called()

    def test_turn_accepts_snapshot_action_and_strips_only_presentation_fields(self):
        action = {
            'type': 'skill_check', 'skill': 'perception', 'dc': 12, 'character_id': 'hero',
            'label': 'Teste de Percepção', 'player_input': 'Procuro sinais.',
        }
        store = FakeCampaignStore({'scene': {'available_actions': [action]}})
        captured = {}

        class FakeOrchestrator:
            def turn(self, request, request_id=None):
                captured['request'] = request
                return {
                    'campaign_id': request.campaign_id,
                    'narration': 'A tentativa avança.',
                    'narration_status': 'unavailable',
                    'rule_resolution': {'schema_version': 'rule-resolution-v1', 'status': 'needs_rule_validation'},
                    'state': request.state,
                    'available_actions': [action],
                    'rule_teaching': None,
                }

        with patch.object(api, 'build_game_orchestrator', return_value=FakeOrchestrator()):
            result = self.call_turn(self.turn_request(action=action), store)
        self.assertEqual(result['revision'], 1)
        self.assertEqual(captured['request'].action, action)

    def test_missing_database_configuration_fails_closed(self):
        request = self.turn_request()
        with patch.dict(os.environ, {'DATABASE_URL': ''}):
            with self.assertRaises(HTTPException) as raised:
                self.call_turn(request)
        self.assertEqual(raised.exception.status_code, 503)

    def test_invalid_backend_key_is_rejected(self):
        with self.assertRaises(HTTPException) as raised:
            api.game_turn(
                self.turn_request(),
                x_api_key='wrong',
                x_request_id=None,
                x_session_token=SESSION_TOKEN,
                idempotency_key=IDEMPOTENCY_KEY,
            )
        self.assertEqual(raised.exception.status_code, 401)

    def test_character_creation_persists_only_phb2024_campaign_and_session(self):
        body = SimpleNamespace(model_dump=lambda mode: {'name': 'Aventureira'})
        preview = {
            'schema_version': 'character-creation-phb2024-v1',
            'ruleset': 'dnd-2024-phb',
            'valid': True,
            'character': {'id': 'character-1', 'name': 'Aventureira'},
            'derived': {'hp': {'current': 10, 'max': 10}},
            'rule_resolution': {'schema_version': 'rule-resolution-v1', 'status': 'resolved'},
        }
        state = {'character': preview['character']}
        store = SimpleNamespace(create_campaign=lambda **kwargs: {
            **kwargs['creation_response'], 'campaign_id': 'campaign-1',
            'session_id': str(uuid.uuid4()), 'revision': 0,
            'state': kwargs['state'], 'available_actions': [],
        })
        with patch.object(api, '_phb2024_character_preview', return_value=(preview, state)), \
             patch.object(api, '_initial_scene', return_value={'available_actions': []}), \
             patch.object(api, '_initial_encounter', return_value={'id': 'encounter-1'}), \
             patch.object(api, 'get_campaign_store', return_value=store):
            result = api.create_phb2024_character(
                body,
                x_api_key='backend-secret',
                x_session_token=SESSION_TOKEN,
                idempotency_key='create-key-for-tests-0001',
            )

        self.assertEqual(result['ruleset'], 'dnd-2024-phb')
        self.assertEqual(result['revision'], 0)

    def test_session_resume_reads_store_and_projects_recent_dialogue(self):
        session_id = uuid.uuid4()
        character = {'id': 'character-1', 'name': 'Aventureira'}
        state = {
            'character': character,
            'scene': {'available_actions': [{'type': 'ability_check'}]},
            'narrative_context': {'recent_dialogue': [
                {'speaker': 'player', 'text': 'Observo.'},
                {'speaker': 'mestre', 'text': 'Há uma trilha.'},
            ]},
        }
        stored = {
            'campaign_id': 'campaign-1', 'session_id': str(session_id),
            'ruleset': 'dnd-2024-phb', 'revision': 3, 'state': state,
        }
        store = SimpleNamespace(load_session=lambda **kwargs: stored)
        with patch.object(api, 'get_campaign_store', return_value=store), \
             patch.object(api, 'derive_character', return_value=(None, {'hp': {'current': 10, 'max': 10}})):
            result = api.resume_session(
                session_id, x_api_key='backend-secret', x_session_token=SESSION_TOKEN,
            )
        self.assertEqual(result['revision'], 3)
        self.assertEqual([entry['speaker'] for entry in result['history']], ['voce', 'mestre'])
        self.assertEqual(result['available_actions'][0]['type'], 'ability_check')


if __name__ == '__main__':
    unittest.main()
