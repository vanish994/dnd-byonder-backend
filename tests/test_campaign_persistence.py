from __future__ import annotations

import os
import uuid
from urllib.parse import urlparse

import psycopg
import pytest

from game.migrations import apply_migrations
from game.persistence import (
    CampaignStore,
    IdempotencyConflict,
    RevisionConflict,
    SessionUnauthorized,
)


DATABASE_URL = os.getenv('C2_TEST_DATABASE_URL', '')
parsed = urlparse(DATABASE_URL) if DATABASE_URL else None
IS_LOCAL_TEST_DATABASE = bool(
    parsed
    and (parsed.hostname is None or parsed.hostname in {'localhost', '127.0.0.1'})
)
pytestmark = pytest.mark.skipif(
    not IS_LOCAL_TEST_DATABASE,
    reason='set C2_TEST_DATABASE_URL to a local-only PostgreSQL database to run C2 integration tests',
)


@pytest.fixture(autouse=True)
def isolated_campaign_tables():
    apply_migrations(DATABASE_URL)
    with psycopg.connect(DATABASE_URL) as connection:
        connection.execute(
            '''TRUNCATE campaign_turn_events, campaign_creation_requests,
                      campaign_snapshots, sessions, campaigns CASCADE'''
        )
    yield


def create_session(store: CampaignStore, *, token: str = 'session-token-with-enough-test-entropy-0001'):
    return store.create_campaign(
        state={'character': {'name': 'Teste'}, 'scene': {'available_actions': []}},
        creation_response={
            'schema_version': 'character-creation-phb2024-v1',
            'ruleset': 'dnd-2024-phb',
            'valid': True,
            'character': {'name': 'Teste'},
            'derived': {},
            'rule_resolution': {'schema_version': 'rule-resolution-v1', 'status': 'resolved'},
        },
        request_payload={'name': 'Teste', 'class_id': 'fighter'},
        idempotency_key='creation-key-local-test-0001',
        session_token=token,
        ruleset='dnd-2024-phb',
    )


def successful_turn(state, campaign_id):
    next_state = {**state, 'turns': state.get('turns', 0) + 1}
    return {
        'campaign_id': campaign_id,
        'narration': 'A cena avança.',
        'narration_status': 'unavailable',
        'rule_resolution': {'schema_version': 'rule-resolution-v1', 'status': 'needs_rule_validation'},
        'state': next_state,
        'available_actions': [],
    }


def test_creation_is_idempotent_and_session_token_is_required():
    store = CampaignStore(DATABASE_URL)
    first = create_session(store)
    replay = create_session(store)
    assert replay == first
    assert first['revision'] == 0
    assert first['ruleset'] == 'dnd-2024-phb'

    loaded = store.load_session(session_id=uuid.UUID(first['session_id']), session_token='session-token-with-enough-test-entropy-0001')
    assert loaded['state']['character']['name'] == 'Teste'
    with pytest.raises(SessionUnauthorized):
        store.load_session(session_id=uuid.UUID(first['session_id']), session_token='wrong-session-token-with-enough-entropy-0001')


def test_creation_idempotency_key_rejects_changed_payload_or_credential():
    store = CampaignStore(DATABASE_URL)
    create_session(store)
    with pytest.raises(IdempotencyConflict):
        store.create_campaign(
            state={'character': {'name': 'Outra'}},
            creation_response={'schema_version': 'character-creation-phb2024-v1'},
            request_payload={'name': 'Outra'},
            idempotency_key='creation-key-local-test-0001',
            session_token='session-token-with-enough-test-entropy-0001',
        )
    with pytest.raises(SessionUnauthorized):
        store.create_campaign(
            state={'character': {'name': 'Teste'}, 'scene': {'available_actions': []}},
            creation_response={'schema_version': 'character-creation-phb2024-v1'},
            request_payload={'name': 'Teste', 'class_id': 'fighter'},
            idempotency_key='creation-key-local-test-0001',
            session_token='another-session-token-with-enough-entropy-0001',
        )


def test_turn_commit_replay_and_revision_conflict_are_atomic():
    store = CampaignStore(DATABASE_URL)
    created = create_session(store)
    session_id = uuid.UUID(created['session_id'])
    token = 'session-token-with-enough-test-entropy-0001'
    called = []

    def execute(state, campaign_id):
        called.append(True)
        return successful_turn(state, campaign_id)

    request = {'expected_revision': 0, 'player_input': 'Aguardo.', 'action': None}
    first = store.commit_turn(
        session_id=session_id, session_token=token, expected_revision=0,
        idempotency_key='turn-key-local-test-000001', request_payload=request,
        execute_turn=execute,
    )
    replay = store.commit_turn(
        session_id=session_id, session_token=token, expected_revision=0,
        idempotency_key='turn-key-local-test-000001', request_payload=request,
        execute_turn=execute,
    )
    assert first == replay
    assert len(called) == 1
    assert first['revision'] == 1
    assert first['state']['turns'] == 1

    current = store.load_session(session_id=session_id, session_token=token)
    assert current['revision'] == 1
    assert current['state']['turns'] == 1
    with pytest.raises(RevisionConflict) as raised:
        store.commit_turn(
            session_id=session_id, session_token=token, expected_revision=0,
            idempotency_key='turn-key-local-test-000002', request_payload=request,
            execute_turn=execute,
        )
    assert raised.value.current_revision == 1


def test_turn_idempotency_key_cannot_represent_a_different_request():
    store = CampaignStore(DATABASE_URL)
    created = create_session(store)
    session_id = uuid.UUID(created['session_id'])
    token = 'session-token-with-enough-test-entropy-0001'
    key = 'turn-key-local-test-000003'
    store.commit_turn(
        session_id=session_id, session_token=token, expected_revision=0,
        idempotency_key=key, request_payload={'expected_revision': 0, 'player_input': 'A.', 'action': None},
        execute_turn=successful_turn,
    )
    with pytest.raises(IdempotencyConflict):
        store.commit_turn(
            session_id=session_id, session_token=token, expected_revision=0,
            idempotency_key=key, request_payload={'expected_revision': 0, 'player_input': 'B.', 'action': None},
            execute_turn=successful_turn,
        )


def test_campaign_snapshots_are_immutable_in_postgres():
    store = CampaignStore(DATABASE_URL)
    created = create_session(store)
    with pytest.raises(psycopg.DatabaseError) as raised:
        with psycopg.connect(DATABASE_URL) as connection:
            connection.execute(
                'UPDATE campaign_snapshots SET state = %s::jsonb WHERE session_id = %s AND revision = 0',
                ('{"forged": true}', uuid.UUID(created['session_id'])),
            )
    assert 'campaign snapshots are immutable' in raised.value.diag.message_primary
