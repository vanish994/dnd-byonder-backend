import os
import re
import sqlite3
import logging
import time
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field, StrictInt, StrictStr, ValidationError, constr, model_validator

from game.contracts import GameTurnRequest, GameTurnResponse
from game.orchestrator import (
    GameOrchestrator,
    InvalidGameAction,
    NarrationError,
    RuleResolutionError,
)
from rule_engine.dice import MAX_MODIFIER, DiceExpressionError, roll_dice
from rule_engine.source_policy import STRICT_EDITION_SCOPE, append_strict_source_policy
from services.gemini_narrator import GeminiNarratorClient
from services.mimo_narrator import MimoNarratorClient


logger = logging.getLogger(__name__)

DB_PATH = Path(
    os.getenv(
        'RULES_DB_PATH',
        Path(__file__).resolve().parent.parent / 'dnd2024_knowledge_base' / 'knowledge_base' / 'dnd_rules.db',
    )
)
API_KEY = os.getenv('RULE_ENGINE_API_KEY', '').strip()
MIMO_BASE_URL = os.getenv('MIMO_BASE_URL', '').strip()
MIMO_MODEL = os.getenv('MIMO_MODEL', 'mimo-v2.6-flash').strip()
MIMO_API_KEY = os.getenv('MIMO_API_KEY', '').strip()
NARRATOR_PROVIDER = os.getenv('NARRATOR_PROVIDER', 'gemini').strip().lower()
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '').strip()
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-3.8-flash').strip()
app = FastAPI(title='D&D 2024 Rule Knowledge API', version='0.1.0')

ABILITY_CHECK_RULE_ID = 'ability_check.mvp.v1'
SAVING_THROW_RULE_ID = 'saving_throw.mvp.v1'
ATTACK_ROLL_RULE_ID = 'attack_roll.mvp.v1'
ATTACK_DAMAGE_RULE_ID = 'attack_damage.mvp.v1'
INITIATIVE_RULE_ID = 'initiative.mvp.v1'
COMBAT_RULE_ID = 'combat.mvp.v1'
RULE_RESOLUTION_SCHEMA_VERSION = 'rule-resolution-v1'
TextAction = constr(strict=True, min_length=1, max_length=200)


class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=8, ge=1, le=30)
    edition: str | None = None
    document: str | None = None


class AbilityCheckAction(BaseModel):
    type: Literal['ability_check']
    ability: Literal['strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma']
    dc: StrictInt = Field(ge=1)
    modifier: StrictInt = Field(ge=-MAX_MODIFIER, le=MAX_MODIFIER)

    class Config:
        extra = 'forbid'


class SavingThrowAction(BaseModel):
    type: Literal['saving_throw']
    ability: Literal['strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma']
    dc: StrictInt = Field(ge=1)
    modifier: StrictInt = Field(ge=-MAX_MODIFIER, le=MAX_MODIFIER)

    class Config:
        extra = 'forbid'


class AttackDamage(BaseModel):
    dice: Literal['1d8']
    modifier: StrictInt = Field(ge=-MAX_MODIFIER, le=MAX_MODIFIER)

    class Config:
        extra = 'forbid'


class AttackAction(BaseModel):
    type: Literal['attack']
    attack_bonus: StrictInt
    target_ac: StrictInt | None = None
    actor_id: StrictStr | None = None
    target_id: StrictStr | None = None
    damage: AttackDamage | None = None

    @model_validator(mode='after')
    def validate_attack_target(self):
        combat_fields = (self.actor_id is not None, self.target_id is not None)
        if combat_fields[0] != combat_fields[1]:
            raise ValueError('actor_id and target_id must be provided together')
        if self.actor_id is None and self.target_ac is None:
            raise ValueError('target_ac is required outside combat')
        if self.actor_id is not None and self.target_ac is not None:
            raise ValueError('target_ac must come from combat state')
        return self

    class Config:
        extra = 'forbid'


class CombatantSpec(BaseModel):
    id: StrictStr = Field(min_length=1, max_length=64)
    hp: StrictInt = Field(ge=0)
    max_hp: StrictInt = Field(gt=0)
    ac: StrictInt = Field(ge=0)
    initiative_modifier: StrictInt = Field(ge=-MAX_MODIFIER, le=MAX_MODIFIER)
    position: StrictInt = Field(ge=0)
    movement_speed: StrictInt = Field(ge=0, le=MAX_MODIFIER)
    side: StrictStr = Field(default='neutral', min_length=1, max_length=64)

    class Config:
        extra = 'forbid'


class StartCombatAction(BaseModel):
    type: Literal['start_combat']
    combatants: list[CombatantSpec] = Field(min_length=2)

    class Config:
        extra = 'forbid'


class MoveAction(BaseModel):
    type: Literal['move']
    actor_id: StrictStr = Field(min_length=1, max_length=64)
    distance: StrictInt = Field(ge=0)

    class Config:
        extra = 'forbid'


class EndTurnAction(BaseModel):
    type: Literal['end_turn']
    actor_id: StrictStr = Field(min_length=1, max_length=64)

    class Config:
        extra = 'forbid'


class ResolveRequest(BaseModel):
    action: TextAction | AbilityCheckAction | SavingThrowAction | AttackAction | StartCombatAction | MoveAction | EndTurnAction
    state: dict[str, Any] = Field(default_factory=dict)
    rule_ids: list[str] = Field(default_factory=list)


class DiceRollRequest(BaseModel):
    expression: str = Field(min_length=1, max_length=50)
    mode: Literal['normal', 'advantage', 'disadvantage'] = 'normal'


def authorize(x_api_key: str | None):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail='invalid api key')


def connect():
    if not DB_PATH.exists():
        raise HTTPException(status_code=500, detail='rules database not found')
    con = sqlite3.connect(f'file:{DB_PATH}?mode=ro', uri=True)
    con.row_factory = sqlite3.Row
    return con


def fts_query(text: str) -> str:
    # Treat input as search terms, never as raw FTS5 syntax.
    terms = re.findall(r'[\wÀ-ÿ]+', text.lower())
    if not terms:
        raise HTTPException(status_code=400, detail='query has no searchable terms')
    return ' AND '.join('"' + t.replace('"', '') + '"' for t in terms[:16])


def serialize(row):
    return {
        'source_id': row['doc_id'],
        'edition': row['edition'],
        'chunk_id': row['chunk_id'],
        'title': row['title'],
        'section': row['section'],
        'page': row['page'],
        'line_start': row['line_start'],
        'line_end': row['line_end'],
        'text': row['text'],
    }


@app.get('/health')
def health():
    con = connect()
    try:
        documents = con.execute('SELECT count(*) FROM documents').fetchone()[0]
        chunks = con.execute('SELECT count(*) FROM chunks').fetchone()[0]
    finally:
        con.close()
    return {
        'status': 'ok',
        'edition_scope': list(STRICT_EDITION_SCOPE),
        'source_policy': 'explicit 2024/2025 only; canonical source preferred for duplicate titles',
        'documents': documents,
        'chunks': chunks,
    }


@app.post('/v1/dice/roll')
def dice_roll(body: DiceRollRequest, x_api_key: str | None = Header(default=None)):
    authorize(x_api_key)
    try:
        return roll_dice(body.expression, body.mode)
    except DiceExpressionError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


@app.post('/v1/rules/search')
def search(body: SearchRequest, x_api_key: str | None = Header(default=None)):
    authorize(x_api_key)
    con = connect()
    try:
        sql = """SELECT d.doc_id, d.edition, c.chunk_id, c.title, c.section, c.page, c.line_start, c.line_end, c.text
                 FROM chunks_fts f JOIN chunks c ON c.rowid=f.rowid
                 JOIN documents d ON d.doc_id=c.doc_id
                 WHERE chunks_fts MATCH ?"""
        args: list[Any] = [fts_query(body.query)]
        sql, args = append_strict_source_policy(sql, args, body.edition)
        if body.document:
            sql += ' AND c.title LIKE ?'
            args.append('%' + body.document + '%')
        sql += ' LIMIT ?'
        args.append(body.limit)
        rows = con.execute(sql, args).fetchall()
        return {
            'query': body.query,
            'count': len(rows),
            'results': [serialize(r) for r in rows],
            'source_policy': 'explicit 2024/2025 only; canonical source preferred for duplicate titles; candidates require validation',
        }
    finally:
        con.close()


@app.get('/v1/rules/context')
def context(
    q: str = Query(min_length=2, max_length=500),
    limit: int = Query(default=8, ge=1, le=30),
    x_api_key: str | None = Header(default=None),
):
    return search(SearchRequest(query=q, limit=limit), x_api_key)


@app.post('/v1/resolve')
def resolve(body: ResolveRequest, x_api_key: str | None = Header(default=None)):
    authorize(x_api_key)
    return resolve_request(body)


def resolve_game_action(action: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Validate and resolve a structured action using the existing Rule Engine."""
    try:
        body = ResolveRequest(action=action, state=state)
    except ValidationError as exc:
        raise ValueError('invalid structured action') from exc
    resolution = resolve_request(body)
    state.clear()
    state.update(body.state)
    return resolution


def build_game_orchestrator() -> GameOrchestrator:
    if NARRATOR_PROVIDER == 'gemini':
        if not GEMINI_API_KEY:
            raise HTTPException(status_code=503, detail='Gemini narrator is not configured')
        try:
            timeout = float(os.getenv('GEMINI_TIMEOUT_SECONDS', '30'))
            max_output_tokens = int(os.getenv('GEMINI_MAX_OUTPUT_TOKENS', '512'))
            temperature = float(os.getenv('GEMINI_TEMPERATURE', '0.7'))
        except ValueError as exc:
            raise HTTPException(status_code=503, detail='invalid Gemini narrator configuration') from exc
        narrator = GeminiNarratorClient(
            api_key=GEMINI_API_KEY,
            model=GEMINI_MODEL,
            timeout_seconds=timeout,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
    elif NARRATOR_PROVIDER == 'mimo':
        if not MIMO_BASE_URL or not MIMO_API_KEY:
            raise HTTPException(status_code=503, detail='MiMo narrator is not configured')
        try:
            timeout = float(os.getenv('MIMO_TIMEOUT_SECONDS', '30'))
        except ValueError as exc:
            raise HTTPException(status_code=503, detail='invalid MIMO_TIMEOUT_SECONDS') from exc
        narrator = MimoNarratorClient(
            base_url=MIMO_BASE_URL,
            model=MIMO_MODEL,
            api_key=MIMO_API_KEY,
            timeout_seconds=timeout,
        )
    else:
        raise HTTPException(status_code=503, detail='unsupported narrator provider')
    return GameOrchestrator(narrator, resolve_action=resolve_game_action)


@app.post('/v1/game/turn', response_model=GameTurnResponse)
def game_turn(
    body: GameTurnRequest,
    x_api_key: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
):
    authorize(x_api_key)
    started = time.perf_counter()
    request_id = x_request_id if isinstance(x_request_id, str) else None
    logger.info(
        "GAME_TURN_STARTED action=%s campaign_id=%s request_id=%s",
        (body.action or {}).get('type', 'none'),
        body.campaign_id,
        request_id or 'none',
    )
    try:
        response = build_game_orchestrator().turn(body, request_id=request_id)
        resolution = response.rule_resolution if hasattr(response, 'rule_resolution') else response['rule_resolution']
        logger.info(
            "GAME_TURN_COMPLETED resolution_status=%s duration_ms=%.1f request_id=%s",
            resolution.get('status', 'unknown'),
            (time.perf_counter() - started) * 1000,
            request_id or 'none',
        )
        return response
    except InvalidGameAction as exc:
        logger.warning(
            "GAME_TURN_INVALID_ACTION duration_ms=%.1f error_type=%s request_id=%s",
            (time.perf_counter() - started) * 1000,
            type(exc).__name__,
            request_id or 'none',
        )
        raise HTTPException(status_code=422, detail='invalid structured action') from exc
    except RuleResolutionError as exc:
        logger.warning(
            "GAME_TURN_RULE_ERROR duration_ms=%.1f error_type=%s request_id=%s",
            (time.perf_counter() - started) * 1000,
            type(exc).__name__,
            request_id or 'none',
        )
        raise HTTPException(status_code=502, detail='invalid Rule Engine resolution') from exc
    except NarrationError as exc:
        logger.warning(
            "GAME_TURN_NARRATION_ERROR duration_ms=%.1f error_type=%s request_id=%s",
            (time.perf_counter() - started) * 1000,
            type(exc.__cause__).__name__ if exc.__cause__ else type(exc).__name__,
            request_id or 'none',
        )
        raise HTTPException(
            status_code=502,
            detail={
                'message': 'Narrator unavailable',
                'rule_resolution': exc.rule_resolution,
            },
        ) from exc


def _combat_available_actions(combat: dict[str, Any]) -> list[dict[str, str]]:
    if not combat.get('active'):
        return []
    actor = combat.get('combatants', {}).get(combat.get('current_actor_id'))
    if not actor or actor.get('unconscious'):
        return [{'type': 'end_turn'}]
    actions: list[dict[str, str]] = []
    if actor.get('movement_remaining', 0) > 0:
        actions.append({'type': 'move'})
    if actor.get('action_available'):
        actions.append({'type': 'attack'})
    actions.append({'type': 'end_turn'})
    return actions


def _combat_resolution(
    action: dict[str, Any],
    *,
    check: dict[str, Any],
    rolls: list[dict[str, Any]],
    outcome: dict[str, Any],
    rules_used: list[str] | None = None,
) -> dict[str, Any]:
    return {
        'schema_version': RULE_RESOLUTION_SCHEMA_VERSION,
        'resolution_id': str(uuid.uuid4()),
        'status': 'resolved',
        'action': action,
        'check': check,
        'rolls': rolls,
        'outcome': outcome,
        'rules_used': rules_used or [COMBAT_RULE_ID],
    }


def _require_combat(state: dict[str, Any]) -> dict[str, Any]:
    combat = state.get('combat')
    if not isinstance(combat, dict) or not combat.get('active'):
        raise ValueError('combat is not active')
    if not isinstance(combat.get('combatants'), dict):
        raise ValueError('combatants are missing')
    return combat


def _require_current_actor(combat: dict[str, Any], actor_id: str) -> dict[str, Any]:
    if actor_id != combat.get('current_actor_id'):
        raise ValueError('actor is not the current actor')
    actor = combat['combatants'].get(actor_id)
    if not isinstance(actor, dict):
        raise ValueError('actor does not exist')
    return actor


def _finish_combat_if_needed(combat: dict[str, Any]) -> None:
    combatants = combat['combatants']
    sides = {item.get('side', 'neutral') for item in combatants.values()}
    defeated = [side for side in sides if not any(item.get('side') == side and item.get('hp', 0) > 0 for item in combatants.values())]
    if len(sides) >= 2 and defeated:
        winners = [side for side in sides if side not in defeated]
        combat['active'] = False
        combat['winner_side'] = winners[0] if len(winners) == 1 else None
        combat['available_actions'] = []


def resolve_start_combat(
    body: ResolveRequest,
    *,
    randbelow: Callable[[int], int] | None = None,
) -> dict[str, Any]:
    action = body.action
    if not isinstance(action, StartCombatAction):
        raise TypeError('resolve_start_combat requires a validated action')
    if body.state.get('combat', {}).get('active'):
        raise ValueError('combat is already active')
    combatants: dict[str, dict[str, Any]] = {}
    rolls: list[dict[str, Any]] = []
    initiative_rows: list[tuple[str, int, int]] = []
    for spec in action.combatants:
        if spec.id in combatants:
            raise ValueError('combatant ids must be unique')
        if spec.hp > spec.max_hp:
            raise ValueError('hp cannot exceed max_hp')
        roll = roll_dice('d20', randbelow=randbelow)
        d20_result = roll['rolls'][0]
        initiative_total = d20_result + spec.initiative_modifier
        initiative_rows.append((spec.id, initiative_total, spec.initiative_modifier))
        rolls.append({
            'type': 'd20',
            'purpose': 'initiative',
            'actor_id': spec.id,
            'result': d20_result,
        })
        combatants[spec.id] = {
            'id': spec.id,
            'hp': spec.hp,
            'max_hp': spec.max_hp,
            'ac': spec.ac,
            'initiative': initiative_total,
            'initiative_modifier': spec.initiative_modifier,
            'position': spec.position,
            'movement_speed': spec.movement_speed,
            'unconscious': spec.hp == 0,
            'side': spec.side,
            'action_available': True,
            'bonus_action_available': True,
            'reaction_available': True,
            'movement_remaining': spec.movement_speed,
        }
    order = [item[0] for item in sorted(initiative_rows, key=lambda row: (-row[1], -row[2], row[0]))]
    combat = {
        'active': True,
        'round': 1,
        'turn_index': 0,
        'current_actor_id': order[0],
        'turn_order': order,
        'combatants': combatants,
        'winner_side': None,
    }
    combat['available_actions'] = _combat_available_actions(combat)
    body.state['combat'] = combat
    return _combat_resolution(
        {'type': 'start_combat'},
        check={'combatant_ids': order},
        rolls=rolls,
        outcome={
            'combat_started': True,
            'round': 1,
            'turn_index': 0,
            'current_actor_id': order[0],
            'turn_order': order,
        },
        rules_used=[INITIATIVE_RULE_ID, COMBAT_RULE_ID],
    )


def resolve_move(body: ResolveRequest) -> dict[str, Any]:
    action = body.action
    if not isinstance(action, MoveAction):
        raise TypeError('resolve_move requires a validated action')
    combat = _require_combat(body.state)
    actor = _require_current_actor(combat, action.actor_id)
    if actor.get('unconscious'):
        raise ValueError('unconscious actor cannot move')
    if action.distance > actor.get('movement_remaining', 0):
        raise ValueError('movement exceeds remaining movement')
    actor['position'] += action.distance
    actor['movement_remaining'] -= action.distance
    combat['available_actions'] = _combat_available_actions(combat)
    return _combat_resolution(
        {'type': 'move', 'actor_id': action.actor_id},
        check={'distance': action.distance},
        rolls=[],
        outcome={
            'distance': action.distance,
            'position': actor['position'],
            'movement_remaining': actor['movement_remaining'],
        },
    )


def resolve_combat_attack(
    body: ResolveRequest,
    *,
    randbelow: Callable[[int], int] | None = None,
) -> dict[str, Any]:
    action = body.action
    if not isinstance(action, AttackAction) or action.actor_id is None or action.target_id is None:
        raise TypeError('resolve_combat_attack requires a validated combat attack')
    combat = _require_combat(body.state)
    actor = _require_current_actor(combat, action.actor_id)
    target = combat['combatants'].get(action.target_id)
    if target is None:
        raise ValueError('target does not exist')
    if actor.get('unconscious'):
        raise ValueError('unconscious actor cannot attack')
    if target.get('unconscious'):
        raise ValueError('unconscious target is invalid')
    if not actor.get('action_available'):
        raise ValueError('action is already consumed')
    if action.damage is None:
        raise ValueError('combat attack requires damage')
    standalone_action = AttackAction(
        type='attack',
        attack_bonus=action.attack_bonus,
        target_ac=target['ac'],
        damage=action.damage,
    )
    resolution = resolve_attack(ResolveRequest(action=standalone_action), randbelow=randbelow)
    actor['action_available'] = False
    damage = resolution['outcome']['damage']
    hp_before = target['hp']
    if resolution['outcome']['hit'] and damage is not None:
        target['hp'] = max(0, target['hp'] - damage)
        target['unconscious'] = target['hp'] == 0
    _finish_combat_if_needed(combat)
    combat['available_actions'] = _combat_available_actions(combat)
    resolution['action'] = {
        'type': 'attack',
        'actor_id': action.actor_id,
        'target_id': action.target_id,
    }
    resolution['check']['target_ac'] = target['ac']
    resolution['outcome'].update({
        'target_hp_before': hp_before,
        'target_hp_after': target['hp'],
        'target_unconscious': target['unconscious'],
        'combat_active': combat['active'],
    })
    return resolution


def resolve_end_turn(body: ResolveRequest) -> dict[str, Any]:
    action = body.action
    if not isinstance(action, EndTurnAction):
        raise TypeError('resolve_end_turn requires a validated action')
    combat = _require_combat(body.state)
    _require_current_actor(combat, action.actor_id)
    order = combat['turn_order']
    previous_index = combat['turn_index']
    next_index = previous_index
    wrapped = False
    next_actor = None
    for _ in range(len(order)):
        next_index += 1
        if next_index >= len(order):
            next_index = 0
            wrapped = True
        candidate = combat['combatants'][order[next_index]]
        if not candidate.get('unconscious'):
            next_actor = candidate
            break
    if next_actor is None:
        combat['active'] = False
        combat['available_actions'] = []
        return _combat_resolution(
            {'type': 'end_turn', 'actor_id': action.actor_id},
            check={}, rolls=[],
            outcome={'combat_active': False, 'combat_ended': True},
        )
    if wrapped:
        combat['round'] += 1
    combat['turn_index'] = next_index
    combat['current_actor_id'] = next_actor['id']
    next_actor['action_available'] = True
    next_actor['bonus_action_available'] = True
    next_actor['reaction_available'] = True
    next_actor['movement_remaining'] = next_actor['movement_speed']
    combat['available_actions'] = _combat_available_actions(combat)
    return _combat_resolution(
        {'type': 'end_turn', 'actor_id': action.actor_id},
        check={}, rolls=[],
        outcome={
            'combat_active': combat['active'],
            'combat_ended': False,
            'round': combat['round'],
            'turn_index': next_index,
            'current_actor_id': next_actor['id'],
        },
    )


def resolve_request(
    body: ResolveRequest,
    *,
    randbelow: Callable[[int], int] | None = None,
):
    if isinstance(body.action, StartCombatAction):
        return resolve_start_combat(body, randbelow=randbelow)
    if isinstance(body.action, MoveAction):
        return resolve_move(body)
    if isinstance(body.action, EndTurnAction):
        return resolve_end_turn(body)
    if isinstance(body.action, AbilityCheckAction):
        return resolve_explicit_action(body, randbelow=randbelow)
    if isinstance(body.action, SavingThrowAction):
        return resolve_saving_throw(body, randbelow=randbelow)
    if isinstance(body.action, AttackAction):
        if body.action.actor_id is not None:
            return resolve_combat_attack(body, randbelow=randbelow)
        return resolve_attack(body, randbelow=randbelow)

    # Text-only and unsupported actions remain fail-closed; KB candidates are evidence, not executable rules.
    return {
        'schema_version': RULE_RESOLUTION_SCHEMA_VERSION,
        'status': 'needs_rule_validation',
        'reason': 'No deterministic resolution was emitted because the requested rule has not been bound to a validated mechanic.',
    }


def resolve_explicit_action(
    body: ResolveRequest,
    *,
    randbelow: Callable[[int], int] | None = None,
):
    """Resolve the supported explicit ability-check MVP without consulting KB candidates."""
    action = body.action
    if not isinstance(action, AbilityCheckAction):
        raise TypeError('resolve_explicit_action requires a validated ability_check action')

    roll = roll_dice('d20', randbelow=randbelow)
    d20_result = roll['rolls'][0]
    total = d20_result + action.modifier
    return {
        'schema_version': RULE_RESOLUTION_SCHEMA_VERSION,
        'resolution_id': str(uuid.uuid4()),
        'status': 'resolved',
        'action': {'type': action.type, 'ability': action.ability},
        'check': {
            'ability': action.ability,
            'dc': action.dc,
            'modifier': action.modifier,
        },
        'rolls': [{'type': 'd20', 'result': d20_result}],
        'outcome': {'total': total, 'success': total >= action.dc},
        'rules_used': [ABILITY_CHECK_RULE_ID],
    }


def resolve_saving_throw(
    body: ResolveRequest,
    *,
    randbelow: Callable[[int], int] | None = None,
):
    """Resolve the supported saving-throw MVP without critical rules."""
    action = body.action
    if not isinstance(action, SavingThrowAction):
        raise TypeError('resolve_saving_throw requires a validated saving_throw action')
    roll = roll_dice('d20', randbelow=randbelow)
    d20_result = roll['rolls'][0]
    total = d20_result + action.modifier
    return {
        'schema_version': RULE_RESOLUTION_SCHEMA_VERSION,
        'resolution_id': str(uuid.uuid4()),
        'status': 'resolved',
        'action': {'type': action.type, 'ability': action.ability},
        'check': {
            'ability': action.ability,
            'dc': action.dc,
            'modifier': action.modifier,
        },
        'rolls': [{'type': 'd20', 'result': d20_result}],
        'outcome': {'total': total, 'success': total >= action.dc},
        'rules_used': [SAVING_THROW_RULE_ID],
    }


def resolve_attack(
    body: ResolveRequest,
    *,
    randbelow: Callable[[int], int] | None = None,
):
    """Resolve an attack roll, including natural 20 and natural 1 classification."""
    action = body.action
    if not isinstance(action, AttackAction):
        raise TypeError('resolve_attack requires a validated attack action')
    roll = roll_dice('d20', randbelow=randbelow)
    d20_result = roll['rolls'][0]
    total = d20_result + action.attack_bonus
    critical = d20_result == 20
    natural_1 = d20_result == 1
    hit = True if critical else False if natural_1 else total >= action.target_ac
    damage_result = None
    rolls = [{'type': 'd20', 'result': d20_result}]
    rules_used = [ATTACK_ROLL_RULE_ID]
    if hit and action.damage is not None:
        damage_roll = roll_dice(action.damage.dice, randbelow=randbelow)
        damage_result = damage_roll['rolls'][0] + action.damage.modifier
        rolls.append({'type': 'd8', 'result': damage_roll['rolls'][0]})
        rules_used.append(ATTACK_DAMAGE_RULE_ID)
    check = {
        'attack_bonus': action.attack_bonus,
        'target_ac': action.target_ac,
    }
    if action.damage is not None:
        check['damage'] = {
            'dice': action.damage.dice,
            'modifier': action.damage.modifier,
        }
    return {
        'schema_version': RULE_RESOLUTION_SCHEMA_VERSION,
        'resolution_id': str(uuid.uuid4()),
        'status': 'resolved',
        'action': {'type': action.type},
        'check': check,
        'rolls': rolls,
        'outcome': {
            'total': total,
            'hit': hit,
            'critical': critical,
            'natural_1': natural_1,
            'damage': damage_result,
        },
        'rules_used': rules_used,
    }
