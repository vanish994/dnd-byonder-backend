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

from game.contracts import (
    CharacterCreationResponse,
    CharacterOptionsResponse,
    CharacterValidationResponse,
    GameTurnRequest,
    GameTurnResponse,
    GuidedCharacterRequest,
)
from game.orchestrator import (
    GameOrchestrator,
    InvalidGameAction,
    NarrationError,
    RuleResolutionError,
)
from rule_engine.conditions import (
    advance_condition_durations,
    has_condition,
    has_disadvantage,
)
from rule_engine.dice import MAX_MODIFIER, DiceExpressionError, roll_dice
from rule_engine.character import (
    SKILL_TO_ABILITY,
    Character,
    build_guided_character,
    character_options,
    character_to_state,
    derive_character,
)
from rule_engine.source_policy import STRICT_EDITION_SCOPE, append_strict_source_policy
from services.groq_narrator import GroqNarratorClient


logger = logging.getLogger(__name__)

DB_PATH = Path(
    os.getenv(
        'RULES_DB_PATH',
        Path(__file__).resolve().parent.parent / 'dnd2024_knowledge_base' / 'knowledge_base' / 'dnd_rules.db',
    )
)
API_KEY = os.getenv('RULE_ENGINE_API_KEY', '').strip()
GROQ_API_KEY = os.getenv('GROQ_API_KEY', '').strip()
GROQ_MODEL = os.getenv('GROQ_MODEL', '').strip()
GROQ_BASE_URL = os.getenv('GROQ_BASE_URL', 'https://api.groq.com/openai/v1').strip()
app = FastAPI(title='D&D 2024 Rule Knowledge API', version='0.1.0')

ABILITY_CHECK_RULE_ID = 'ability_check.mvp.v1'
SAVING_THROW_RULE_ID = 'saving_throw.mvp.v1'
ATTACK_ROLL_RULE_ID = 'attack_roll.mvp.v1'
ATTACK_DAMAGE_RULE_ID = 'attack_damage.mvp.v1'
INITIATIVE_RULE_ID = 'initiative.mvp.v1'
COMBAT_RULE_ID = 'combat.mvp.v1'
ABILITY_MODIFIER_RULE_ID = 'ability_modifier.v1'
PROFICIENCY_BONUS_RULE_ID = 'proficiency_bonus.v1'
SKILL_CHECK_RULE_ID = 'skill_check.v1'
SAVING_THROW_DERIVED_RULE_ID = 'saving_throw.v1'
UNARMORED_AC_RULE_ID = 'unarmored_ac.v1'
WEAPON_ATTACK_RULE_ID = 'weapon_attack.v1'
WEAPON_DAMAGE_RULE_ID = 'weapon_damage.v1'
CHARACTER_RULE_ID = 'character.v1'
RULE_RESOLUTION_SCHEMA_VERSION = 'rule-resolution-v1'
INITIAL_SCENE_ID = 'intro'
INITIAL_ENCOUNTER_ID = 'intro-ambush'
ACTION_PRESENTATION_FIELDS = {'label', 'description', 'player_input'}
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
    modifier: StrictInt | None = Field(default=None, ge=-MAX_MODIFIER, le=MAX_MODIFIER)
    character_id: StrictStr | None = None

    @model_validator(mode='after')
    def validate_source(self):
        if self.modifier is None and self.character_id is None:
            raise ValueError('ability check requires modifier or character_id')
        if self.modifier is not None and self.character_id is not None:
            raise ValueError('character-based ability check cannot receive modifier')
        return self

    class Config:
        extra = 'forbid'


class SavingThrowAction(BaseModel):
    type: Literal['saving_throw']
    ability: Literal['strength', 'dexterity', 'constitution', 'intelligence', 'wisdom', 'charisma']
    dc: StrictInt = Field(ge=1)
    modifier: StrictInt | None = Field(default=None, ge=-MAX_MODIFIER, le=MAX_MODIFIER)
    character_id: StrictStr | None = None

    @model_validator(mode='after')
    def validate_source(self):
        if self.modifier is None and self.character_id is None:
            raise ValueError('saving throw requires modifier or character_id')
        if self.modifier is not None and self.character_id is not None:
            raise ValueError('character-based saving throw cannot receive modifier')
        return self

    class Config:
        extra = 'forbid'


class AttackDamage(BaseModel):
    dice: Literal['1d8']
    modifier: StrictInt = Field(ge=-MAX_MODIFIER, le=MAX_MODIFIER)

    class Config:
        extra = 'forbid'


class AttackAction(BaseModel):
    type: Literal['attack']
    attack_bonus: StrictInt | None = None
    target_ac: StrictInt | None = None
    actor_id: StrictStr | None = None
    target_id: StrictStr | None = None
    damage: AttackDamage | None = None
    weapon_id: StrictStr | None = None

    @model_validator(mode='after')
    def validate_attack_target(self):
        combat_fields = (self.actor_id is not None, self.target_id is not None)
        if combat_fields[0] != combat_fields[1]:
            raise ValueError('actor_id and target_id must be provided together')
        if self.actor_id is None and self.target_ac is None:
            raise ValueError('target_ac is required outside combat')
        if self.actor_id is not None and self.target_ac is not None:
            raise ValueError('target_ac must come from combat state')
        if self.attack_bonus is None and self.weapon_id is None:
            raise ValueError('attack requires attack_bonus or weapon_id')
        if self.actor_id is None and self.attack_bonus is None:
            raise ValueError('legacy attack requires attack_bonus')
        return self

    class Config:
        extra = 'forbid'


class CombatantSpec(BaseModel):
    id: StrictStr = Field(min_length=1, max_length=64)
    hp: StrictInt | None = Field(default=None, ge=0)
    max_hp: StrictInt | None = Field(default=None, gt=0)
    ac: StrictInt | None = Field(default=None, ge=0)
    initiative_modifier: StrictInt | None = Field(default=None, ge=-MAX_MODIFIER, le=MAX_MODIFIER)
    position: StrictInt = Field(default=0, ge=0)
    movement_speed: StrictInt = Field(default=30, ge=0, le=MAX_MODIFIER)
    side: StrictStr = Field(default='neutral', min_length=1, max_length=64)
    character: Character | None = None

    class Config:
        extra = 'forbid'


class StartCombatAction(BaseModel):
    type: Literal['start_combat']
    combatants: list[CombatantSpec] | None = Field(default=None, min_length=2)
    encounter_id: StrictStr | None = Field(default=None, min_length=1, max_length=64)

    @model_validator(mode='after')
    def validate_source(self):
        if self.combatants is None and self.encounter_id is None:
            raise ValueError('start combat requires combatants or encounter_id')
        if self.combatants is not None and self.encounter_id is not None:
            raise ValueError('start combat cannot mix combatants and encounter_id')
        return self

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


class CreateCharacterAction(BaseModel):
    type: Literal['create_character']
    character: Character

    class Config:
        extra = 'forbid'


class SkillCheckAction(BaseModel):
    type: Literal['skill_check']
    skill: StrictStr
    dc: StrictInt = Field(ge=1)
    character_id: StrictStr | None = None
    modifier: StrictInt | None = Field(default=None, ge=-MAX_MODIFIER, le=MAX_MODIFIER)

    @model_validator(mode='after')
    def validate_source(self):
        if self.skill not in SKILL_TO_ABILITY:
            raise ValueError(f'unknown skill: {self.skill}')
        if self.character_id is not None and self.modifier is not None:
            raise ValueError('character-based skill check cannot receive modifier')
        if self.character_id is None and self.modifier is None:
            raise ValueError('skill check requires character_id or modifier')
        return self

    class Config:
        extra = 'forbid'


class ResolveRequest(BaseModel):
    action: TextAction | CreateCharacterAction | SkillCheckAction | AbilityCheckAction | SavingThrowAction | AttackAction | StartCombatAction | MoveAction | EndTurnAction
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


@app.get('/v1/character/options', response_model=CharacterOptionsResponse)
def get_character_options(x_api_key: str | None = Header(default=None)):
    authorize(x_api_key)
    return character_options()


def _initial_scene_actions(character_id: str) -> list[dict[str, Any]]:
    return [
        {
            'type': 'ability_check',
            'ability': 'wisdom',
            'dc': 10,
            'character_id': character_id,
            'label': 'Observar a clareira',
            'description': 'Procure sinais, sons e detalhes importantes ao redor.',
            'player_input': 'Observo cuidadosamente a clareira.',
        },
        {
            'type': 'start_combat',
            'encounter_id': INITIAL_ENCOUNTER_ID,
            'label': 'Investigar o ruído',
            'description': 'Siga o som entre as árvores e prepare-se para o perigo.',
            'player_input': 'Investigo o ruído entre as árvores.',
        },
    ]


def _initial_encounter() -> dict[str, Any]:
    return {
        'id': INITIAL_ENCOUNTER_ID,
        'enemy': {
            'id': 'goblin-scout',
            'name': 'Batedor goblin',
            'hp': 8,
            'max_hp': 8,
            'ac': 12,
            'initiative_modifier': 1,
            'position': 5,
            'movement_speed': 30,
            'side': 'enemy',
        },
    }


def _initial_scene(character_id: str) -> dict[str, Any]:
    return {
        'id': INITIAL_SCENE_ID,
        'type': 'exploration',
        'title': 'A clareira silenciosa',
        'description': 'A estrada termina em uma clareira. Um ruído se move entre as árvores.',
        'available_actions': _initial_scene_actions(character_id),
    }


def _guided_character_preview(body: GuidedCharacterRequest) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        character = build_guided_character(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    resolution_body = ResolveRequest(action=CreateCharacterAction(type='create_character', character=character))
    resolution = resolve_request(resolution_body)
    return {
        'schema_version': 'character-creation-v1',
        'valid': True,
        'character': resolution_body.state['character'],
        'derived': resolution['outcome']['derived'],
        'rule_resolution': resolution,
    }, resolution_body.state


@app.post('/v1/character/validate', response_model=CharacterValidationResponse)
def validate_character(body: GuidedCharacterRequest, x_api_key: str | None = Header(default=None)):
    authorize(x_api_key)
    preview, _state = _guided_character_preview(body)
    return preview


@app.post('/v1/character/create', response_model=CharacterCreationResponse)
def create_character(body: GuidedCharacterRequest, x_api_key: str | None = Header(default=None)):
    authorize(x_api_key)
    preview, state = _guided_character_preview(body)
    character_id = preview['character']['id']
    scene = _initial_scene(character_id)
    state['scene'] = scene
    state['encounter'] = _initial_encounter()
    return {
        **preview,
        'campaign_id': str(uuid.uuid4()),
        'state': state,
        'available_actions': scene['available_actions'],
    }


def resolve_game_action(action: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Validate and resolve a structured action using the existing Rule Engine."""
    mechanical_action = {
        key: value for key, value in action.items()
        if key not in ACTION_PRESENTATION_FIELDS
    }
    if mechanical_action.get('type') == 'start_combat' and mechanical_action.get('encounter_id'):
        encounter = state.get('encounter')
        if not isinstance(encounter, dict) or encounter.get('id') != mechanical_action['encounter_id']:
            raise ValueError('unknown encounter')
        character = state.get('character')
        enemy = encounter.get('enemy')
        if not isinstance(character, dict) or not isinstance(enemy, dict):
            raise ValueError('encounter is incomplete')
        mechanical_action = {
            **mechanical_action,
            'combatants': [
                {'id': character['id'], 'character': character, 'side': 'player'},
                {
                    'id': enemy['id'],
                    'hp': enemy['hp'],
                    'max_hp': enemy['max_hp'],
                    'ac': enemy['ac'],
                    'initiative_modifier': enemy['initiative_modifier'],
                    'position': enemy['position'],
                    'movement_speed': enemy['movement_speed'],
                    'side': enemy['side'],
                },
            ],
        }
        mechanical_action.pop('encounter_id', None)
    try:
        body = ResolveRequest(action=mechanical_action, state=state)
    except ValidationError as exc:
        raise ValueError('invalid structured action') from exc
    resolution = resolve_request(body)
    state.clear()
    state.update(body.state)
    return resolution


def build_game_orchestrator() -> GameOrchestrator:
    if not GROQ_API_KEY or not GROQ_MODEL or not GROQ_BASE_URL:
        raise HTTPException(status_code=503, detail='Groq narrator is not configured')
    try:
        timeout = float(os.getenv('GROQ_TIMEOUT_SECONDS', '30'))
        max_output_tokens = int(os.getenv('GROQ_MAX_OUTPUT_TOKENS', '512'))
        temperature = float(os.getenv('GROQ_TEMPERATURE', '0.7'))
    except ValueError as exc:
        raise HTTPException(status_code=503, detail='invalid Groq narrator configuration') from exc
    narrator = GroqNarratorClient(
        api_key=GROQ_API_KEY,
        model=GROQ_MODEL,
        base_url=GROQ_BASE_URL,
        timeout_seconds=timeout,
        max_output_tokens=max_output_tokens,
        temperature=temperature,
    )
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
    if actor.get('movement_remaining', 0) > 0 and not has_condition(actor, 'grappled'):
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


def _character_from_state(state: dict[str, Any], character_id: str | None = None) -> tuple[Character, dict[str, Any]]:
    raw = state.get('character')
    if character_id is not None:
        characters = state.get('characters')
        if isinstance(characters, dict):
            raw = characters.get(character_id)
        elif isinstance(raw, dict) and raw.get('id') != character_id:
            raw = None
    if raw is None:
        raise ValueError('character not found')
    return derive_character(raw)


def _character_for_combatant(state: dict[str, Any], combatant: dict[str, Any]) -> tuple[Character, dict[str, Any]]:
    raw = combatant.get('character')
    if raw is not None:
        return derive_character(raw)
    return _character_from_state(state, combatant.get('id'))


def resolve_create_character(body: ResolveRequest) -> dict[str, Any]:
    action = body.action
    if not isinstance(action, CreateCharacterAction):
        raise TypeError('resolve_create_character requires a validated action')
    character, derived = derive_character(action.character)
    body.state['character'] = character_to_state(character)
    return _combat_resolution(
        {'type': 'create_character', 'character_id': character.id},
        check={'derived': derived},
        rolls=[],
        outcome={'character_id': character.id, 'derived': derived},
        rules_used=[CHARACTER_RULE_ID, ABILITY_MODIFIER_RULE_ID, PROFICIENCY_BONUS_RULE_ID, UNARMORED_AC_RULE_ID],
    )


def resolve_skill_check(
    body: ResolveRequest,
    *,
    randbelow: Callable[[int], int] | None = None,
) -> dict[str, Any]:
    action = body.action
    if not isinstance(action, SkillCheckAction):
        raise TypeError('resolve_skill_check requires a validated action')
    if action.character_id is not None:
        _character, derived = _character_from_state(body.state, action.character_id)
        derived_modifier = derived['skill_modifiers'][action.skill]
    else:
        derived_modifier = action.modifier
    if derived_modifier is None:
        raise ValueError('skill modifier could not be derived')
    roll = roll_dice('d20', randbelow=randbelow)
    d20_result = roll['rolls'][0]
    total = d20_result + derived_modifier
    return {
        'schema_version': RULE_RESOLUTION_SCHEMA_VERSION,
        'resolution_id': str(uuid.uuid4()),
        'status': 'resolved',
        'action': {'type': action.type, 'skill': action.skill},
        'check': {
            'skill': action.skill,
            'ability': SKILL_TO_ABILITY[action.skill],
            'dc': action.dc,
            'modifier': derived_modifier,
        },
        'rolls': [{'type': 'd20', 'result': d20_result}],
        'outcome': {'total': total, 'success': total >= action.dc},
        'rules_used': [SKILL_CHECK_RULE_ID, ABILITY_MODIFIER_RULE_ID, PROFICIENCY_BONUS_RULE_ID],
    }


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
        character_state = None
        if spec.character is not None:
            character, derived = derive_character(spec.character)
            character_state = character_to_state(character)
            resolved_max_hp = derived['hp']['max']
            resolved_hp = derived['hp']['current']
            resolved_ac = derived['ac']['value']
            resolved_initiative_modifier = derived['initiative_modifier']
        else:
            resolved_max_hp = spec.max_hp
            resolved_hp = spec.hp
            resolved_ac = spec.ac
            resolved_initiative_modifier = spec.initiative_modifier
        if resolved_hp is None or resolved_max_hp is None or resolved_ac is None or resolved_initiative_modifier is None:
            raise ValueError('combatant requires a character or complete legacy combat statistics')
        if resolved_hp > resolved_max_hp:
            raise ValueError('hp cannot exceed max_hp')
        roll = roll_dice('d20', randbelow=randbelow)
        d20_result = roll['rolls'][0]
        initiative_total = d20_result + resolved_initiative_modifier
        initiative_rows.append((spec.id, initiative_total, resolved_initiative_modifier))
        rolls.append({
            'type': 'd20',
            'purpose': 'initiative',
            'actor_id': spec.id,
            'result': d20_result,
        })
        combatants[spec.id] = {
            'id': spec.id,
            'hp': resolved_hp,
            'max_hp': resolved_max_hp,
            'ac': resolved_ac,
            'initiative': initiative_total,
            'initiative_modifier': resolved_initiative_modifier,
            'position': spec.position,
            'movement_speed': spec.movement_speed,
            'unconscious': resolved_hp == 0,
            'side': spec.side,
            'conditions': [],
            'action_available': True,
            'bonus_action_available': True,
            'reaction_available': True,
            'movement_remaining': spec.movement_speed,
            'character': character_state,
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
    if action.distance > 0 and has_condition(actor, 'grappled'):
        raise ValueError('grappled actor cannot move')
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
    attack_roll_mode = "normal"

    if has_disadvantage(
        actor,
        roll_type="attack",
    ):
        attack_roll_mode = "disadvantage"
    target = combat['combatants'].get(action.target_id)
    if target is None:
        raise ValueError('target does not exist')
    if actor.get('unconscious'):
        raise ValueError('unconscious actor cannot attack')
    if target.get('unconscious'):
        raise ValueError('unconscious target is invalid')
    if not actor.get('action_available'):
        raise ValueError('action is already consumed')
    derived_rules: list[str] = []
    if action.weapon_id is not None:
        character, derived = _character_for_combatant(body.state, actor)
        weapon = character.weapon(action.weapon_id)
        attack_bonus = derived['ability_modifiers'][weapon.ability]
        if weapon.proficient:
            attack_bonus += derived['proficiency_bonus']
        damage = AttackDamage(dice=weapon.damage_dice, modifier=derived['ability_modifiers'][weapon.ability])
        derived_rules = [WEAPON_ATTACK_RULE_ID, WEAPON_DAMAGE_RULE_ID, ABILITY_MODIFIER_RULE_ID, PROFICIENCY_BONUS_RULE_ID]
    else:
        if action.attack_bonus is None or action.damage is None:
            raise ValueError('legacy combat attack requires attack_bonus and damage')
        attack_bonus = action.attack_bonus
        damage = action.damage
    standalone_action = AttackAction(
        type='attack',
        attack_bonus=attack_bonus,
        target_ac=target['ac'],
        damage=damage,
    )
    resolution = resolve_attack(
        ResolveRequest(action=standalone_action),
        randbelow=randbelow,
        roll_mode=attack_roll_mode,
    )
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
    if derived_rules:
        resolution['check']['weapon_id'] = action.weapon_id
        resolution['rules_used'] = derived_rules + resolution['rules_used']
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
    actor = combat["combatants"][action.actor_id]

    expired_conditions = advance_condition_durations(
        actor,
        timing="turn_end",
    )
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
            outcome={
                'combat_active': False,
                'combat_ended': True,
                'expired_conditions': [
                    condition["id"]
                    for condition in expired_conditions
                ],
            },
        )
    if wrapped:
        combat['round'] += 1
    combat['turn_index'] = next_index
    combat['current_actor_id'] = next_actor['id']
    next_actor['action_available'] = True
    next_actor['bonus_action_available'] = True
    next_actor['reaction_available'] = True
    next_actor['movement_remaining'] = (
        0
        if has_condition(next_actor, 'grappled')
        else next_actor['movement_speed']
    )
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
            'expired_conditions': [
                condition["id"]
                for condition in expired_conditions
            ],
        },
    )


def resolve_request(
    body: ResolveRequest,
    *,
    randbelow: Callable[[int], int] | None = None,
):
    if isinstance(body.action, CreateCharacterAction):
        return resolve_create_character(body)
    if isinstance(body.action, SkillCheckAction):
        return resolve_skill_check(body, randbelow=randbelow)
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

    modifier = action.modifier
    derived_rules: list[str] = []
    if action.character_id is not None:
        _character, derived = _character_from_state(body.state, action.character_id)
        modifier = derived['ability_modifiers'][action.ability]
        derived_rules = [ABILITY_MODIFIER_RULE_ID, CHARACTER_RULE_ID]
    if modifier is None:
        raise ValueError('ability check requires modifier or character_id')
    condition_creature = None

    if action.character_id is not None:
        combat = body.state.get("combat")

        if isinstance(combat, dict):
            combatants = combat.get("combatants")

            if isinstance(combatants, dict):
                candidate = combatants.get(action.character_id)

                if isinstance(candidate, dict):
                    condition_creature = candidate

    roll_mode = "normal"

    if condition_creature is not None:
        if has_disadvantage(
            condition_creature,
            roll_type="ability_check",
        ):
            roll_mode = "disadvantage"

    if roll_mode == "normal":
        roll = roll_dice('d20', randbelow=randbelow)
    else:
        roll = roll_dice(
            'd20',
            mode=roll_mode,
            randbelow=randbelow,
        )
    d20_result = roll['selected_roll'] if roll_mode != "normal" else roll['rolls'][0]
    total = d20_result + modifier
    roll_entry: dict[str, Any] = {'type': 'd20', 'result': d20_result}
    if roll_mode != "normal":
        roll_entry.update({'mode': roll_mode, 'rolls': roll['rolls']})
    return {
        'schema_version': RULE_RESOLUTION_SCHEMA_VERSION,
        'resolution_id': str(uuid.uuid4()),
        'status': 'resolved',
        'action': {'type': action.type, 'ability': action.ability},
        'check': {
            'ability': action.ability,
            'dc': action.dc,
            'modifier': modifier,
        },
        'rolls': [roll_entry],
        'outcome': {'total': total, 'success': total >= action.dc},
        'rules_used': derived_rules + [ABILITY_CHECK_RULE_ID] if derived_rules else [ABILITY_CHECK_RULE_ID],
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
    modifier = action.modifier
    derived_rules: list[str] = []
    if action.character_id is not None:
        _character, derived = _character_from_state(body.state, action.character_id)
        modifier = derived['saving_throw_modifiers'][action.ability]
        derived_rules = [SAVING_THROW_DERIVED_RULE_ID, ABILITY_MODIFIER_RULE_ID, PROFICIENCY_BONUS_RULE_ID]
    if modifier is None:
        raise ValueError('saving throw requires modifier or character_id')
    roll = roll_dice('d20', randbelow=randbelow)
    d20_result = roll['rolls'][0]
    total = d20_result + modifier
    return {
        'schema_version': RULE_RESOLUTION_SCHEMA_VERSION,
        'resolution_id': str(uuid.uuid4()),
        'status': 'resolved',
        'action': {'type': action.type, 'ability': action.ability},
        'check': {
            'ability': action.ability,
            'dc': action.dc,
            'modifier': modifier,
        },
        'rolls': [{'type': 'd20', 'result': d20_result}],
        'outcome': {'total': total, 'success': total >= action.dc},
        'rules_used': derived_rules + [SAVING_THROW_RULE_ID] if derived_rules else [SAVING_THROW_RULE_ID],
    }


def resolve_attack(
    body: ResolveRequest,
    *,
    randbelow: Callable[[int], int] | None = None,
    roll_mode: str = "normal",
):
    """Resolve an attack roll, including natural 20 and natural 1 classification."""
    action = body.action
    if not isinstance(action, AttackAction):
        raise TypeError('resolve_attack requires a validated attack action')
    if roll_mode not in {
        "normal",
        "advantage",
        "disadvantage",
    }:
        raise ValueError(f"unknown roll mode: {roll_mode}")

    if roll_mode == "normal":
        roll = roll_dice('d20', randbelow=randbelow)
    else:
        roll = roll_dice(
            'd20',
            mode=roll_mode,
            randbelow=randbelow,
        )
    d20_result = roll['selected_roll'] if roll_mode != "normal" else roll['rolls'][0]
    total = d20_result + action.attack_bonus
    critical = d20_result == 20
    natural_1 = d20_result == 1
    hit = True if critical else False if natural_1 else total >= action.target_ac
    damage_result = None
    attack_roll = {'type': 'd20', 'result': d20_result}
    if roll_mode != "normal":
        attack_roll.update({'mode': roll_mode, 'rolls': roll['rolls']})
    rolls = [attack_roll]
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
