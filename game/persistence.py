from __future__ import annotations

import copy
import hashlib
import hmac
import json
import os
import re
import uuid
from collections.abc import Callable
from typing import Any

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb


_IDEMPOTENCY_KEY = re.compile(r'^[A-Za-z0-9._:-]{16,128}$')
_RULESET = 'dnd-2024-phb'


class PersistenceNotConfigured(RuntimeError):
    pass


class PersistenceUnavailable(RuntimeError):
    pass


class CampaignNotFound(RuntimeError):
    pass


class SessionUnauthorized(RuntimeError):
    pass


class RevisionConflict(RuntimeError):
    def __init__(self, current_revision: int) -> None:
        super().__init__('session revision is stale')
        self.current_revision = current_revision


class IdempotencyConflict(RuntimeError):
    pass


class SnapshotIntegrityError(RuntimeError):
    pass


class CampaignStore:
    """PostgreSQL persistence for server-authoritative, PHB 2024 campaign state."""

    def __init__(self, database_url: str | None = None) -> None:
        configured = database_url if database_url is not None else os.getenv('DATABASE_URL', '')
        self.database_url = configured.strip()

    def _connect(self):
        if not self.database_url:
            raise PersistenceNotConfigured('campaign persistence is not configured')
        try:
            return psycopg.connect(self.database_url, row_factory=dict_row)
        except psycopg.Error as exc:
            raise PersistenceUnavailable('campaign persistence is unavailable') from exc

    @staticmethod
    def _digest(value: Any) -> str:
        encoded = json.dumps(
            value,
            ensure_ascii=False,
            sort_keys=True,
            separators=(',', ':'),
            allow_nan=False,
        ).encode('utf-8')
        return hashlib.sha256(encoded).hexdigest()

    @staticmethod
    def _token_digest(session_token: str) -> bytes:
        if not isinstance(session_token, str) or not 32 <= len(session_token) <= 256:
            raise SessionUnauthorized('invalid session credential')
        return hashlib.sha256(session_token.encode('utf-8')).digest()

    @staticmethod
    def _validate_idempotency_key(idempotency_key: str) -> None:
        if not isinstance(idempotency_key, str) or not _IDEMPOTENCY_KEY.fullmatch(idempotency_key):
            raise IdempotencyConflict('invalid idempotency key')

    @staticmethod
    def _advisory_lock_id(idempotency_key: str) -> int:
        return int.from_bytes(hashlib.sha256(idempotency_key.encode('utf-8')).digest()[:8], 'big', signed=True)

    @staticmethod
    def _verify_snapshot(state: Any, expected_digest: str) -> dict[str, Any]:
        if not isinstance(state, dict) or CampaignStore._digest(state) != expected_digest:
            raise SnapshotIntegrityError('campaign snapshot integrity check failed')
        return copy.deepcopy(state)

    def create_campaign(
        self,
        *,
        state: dict[str, Any],
        creation_response: dict[str, Any],
        request_payload: dict[str, Any],
        idempotency_key: str,
        session_token: str,
        ruleset: str = _RULESET,
    ) -> dict[str, Any]:
        if ruleset != _RULESET:
            raise ValueError('unsupported campaign ruleset')
        self._validate_idempotency_key(idempotency_key)
        token_digest = self._token_digest(session_token)
        request_digest = self._digest({'ruleset': ruleset, 'request': request_payload})
        campaign_id = uuid.uuid4()
        session_id = uuid.uuid4()
        state_digest = self._digest(state)
        campaign_type = 'dynamic' if state.get('schema_version') == 'campaign-state-v1' or state.get('scene', {}).get('id') == 'generated-opening' else 'dragon-delves'
        campaign_title = state.get('campaign', {}).get('title') if isinstance(state.get('campaign'), dict) else None
        response = {
            **copy.deepcopy(creation_response),
            'campaign_id': str(campaign_id),
            'session_id': str(session_id),
            'revision': 0,
            'state': copy.deepcopy(state),
            'available_actions': copy.deepcopy(
                state.get('scene', {}).get('available_actions', [])
                if isinstance(state.get('scene'), dict) else []
            ),
        }

        try:
            with self._connect() as connection:
                connection.execute(
                    'SELECT pg_advisory_xact_lock(%s)',
                    (self._advisory_lock_id(idempotency_key),),
                )
                existing = connection.execute(
                    '''SELECT session_token_hash, request_hash, response
                       FROM campaign_creation_requests WHERE idempotency_key = %s FOR UPDATE''',
                    (idempotency_key,),
                ).fetchone()
                if existing:
                    if not hmac.compare_digest(bytes(existing['session_token_hash']), token_digest):
                        raise SessionUnauthorized('invalid session credential')
                    if existing['request_hash'] != request_digest:
                        raise IdempotencyConflict('idempotency key was already used for another request')
                    return copy.deepcopy(existing['response'])

                connection.execute(
                    'INSERT INTO campaigns (campaign_id, ruleset, campaign_type, campaign_title) VALUES (%s, %s, %s, %s)',
                    (campaign_id, ruleset, campaign_type, campaign_title),
                )
                connection.execute(
                    '''INSERT INTO sessions (session_id, campaign_id, session_token_hash, current_revision)
                       VALUES (%s, %s, %s, 0)''',
                    (session_id, campaign_id, token_digest),
                )
                connection.execute(
                    '''INSERT INTO campaign_snapshots (session_id, revision, state, state_hash)
                       VALUES (%s, 0, %s, %s)''',
                    (session_id, Jsonb(state), state_digest),
                )
                connection.execute(
                    '''INSERT INTO campaign_creation_requests
                       (idempotency_key, session_token_hash, request_hash, campaign_id, session_id, response)
                       VALUES (%s, %s, %s, %s, %s, %s)''',
                    (idempotency_key, token_digest, request_digest, campaign_id, session_id, Jsonb(response)),
                )
        except psycopg.Error as exc:
            raise PersistenceUnavailable('campaign persistence is unavailable') from exc
        return response

    def load_session(self, *, session_id: uuid.UUID, session_token: str) -> dict[str, Any]:
        token_digest = self._token_digest(session_token)
        try:
            with self._connect() as connection:
                row = connection.execute(
                    '''SELECT s.session_id, s.campaign_id, s.current_revision,
                              s.session_token_hash, c.ruleset, p.state, p.state_hash
                       FROM sessions AS s
                       JOIN campaigns AS c ON c.campaign_id = s.campaign_id
                       JOIN campaign_snapshots AS p
                         ON p.session_id = s.session_id AND p.revision = s.current_revision
                       WHERE s.session_id = %s''',
                    (session_id,),
                ).fetchone()
                if row is None:
                    raise CampaignNotFound('session not found')
                if not hmac.compare_digest(bytes(row['session_token_hash']), token_digest):
                    raise SessionUnauthorized('invalid session credential')
                if row['ruleset'] != _RULESET:
                    raise SnapshotIntegrityError('unsupported campaign ruleset')
                state = self._verify_snapshot(row['state'], row['state_hash'])
                return {
                    'campaign_id': str(row['campaign_id']),
                    'session_id': str(row['session_id']),
                    'ruleset': row['ruleset'],
                    'revision': row['current_revision'],
                    'state': state,
                }
        except psycopg.Error as exc:
            raise PersistenceUnavailable('campaign persistence is unavailable') from exc

    def commit_turn(
        self,
        *,
        session_id: uuid.UUID,
        session_token: str,
        expected_revision: int,
        idempotency_key: str,
        request_payload: dict[str, Any],
        execute_turn: Callable[[dict[str, Any], str], Any],
    ) -> dict[str, Any]:
        self._validate_idempotency_key(idempotency_key)
        token_digest = self._token_digest(session_token)
        request_digest = self._digest({
            'expected_revision': expected_revision,
            'request': request_payload,
        })
        try:
            # The session row lock serializes turns. The lock remains held through
            # rule resolution and narration so state, idempotency result and next
            # immutable snapshot commit as one transaction.
            with self._connect() as connection:
                session = connection.execute(
                    '''SELECT s.session_id, s.campaign_id, s.current_revision,
                              s.session_token_hash, c.ruleset
                       FROM sessions AS s
                       JOIN campaigns AS c ON c.campaign_id = s.campaign_id
                       WHERE s.session_id = %s FOR UPDATE OF s''',
                    (session_id,),
                ).fetchone()
                if session is None:
                    raise CampaignNotFound('session not found')
                if not hmac.compare_digest(bytes(session['session_token_hash']), token_digest):
                    raise SessionUnauthorized('invalid session credential')
                if session['ruleset'] != _RULESET:
                    raise SnapshotIntegrityError('unsupported campaign ruleset')

                prior = connection.execute(
                    '''SELECT request_hash, response FROM campaign_turn_events
                       WHERE session_id = %s AND idempotency_key = %s''',
                    (session_id, idempotency_key),
                ).fetchone()
                if prior:
                    if prior['request_hash'] != request_digest:
                        raise IdempotencyConflict('idempotency key was already used for another request')
                    return copy.deepcopy(prior['response'])

                current_revision = int(session['current_revision'])
                if expected_revision != current_revision:
                    raise RevisionConflict(current_revision)
                snapshot = connection.execute(
                    '''SELECT state, state_hash FROM campaign_snapshots
                       WHERE session_id = %s AND revision = %s''',
                    (session_id, current_revision),
                ).fetchone()
                if snapshot is None:
                    raise SnapshotIntegrityError('current campaign snapshot is missing')
                state = self._verify_snapshot(snapshot['state'], snapshot['state_hash'])
                campaign_id = str(session['campaign_id'])
                result = execute_turn(state, campaign_id)
                if hasattr(result, 'model_dump'):
                    result = result.model_dump(mode='json')
                if not isinstance(result, dict) or not isinstance(result.get('state'), dict):
                    raise SnapshotIntegrityError('turn executor returned an invalid state')

                next_revision = current_revision + 1
                response = {
                    **result,
                    'campaign_id': campaign_id,
                    'session_id': str(session_id),
                    'revision': next_revision,
                }
                new_state = response['state']
                state_digest = self._digest(new_state)
                connection.execute(
                    '''INSERT INTO campaign_snapshots (session_id, revision, state, state_hash)
                       VALUES (%s, %s, %s, %s)''',
                    (session_id, next_revision, Jsonb(new_state), state_digest),
                )
                connection.execute(
                    '''UPDATE sessions SET current_revision = %s, updated_at = now()
                       WHERE session_id = %s''',
                    (next_revision, session_id),
                )
                connection.execute(
                    '''INSERT INTO campaign_turn_events
                       (event_id, session_id, idempotency_key, request_hash,
                        base_revision, result_revision, request, response)
                       VALUES (%s, %s, %s, %s, %s, %s, %s, %s)''',
                    (
                        uuid.uuid4(), session_id, idempotency_key, request_digest,
                        current_revision, next_revision, Jsonb(request_payload), Jsonb(response),
                    ),
                )
                return response
        except psycopg.Error as exc:
            raise PersistenceUnavailable('campaign persistence is unavailable') from exc
