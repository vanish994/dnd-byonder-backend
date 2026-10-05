import os
import re
import sqlite3
import logging
import time
import uuid
from copy import deepcopy
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
    PHB2024CharacterCreationResponse,
    PHB2024GuidedCharacterRequest,
    PersistedGameTurnResponse,
    SessionResumeResponse,
    SessionTurnRequest,
)
from game.adventure_catalog import REDWOOD_ADVENTURE_ID, get_adventure, list_adventures
from game.orchestrator import (
    GameOrchestrator,
    InvalidGameAction,
    NarrationError,
    RuleResolutionError,
)
from game.persistence import (
    CampaignNotFound,
    CampaignStore,
    IdempotencyConflict,
    PersistenceNotConfigured,
    PersistenceUnavailable,
    RevisionConflict,
    SessionUnauthorized,
    SnapshotIntegrityError,
)
from rule_engine.classes import class_attack_count, class_features, class_resource_maximum, class_resource_recovery, class_resource_recovery_amount
from rule_engine.conditions import (
    advance_condition_durations,
    clear_conditions_for_rest,
    has_condition,
    has_disadvantage,
    remove_condition,
    sync_movement_with_conditions,
)
from rule_engine.dice import MAX_MODIFIER, DiceExpressionError, roll_dice
from rule_engine.equipment import add_item, equip_item, remove_item, unequip_item
from rule_engine.progression import level_up_available, next_level_experience
from rule_engine.resources import consume_resource, define_resource, recover_for_rest, recover_for_turn, recover_resource
from rule_engine.character import (
    SKILL_TO_ABILITY,
    Character,
    build_guided_character,
    character_options,
    character_to_state,
    derive_character,
)
from rule_engine.character_creation_catalog import character_options_phb2024
from rule_engine.character_creation_phb2024 import build_phb2024_character
from rule_engine.source_policy import STRICT_EDITION_SCOPE, append_strict_source_policy
from services.gemini_mj import GeminiMJClient
from services.narrator import UnavailableNarratorProvider


logger = logging.getLogger(__name__)

DB_PATH = Path(
    os.getenv(
        'RULES_DB_PATH',
        Path(__file__).resolve().parent.parent / 'dnd2024_knowledge_base' / 'knowledge_base' / 'dnd_rules.db',
    )
)
API_KEY = os.getenv('RULE_ENGINE_API_KEY', '').strip()
GEMINI_API_KEY = os.getenv('GEMINI_API_KEY', '').strip()
GEMINI_MODEL = os.getenv('GEMINI_MODEL', 'gemini-3.5-flash-lite').strip()
GEMINI_BASE_URL = os.getenv('GEMINI_BASE_URL', 'https://generativelanguage.googleapis.com/v1beta').strip()
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
REDWOOD_GROVE_SCENE_ID = 'redwood-grove-r3'
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
    dice: StrictStr = Field(min_length=2, max_length=20)
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
        if self.actor_id is not None:
            caller_supplied_fields = {'attack_bonus', 'target_ac', 'damage'}.intersection(self.model_fields_set)
            if caller_supplied_fields:
                raise ValueError('combat attack values must be derived by the Rule Engine')
        if self.attack_bonus is None and self.weapon_id is None and self.actor_id is None:
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


class RestAction(BaseModel):
    type: Literal['rest']
    rest_type: Literal['short_rest', 'long_rest']
    character_id: StrictStr | None = None

    class Config:
        extra = 'forbid'


class ResourceAction(BaseModel):
    type: Literal['define_resource', 'consume_resource', 'recover_resource']
    resource_id: StrictStr = Field(min_length=1, max_length=64)
    character_id: StrictStr | None = None
    amount: StrictInt | None = Field(default=None, ge=0)
    maximum: StrictInt | None = Field(default=None, ge=0)
    current: StrictInt | None = Field(default=None, ge=0)
    recovery: Literal['short_rest', 'long_rest', 'turn', 'never'] = 'never'
    recovery_amount: StrictInt | None = Field(default=None, gt=0)

    @model_validator(mode='after')
    def validate_operation(self):
        if self.type == 'define_resource' and self.maximum is None:
            raise ValueError('resource definition requires maximum')
        if self.type != 'define_resource' and self.maximum is not None:
            raise ValueError('maximum is only valid when defining a resource')
        if self.type != 'define_resource' and self.recovery_amount is not None:
            raise ValueError('recovery_amount is only valid when defining a resource')
        if self.type == 'consume_resource':
            if self.amount is None:
                self.amount = 1
            if self.amount <= 0:
                raise ValueError('resource consumption requires a positive amount')
        return self

    class Config:
        extra = 'forbid'


class ExperienceAction(BaseModel):
    type: Literal['add_experience']
    amount: StrictInt = Field(gt=0)
    character_id: StrictStr | None = None

    class Config:
        extra = 'forbid'


class LevelUpAction(BaseModel):
    type: Literal['level_up']
    character_id: StrictStr | None = None

    class Config:
        extra = 'forbid'


class SecondWindAction(BaseModel):
    type: Literal['second_wind']
    actor_id: StrictStr = Field(min_length=1, max_length=64)

    class Config:
        extra = 'forbid'


class ActionSurgeAction(BaseModel):
    type: Literal['action_surge']
    actor_id: StrictStr = Field(min_length=1, max_length=64)

    class Config:
        extra = 'forbid'


class InventoryAction(BaseModel):
    type: Literal['add_item', 'remove_item', 'equip_item', 'unequip_item']
    item_id: StrictStr | None = Field(default=None, min_length=1, max_length=64)
    character_id: StrictStr | None = None
    quantity: StrictInt = Field(default=1, ge=1)
    slot: Literal['weapon', 'armor'] | None = None
    item: dict[str, Any] | None = None

    @model_validator(mode='after')
    def validate_operation(self):
        if self.type in {'add_item', 'remove_item', 'equip_item'} and self.item_id is None:
            raise ValueError('item_id is required')
        if self.type == 'unequip_item' and self.item_id is None and self.slot is None:
            raise ValueError('unequip_item requires item_id or slot')
        if self.type != 'add_item' and self.item is not None:
            raise ValueError('item definition is only valid when adding an item')
        return self

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


class AdventureAction(BaseModel):
    type: Literal['adventure_action']
    intent: Literal['collect_bark_sample']
    tree_id: StrictStr = Field(min_length=1, max_length=32)

    class Config:
        extra = 'forbid'


class ResolveRequest(BaseModel):
    action: TextAction | CreateCharacterAction | SkillCheckAction | AdventureAction | AbilityCheckAction | SavingThrowAction | AttackAction | StartCombatAction | MoveAction | EndTurnAction | RestAction | ResourceAction | ExperienceAction | LevelUpAction | SecondWindAction | ActionSurgeAction | InventoryAction
    state: dict[str, Any] = Field(default_factory=dict)
    rule_ids: list[str] = Field(default_factory=list)


class DiceRollRequest(BaseModel):
    expression: str = Field(min_length=1, max_length=50)
    mode: Literal['normal', 'advantage', 'disadvantage'] = 'normal'


def authorize(x_api_key: str | None):
    if API_KEY and x_api_key != API_KEY:
        raise HTTPException(status_code=401, detail='invalid api key')


def get_campaign_store() -> CampaignStore:
    return CampaignStore(os.getenv('DATABASE_URL', ''))


def raise_campaign_http_error(exc: Exception) -> None:
    if isinstance(exc, CampaignNotFound):
        raise HTTPException(status_code=404, detail='session not found') from exc
    if isinstance(exc, SessionUnauthorized):
        raise HTTPException(status_code=401, detail='invalid session credential') from exc
    if isinstance(exc, RevisionConflict):
        raise HTTPException(
            status_code=409,
            detail={'code': 'STALE_REVISION', 'current_revision': exc.current_revision},
        ) from exc
    if isinstance(exc, IdempotencyConflict):
        raise HTTPException(status_code=409, detail='idempotency key conflict') from exc
    if isinstance(exc, (PersistenceNotConfigured, PersistenceUnavailable)):
        raise HTTPException(status_code=503, detail='campaign persistence is unavailable') from exc
    if isinstance(exc, SnapshotIntegrityError):
        logger.error('CAMPAIGN_SNAPSHOT_INTEGRITY_ERROR')
        raise HTTPException(status_code=500, detail='campaign state integrity check failed') from exc
    raise exc


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
        'source_policy': 'explicit D&D 2024/PHB 2024 only; canonical source preferred for duplicate titles',
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
            'source_policy': 'explicit D&D 2024/PHB 2024 only; canonical source preferred for duplicate titles; candidates require validation',
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
            'label': 'Perceber o ambiente',
            'description': 'Preste atenção a um detalhe, som ou mudança que chame sua atenção.',
            'player_input': 'Observo atentamente o que se destaca ao meu redor.',
        },
        {
            'type': 'narrative_intent',
            'intent': 'investigate_clue',
            'label': 'Investigar uma pista',
            'description': 'Explore a pista, presença ou anomalia que mais chamou sua atenção.',
            'player_input': 'Investigo a pista que mais chamou minha atenção.',
        },
        {
            'type': 'narrative_intent',
            'intent': 'move_stealthily',
            'label': 'Seguir furtivamente',
            'description': 'Avance pela rota escolhida tentando não ser percebido.',
            'player_input': 'Sigo pela rota escolhida furtivamente.',
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
        'title': 'Abertura',
        'opening_seed': uuid.uuid4().hex,
        'available_actions': _initial_scene_actions(character_id),
    }


def _redwood_grove_scene(character_id: str) -> dict[str, Any]:
    """Return the first server-owned Redwood Grove snapshot."""
    return {
        'id': REDWOOD_GROVE_SCENE_ID,
        'type': 'exploration',
        'title': 'Redwood Grove',
        'available_actions': [
            {
                'type': 'adventure_action',
                'intent': 'collect_bark_sample',
                'tree_id': 'r3',
                'label': 'Coletar amostra da árvore',
                'description': 'Colete uma amostra da casca da árvore disponível.',
                'player_input': 'Coleto uma amostra da casca desta árvore.',
            },
            {
                'type': 'skill_check',
                'skill': 'persuasion',
                'dc': 12,
                'character_id': character_id,
                'label': 'Convencer Kaynen',
                'description': 'Tente convencer Kaynen a permitir a coleta.',
                'player_input': 'Tento convencer Kaynen a permitir a coleta.',
            },
            {
                'type': 'skill_check',
                'skill': 'perception',
                'dc': 14,
                'character_id': character_id,
                'label': 'Examinar a entrada da toca',
                'description': 'Procure pegadas próximas à entrada da toca sem presumir o que elas revelam.',
                'player_input': 'Procuro pegadas próximas à entrada da toca.',
            },
        ],
    }


def _redwood_watch_scene(character_id: str) -> dict[str, Any]:
    """Return the compact, server-owned entry snapshot for the adventure."""
    return {
        'id': 'redwood-watch',
        'type': 'exploration',
        'title': 'Redwood Watch',
        'available_actions': [
            {
                'type': 'skill_check',
                'skill': 'persuasion',
                'dc': 12,
                'character_id': character_id,
                'label': 'Convencer Kaynen',
                'description': 'Tente convencer Kaynen a permitir a investigação.',
                'player_input': 'Tento convencer Kaynen a permitir a investigação.',
            },
        ],
    }


def _adventure_state(adventure: dict[str, Any], scene: dict[str, Any]) -> dict[str, Any]:
    return {
        'id': adventure['id'],
        'title': adventure['title'],
        'source': adventure['source'],
        'objective': 'Investigar a corrupção e os desaparecimentos.',
        'scene_id': scene['id'],
        'known_facts': ['A investigação começa na Redwood Watch.'],
        'discoveries': [],
        'npcs': ['kaynen'],
        'threats': [],
        'mechanical_context': {'ruleset': 'dnd-2024-phb'},
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


@app.get('/v2/character/options')
def get_phb2024_character_options(x_api_key: str | None = Header(default=None)):
    authorize(x_api_key)
    return character_options_phb2024()


@app.get('/v2/adventures')
def get_adventure_catalog(x_api_key: str | None = Header(default=None)):
    authorize(x_api_key)
    return list_adventures()


def _phb2024_character_preview(body: PHB2024GuidedCharacterRequest) -> tuple[dict[str, Any], dict[str, Any]]:
    try:
        character = build_phb2024_character(body)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    resolution_body = ResolveRequest(action=CreateCharacterAction(type='create_character', character=character))
    resolution = resolve_request(resolution_body)
    return {
        'schema_version': 'character-creation-phb2024-v1',
        'ruleset': 'dnd-2024-phb',
        'valid': True,
        'character': resolution_body.state['character'],
        'derived': resolution['outcome']['derived'],
        'rule_resolution': resolution,
    }, resolution_body.state


@app.post('/v2/character/validate')
def validate_phb2024_character(body: PHB2024GuidedCharacterRequest, x_api_key: str | None = Header(default=None)):
    authorize(x_api_key)
    try:
        get_adventure(getattr(body, 'adventure_id', REDWOOD_ADVENTURE_ID))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail='adventure is not available in the server-owned catalog') from exc
    preview, _state = _phb2024_character_preview(body)
    return preview


@app.post('/v2/character/create', response_model=PHB2024CharacterCreationResponse)
def create_phb2024_character(
    body: PHB2024GuidedCharacterRequest,
    x_api_key: str | None = Header(default=None),
    x_session_token: str = Header(alias='X-Session-Token'),
    idempotency_key: str = Header(alias='Idempotency-Key'),
):
    authorize(x_api_key)
    try:
        adventure = get_adventure(getattr(body, 'adventure_id', REDWOOD_ADVENTURE_ID))
    except ValueError as exc:
        raise HTTPException(status_code=422, detail='adventure is not available in the server-owned catalog') from exc
    preview, state = _phb2024_character_preview(body)
    character_id = preview['character']['id']
    if adventure['id'] != REDWOOD_ADVENTURE_ID:
        raise HTTPException(status_code=422, detail='adventure bootstrap is not implemented')
    scene = _redwood_watch_scene(character_id)
    state['scene'] = scene
    state['adventure'] = _adventure_state(adventure, scene)
    state['encounter'] = _initial_encounter()
    try:
        return get_campaign_store().create_campaign(
            state=state,
            creation_response=preview,
            request_payload=body.model_dump(mode='json'),
            idempotency_key=idempotency_key,
            session_token=x_session_token,
            ruleset='dnd-2024-phb',
        )
    except (
        IdempotencyConflict,
        PersistenceNotConfigured,
        PersistenceUnavailable,
        SessionUnauthorized,
    ) as exc:
        raise_campaign_http_error(exc)


def resolve_adventure_action(body: ResolveRequest) -> dict[str, Any]:
    """Resolve a bounded, non-dice adventure transition from server state."""
    action = body.action
    if not isinstance(action, AdventureAction):
        raise TypeError('resolve_adventure_action requires an adventure action')
    scene = body.state.get('scene')
    if not isinstance(scene, dict) or scene.get('id') != REDWOOD_GROVE_SCENE_ID:
        raise ValueError('adventure action is unavailable in this scene')

    authorized = [
        candidate for candidate in scene.get('available_actions', [])
        if isinstance(candidate, dict)
        and candidate.get('type') == 'adventure_action'
        and candidate.get('intent') == action.intent
        and candidate.get('tree_id') == action.tree_id
    ]
    if len(authorized) != 1:
        raise ValueError('adventure action is not authorized by the current snapshot')

    adventure = body.state.setdefault('adventure', {})
    samples = adventure.setdefault('redwood_samples', [])
    if action.tree_id in samples:
        raise ValueError('redwood sample was already collected')
    samples.append(action.tree_id)
    scene['available_actions'] = [
        candidate for candidate in scene.get('available_actions', [])
        if not (
            isinstance(candidate, dict)
            and candidate.get('type') == 'adventure_action'
            and candidate.get('intent') == action.intent
            and candidate.get('tree_id') == action.tree_id
        )
    ]
    return {
        'schema_version': RULE_RESOLUTION_SCHEMA_VERSION,
        'status': 'resolved',
        'action': {
            'type': action.type,
            'intent': action.intent,
            'tree_id': action.tree_id,
        },
        'check': {},
        'rolls': [],
        'outcome': {
            'state_changed': True,
            'sample_collected': True,
            'narrative_facts': [
                {'id': 'redwood-bark-sample-collected', 'tree_id': action.tree_id},
            ],
        },
        'rules_used': ['dragon-delves.redwood-grove.adventure-action.v1'],
    }


def resolve_game_action(action: dict[str, Any], state: dict[str, Any]) -> dict[str, Any]:
    """Validate and resolve a structured action using the existing Rule Engine."""
    mechanical_action = {
        key: value for key, value in action.items()
        if key not in ACTION_PRESENTATION_FIELDS
    }
    if mechanical_action.get('type') in {'skill_check', 'ability_check'} and 'dc' not in mechanical_action:
        raise ValueError('resolution requires a server-authorized DC')
    if mechanical_action.get('type') == 'attack':
        actor_id = mechanical_action.get('actor_id')
        target_id = mechanical_action.get('target_id')
        if (
            not isinstance(actor_id, str)
            or not actor_id.strip()
            or not isinstance(target_id, str)
            or not target_id.strip()
        ):
            raise ValueError('game attacks require a combat actor_id and target_id')
    if mechanical_action.get('type') == 'narrative_intent':
        if mechanical_action.get('intent') not in {'investigate_clue', 'move_stealthily'}:
            raise ValueError('unknown narrative intent')
        scene = state.get('scene')
        if not isinstance(scene, dict) or scene.get('id') != INITIAL_SCENE_ID:
            raise ValueError('narrative intent is unavailable in this scene')
        if mechanical_action.get('intent') == 'move_stealthily':
            scene['available_actions'] = [{
                'type': 'skill_check',
                'skill': 'stealth',
                'dc': 10,
                'character_id': state.get('character', {}).get('id'),
                'label': 'Fazer teste de Furtividade',
                'description': 'Avançar sem ser percebido exige um teste de Furtividade.',
                'player_input': 'Faço um teste de Furtividade para avançar sem ser percebido.',
            }]
        else:
            scene['available_actions'] = [{
                'type': 'skill_check',
                'skill': 'perception',
                'dc': 10,
                'character_id': state.get('character', {}).get('id'),
                'label': 'Fazer teste de Percepção',
                'description': 'A pista que você escolheu investigar exige atenção. Faça um teste de Percepção.',
                'player_input': 'Faço um teste de Percepção para investigar a pista.',
            }]
        return {
            'schema_version': RULE_RESOLUTION_SCHEMA_VERSION,
            'status': 'needs_rule_validation',
            'reason': 'Narrative intent acknowledged; the next mechanical check is presented separately.',
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
    scene = state.get('scene')
    combat = state.get('combat')
    adventure = state.get('adventure')
    if (
        mechanical_action.get('type') == 'skill_check'
        and mechanical_action.get('skill') == 'persuasion'
        and resolution.get('outcome', {}).get('success') is True
        and isinstance(adventure, dict)
        and adventure.get('id') == REDWOOD_ADVENTURE_ID
        and adventure.get('scene_id') == 'redwood-watch'
        and isinstance(state.get('character'), dict)
    ):
        next_scene = _redwood_grove_scene(state['character']['id'])
        state['scene'] = next_scene
        adventure['scene_id'] = next_scene['id']
        resolution.setdefault('outcome', {})['scene_transition'] = {
            'from': 'redwood-watch',
            'to': REDWOOD_GROVE_SCENE_ID,
        }
        scene = next_scene
    if (
        mechanical_action.get('type') == 'skill_check'
        and mechanical_action.get('skill') == 'perception'
        and resolution.get('outcome', {}).get('success') is True
        and isinstance(scene, dict)
        and scene.get('id') == INITIAL_SCENE_ID
        and not (isinstance(combat, dict) and combat.get('active'))
    ):
        scene['available_actions'] = [{
            'type': 'start_combat',
            'encounter_id': INITIAL_ENCOUNTER_ID,
            'label': 'Avançar para o confronto',
            'description': 'Você já investigou. Escolha conscientemente se quer enfrentar a ameaça; ou descreva outra ação livremente.',
            'player_input': 'Avanço para o confronto.',
        }]
    if (
        mechanical_action.get('type') == 'skill_check'
        and mechanical_action.get('skill') == 'stealth'
        and resolution.get('outcome', {}).get('success') is True
        and isinstance(scene, dict)
        and scene.get('id') == INITIAL_SCENE_ID
        and not (isinstance(combat, dict) and combat.get('active'))
    ):
        scene['available_actions'] = [{
            'type': 'narrative_intent',
            'intent': 'investigate_clue',
            'label': 'Investigar uma pista',
            'description': 'Explore a pista, presença ou anomalia que mais chamou sua atenção.',
            'player_input': 'Investigo a pista que mais chamou minha atenção.',
        }]
    if (
        mechanical_action.get('type') == 'skill_check'
        and mechanical_action.get('skill') == 'perception'
        and isinstance(scene, dict)
        and scene.get('id') == REDWOOD_GROVE_SCENE_ID
    ):
        outcome = resolution.setdefault('outcome', {})
        if outcome.get('success') is True:
            fact = {'id': 'redwood-grove-armin-tracks', 'location': 'redwood-grove-r4'}
            adventure = state.setdefault('adventure', {})
            discoveries = adventure.setdefault('discoveries', [])
            if fact not in discoveries:
                discoveries.append(fact)
            outcome['narrative_facts'] = [fact]
        else:
            outcome['narrative_facts'] = []
    return resolution


def _validate_snapshot_action(action: dict[str, Any] | None, state: dict[str, Any]) -> None:
    if action is None:
        return

    combat = state.get('combat')
    if isinstance(combat, dict) and combat.get('active') and isinstance(combat.get('available_actions'), list):
        authorized_actions = combat['available_actions']
    else:
        scene = state.get('scene')
        authorized_actions = scene.get('available_actions', []) if isinstance(scene, dict) else []

    requested = {key: value for key, value in action.items() if key not in ACTION_PRESENTATION_FIELDS}
    for authorized in authorized_actions:
        if not isinstance(authorized, dict):
            continue
        canonical = {key: value for key, value in authorized.items() if key not in ACTION_PRESENTATION_FIELDS}
        if requested == canonical:
            return
        # Movement distance is a player choice; the Rule Engine still validates
        # its type, remaining speed, and resulting position against canonical state.
        if canonical.get('type') == 'move' and set(requested) == set(canonical) | {'distance'}:
            candidate = {key: value for key, value in requested.items() if key != 'distance'}
            distance = requested.get('distance')
            if candidate == canonical and isinstance(distance, int) and not isinstance(distance, bool) and distance >= 0:
                return
    raise InvalidGameAction('turn action is not authorized by the current server snapshot')


def build_game_orchestrator() -> GameOrchestrator:
    if not GEMINI_API_KEY or not GEMINI_MODEL or not GEMINI_BASE_URL:
        logger.warning("GEMINI_CONFIGURATION_MISSING fallback=local")
        return GameOrchestrator(UnavailableNarratorProvider(), resolve_action=resolve_game_action)
    try:
        timeout = float(os.getenv('GEMINI_TIMEOUT_SECONDS', '20'))
        max_output_tokens = int(os.getenv('GEMINI_MAX_OUTPUT_TOKENS', '512'))
        temperature = float(os.getenv('GEMINI_TEMPERATURE', '0.7'))
        gemini = GeminiMJClient(
            api_key=GEMINI_API_KEY,
            model=GEMINI_MODEL,
            base_url=GEMINI_BASE_URL,
            timeout_seconds=timeout,
            max_output_tokens=max_output_tokens,
            temperature=temperature,
        )
    except ValueError as exc:
        logger.warning("GEMINI_CONFIGURATION_INVALID error_type=%s fallback=local", type(exc).__name__)
        return GameOrchestrator(UnavailableNarratorProvider(), resolve_action=resolve_game_action)
    return GameOrchestrator(gemini, resolve_action=resolve_game_action, interpret_intent=gemini.interpret)


@app.get('/v1/sessions/{session_id}', response_model=SessionResumeResponse)
def resume_session(
    session_id: uuid.UUID,
    x_api_key: str | None = Header(default=None),
    x_session_token: str = Header(alias='X-Session-Token'),
):
    authorize(x_api_key)
    try:
        stored = get_campaign_store().load_session(session_id=session_id, session_token=x_session_token)
    except (
        CampaignNotFound,
        PersistenceNotConfigured,
        PersistenceUnavailable,
        SessionUnauthorized,
        SnapshotIntegrityError,
    ) as exc:
        raise_campaign_http_error(exc)

    state = stored['state']
    character = state.get('character')
    if not isinstance(character, dict):
        raise HTTPException(status_code=500, detail='campaign character is missing')
    try:
        _character, derived = derive_character(character)
    except (TypeError, ValueError, ValidationError) as exc:
        logger.error('CAMPAIGN_CHARACTER_INTEGRITY_ERROR')
        raise HTTPException(status_code=500, detail='campaign character integrity check failed') from exc

    combat = state.get('combat')
    scene = state.get('scene')
    available_actions = []
    if isinstance(combat, dict) and combat.get('active') and isinstance(combat.get('available_actions'), list):
        available_actions = combat['available_actions']
    elif isinstance(scene, dict) and isinstance(scene.get('available_actions'), list):
        available_actions = scene['available_actions']
    context = state.get('narrative_context')
    dialogue = context.get('recent_dialogue', []) if isinstance(context, dict) else []
    history = [
        {
            'id': f"{stored['session_id']}-{index}",
            'speaker': 'mestre' if item.get('speaker') == 'mestre' else 'voce',
            'text': item['text'],
            'timestamp': index,
        }
        for index, item in enumerate(dialogue)
        if isinstance(item, dict) and isinstance(item.get('text'), str)
        and item.get('speaker') in {'player', 'mestre'}
    ] if isinstance(dialogue, list) else []
    return {
        **{key: stored[key] for key in ('campaign_id', 'session_id', 'ruleset', 'revision')},
        'character': character,
        'derived': derived,
        'state': state,
        'available_actions': available_actions,
        'history': history,
    }


@app.post('/v1/game/turn', response_model=PersistedGameTurnResponse)
def game_turn(
    body: SessionTurnRequest,
    x_api_key: str | None = Header(default=None),
    x_request_id: str | None = Header(default=None),
    x_session_token: str = Header(alias='X-Session-Token'),
    idempotency_key: str = Header(alias='Idempotency-Key'),
):
    authorize(x_api_key)
    started = time.perf_counter()
    request_id = x_request_id if isinstance(x_request_id, str) else None
    logger.info(
        "GAME_TURN_STARTED action=%s session_id=%s request_id=%s",
        (body.action or {}).get('type', 'none'),
        body.session_id,
        request_id or 'none',
    )

    def execute_turn(state: dict[str, Any], campaign_id: str):
        _validate_snapshot_action(body.action, state)
        internal_request = GameTurnRequest(
            campaign_id=campaign_id,
            state=state,
            player_input=body.player_input,
            action=body.action,
            available_actions=[],
        )
        return build_game_orchestrator().turn(internal_request, request_id=request_id)

    try:
        response = get_campaign_store().commit_turn(
            session_id=body.session_id,
            session_token=x_session_token,
            expected_revision=body.expected_revision,
            idempotency_key=idempotency_key,
            request_payload={
                'expected_revision': body.expected_revision,
                'player_input': body.player_input,
                'action': body.action,
            },
            execute_turn=execute_turn,
        )
        resolution = response['rule_resolution']
        logger.info(
            "GAME_TURN_COMPLETED resolution_status=%s revision=%s duration_ms=%.1f request_id=%s",
            resolution.get('status', 'unknown'),
            response['revision'],
            (time.perf_counter() - started) * 1000,
            request_id or 'none',
        )
        return response
    except (
        CampaignNotFound,
        IdempotencyConflict,
        PersistenceNotConfigured,
        PersistenceUnavailable,
        RevisionConflict,
        SessionUnauthorized,
        SnapshotIntegrityError,
    ) as exc:
        raise_campaign_http_error(exc)
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


def _validate_movement_state(actor: dict[str, Any]) -> None:
    movement_speed = actor.get('movement_speed')
    movement_remaining = actor.get('movement_remaining')
    position = actor.get('position')

    if (
        not isinstance(movement_speed, int)
        or isinstance(movement_speed, bool)
        or movement_speed < 0
    ):
        raise ValueError('invalid movement_speed')

    if (
        not isinstance(movement_remaining, int)
        or isinstance(movement_remaining, bool)
        or movement_remaining < 0
    ):
        raise ValueError('invalid movement_remaining')

    if movement_remaining > movement_speed:
        raise ValueError('movement_remaining exceeds movement_speed')

    if (
        not isinstance(position, int)
        or isinstance(position, bool)
        or position < 0
    ):
        raise ValueError('invalid position')


def _validate_initiative_state(combat: dict[str, Any]) -> None:
    combatants = combat.get('combatants')
    if not isinstance(combatants, dict) or not combatants:
        raise ValueError('combatants cannot be empty')

    for actor_id, actor in combatants.items():
        if not isinstance(actor, dict):
            raise ValueError(f'invalid combatant: {actor_id}')
        hp = actor.get('hp')
        max_hp = actor.get('max_hp')
        if (
            not isinstance(hp, int)
            or isinstance(hp, bool)
            or not isinstance(max_hp, int)
            or isinstance(max_hp, bool)
            or hp < 0
            or max_hp <= 0
            or hp > max_hp
        ):
            raise ValueError('invalid combatant hp')
        if actor.get('unconscious') != (hp == 0):
            raise ValueError('unconscious state is inconsistent with hp')

    order = combat.get('turn_order')
    if not isinstance(order, list) or not order:
        raise ValueError('initiative order cannot be empty')
    if any(not isinstance(actor_id, str) for actor_id in order):
        raise ValueError('initiative order contains invalid actor')
    if len(order) != len(set(order)):
        raise ValueError('initiative order contains duplicate actor')
    if any(actor_id not in combatants for actor_id in order):
        raise ValueError('initiative order contains unknown actor')
    if set(order) != set(combatants):
        raise ValueError('initiative order does not contain every combatant')

    current_actor_id = combat.get('current_actor_id')
    if current_actor_id not in combatants:
        raise ValueError('current actor does not exist')
    if current_actor_id not in order:
        raise ValueError('current actor is outside initiative order')

    turn_index = combat.get('turn_index')
    if (
        not isinstance(turn_index, int)
        or isinstance(turn_index, bool)
        or turn_index < 0
        or turn_index >= len(order)
        or order[turn_index] != current_actor_id
    ):
        raise ValueError('turn index is inconsistent with current actor')


def _start_turn(combat: dict[str, Any], actor: dict[str, Any]) -> None:
    actor['action_available'] = True
    actor['action_uses_remaining'] = 1
    actor['action_surge_used_this_turn'] = False
    actor['bonus_action_available'] = True
    actor['reaction_available'] = True
    actor.pop('_movement_remaining_before_condition_block', None)
    actor['movement_remaining'] = actor['movement_speed']
    sync_movement_with_conditions(actor)
    if 'resources' in actor:
        recover_for_turn(actor)
    if isinstance(actor.get('character'), dict) and 'resources' in actor['character']:
        recover_for_turn(actor['character'])


def _determine_next_actor(
    combat: dict[str, Any],
) -> tuple[dict[str, Any] | None, int, bool]:
    order = combat['turn_order']
    previous_index = combat['turn_index']
    next_index = previous_index
    wrapped = False
    for _ in range(len(order)):
        next_index += 1
        if next_index >= len(order):
            next_index = 0
            wrapped = True
        candidate = combat['combatants'][order[next_index]]
        if not candidate.get('unconscious'):
            return candidate, next_index, wrapped
    return None, previous_index, wrapped


def _combat_available_actions(combat: dict[str, Any]) -> list[dict[str, str]]:
    if not combat.get('active'):
        return []
    _validate_initiative_state(combat)
    actor = combat.get('combatants', {}).get(combat.get('current_actor_id'))
    if not actor or actor.get('unconscious'):
        if actor:
            return [{'type': 'end_turn', 'actor_id': actor['id'], 'label': 'Encerrar turno'}]
        return []
    if actor.get('side') != 'player':
        return [{
            'type': 'end_turn',
            'actor_id': actor['id'],
            'label': 'Aguardar o próximo turno',
            'description': 'O Rule Engine está no turno de outro combatente.',
            'player_input': 'Aguardo o próximo turno.',
        }]
    _validate_movement_state(actor)
    sync_movement_with_conditions(actor)
    _validate_movement_state(actor)
    actions: list[dict[str, str]] = []
    movement_available = actor.get('movement_remaining', 0) > 0
    if has_condition(actor, 'prone'):
        stand_cost = actor.get('movement_speed', 0) // 2
        movement_available = actor.get('movement_remaining', 0) >= stand_cost
    if movement_available and not (
        has_condition(actor, 'grappled') or has_condition(actor, 'restrained')
    ):
        actions.append({
            'type': 'move',
            'actor_id': actor['id'],
            'label': 'Mover',
            'description': 'Escolha a distância, até o movimento restante.',
        })
    target_id = next(
        (
            candidate_id
            for candidate_id, candidate in combat.get('combatants', {}).items()
            if candidate_id != actor['id'] and candidate.get('side') != 'player' and not candidate.get('unconscious')
        ),
        None,
    )
    if target_id is not None and actor.get('action_uses_remaining', 1 if actor.get('action_available') else 0) > 0:
        actions.append({
            'type': 'attack',
            'actor_id': actor['id'],
            'target_id': target_id,
            'label': 'Atacar',
            'description': 'Ataque o inimigo atual com a arma equipada.',
            'player_input': 'Ataco o inimigo atual.',
        })
    character = actor.get('character')
    second_wind = character.get('resources', {}).get('second_wind') if isinstance(character, dict) else None
    if actor.get('bonus_action_available') and isinstance(second_wind, dict) and second_wind.get('current', 0) > 0:
        actions.append({'type': 'second_wind', 'actor_id': actor['id'], 'label': 'Segundo Fôlego'})
    action_surge = character.get('resources', {}).get('action_surge') if isinstance(character, dict) else None
    if (
        isinstance(action_surge, dict)
        and action_surge.get('current', 0) > 0
        and not actor.get('action_surge_used_this_turn', False)
    ):
        actions.append({'type': 'action_surge', 'actor_id': actor['id'], 'label': 'Surto de Ação'})
    actions.append({'type': 'end_turn', 'actor_id': actor['id'], 'label': 'Encerrar turno'})
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
    _validate_initiative_state(combat)
    return combat


def _require_current_actor(combat: dict[str, Any], actor_id: str) -> dict[str, Any]:
    if actor_id != combat.get('current_actor_id'):
        raise ValueError('actor is not the current actor')
    actor = combat['combatants'].get(actor_id)
    if not isinstance(actor, dict):
        raise ValueError('actor does not exist')
    return actor


def _require_bonus_action(combat: dict[str, Any], actor_id: str) -> dict[str, Any]:
    if not combat.get('active'):
        raise ValueError('combat is not active')
    actor = _require_current_actor(combat, actor_id)
    if actor.get('unconscious'):
        raise ValueError('unconscious actor cannot use bonus action')
    if not actor.get('bonus_action_available'):
        raise ValueError('BLOCKED_ACTION')
    return actor


def _consume_bonus_action(actor: dict[str, Any]) -> None:
    actor['bonus_action_available'] = False


def _require_reaction(combat: dict[str, Any], actor_id: str) -> dict[str, Any]:
    if not combat.get('active'):
        raise ValueError('combat is not active')
    actor = _require_current_actor(combat, actor_id)
    if actor.get('unconscious'):
        raise ValueError('unconscious actor cannot use reaction')
    if not actor.get('reaction_available'):
        raise ValueError('BLOCKED_ACTION')
    return actor


def _consume_reaction(actor: dict[str, Any]) -> None:
    actor['reaction_available'] = False


def _finish_combat_if_needed(combat: dict[str, Any]) -> None:
    combatants = combat['combatants']
    sides = {item.get('side', 'neutral') for item in combatants.values()}
    living_sides = {
        item.get('side', 'neutral')
        for item in combatants.values()
        if not item.get('unconscious')
    }
    if not living_sides:
        combat['active'] = False
        combat['winner_side'] = None
        combat['available_actions'] = []
        return
    defeated = [
        side
        for side in sides
        if not any(
            item.get('side') == side and not item.get('unconscious')
            for item in combatants.values()
        )
    ]
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


def _mutable_character_state(state: dict[str, Any], character_id: str | None = None) -> dict[str, Any]:
    raw = state.get('character')
    if character_id is not None:
        characters = state.get('characters')
        if isinstance(characters, dict):
            raw = characters.get(character_id)
        elif isinstance(raw, dict) and raw.get('id') != character_id:
            raw = None
    if not isinstance(raw, dict):
        raise ValueError('character not found')
    return raw


def _character_resolution(action: dict[str, Any], outcome: dict[str, Any]) -> dict[str, Any]:
    return _combat_resolution(
        action,
        check={},
        rolls=[],
        outcome=outcome,
        rules_used=['character_state.v1'],
    )


def resolve_rest(body: ResolveRequest) -> dict[str, Any]:
    action = body.action
    if not isinstance(action, RestAction):
        raise TypeError('resolve_rest requires a validated action')
    if body.state.get('combat', {}).get('active'):
        raise ValueError('cannot rest during active combat')
    character = _mutable_character_state(body.state, action.character_id)
    removed = clear_conditions_for_rest(character, rest_type=action.rest_type)
    recovered = recover_for_rest(character, action.rest_type)
    hp_before = character.get('current_hp')
    if action.rest_type == 'long_rest':
        _validated_character, derived = derive_character(character)
        character['current_hp'] = derived['hp']['max']
        for field in ('action_available', 'bonus_action_available', 'reaction_available'):
            if field in character:
                character[field] = True
    return _character_resolution(
        {'type': 'rest', 'rest_type': action.rest_type},
        {
            'rest_type': action.rest_type,
            'conditions_removed': [item['id'] for item in removed],
            'resources_recovered': recovered,
            'hp_before': hp_before,
            'hp_after': character.get('current_hp'),
        },
    )


def resolve_add_experience(body: ResolveRequest) -> dict[str, Any]:
    action = body.action
    if not isinstance(action, ExperienceAction):
        raise TypeError('resolve_add_experience requires a validated action')
    character_state = _mutable_character_state(body.state, action.character_id)
    character = Character.model_validate(character_state)
    character_state['experience_points'] = character.experience_points + action.amount
    updated = Character.model_validate(character_state)
    threshold = next_level_experience(updated.level)
    return _character_resolution(
        {'type': 'add_experience', 'character_id': updated.id},
        {
            'character_id': updated.id,
            'experience_points': updated.experience_points,
            'level': updated.level,
            'level_up_available': level_up_available(updated.level, updated.experience_points),
            'next_level_experience': threshold,
        },
    )


def _sync_class_resources(character_state: dict[str, Any], character: Character) -> None:
    resources = character_state.setdefault('resources', {})
    if not isinstance(resources, dict):
        raise ValueError('resources must be an object')
    resource_ids = ['second_wind']
    if character.level >= 2:
        resource_ids.append('action_surge')
    for resource_id in resource_ids:
        maximum = class_resource_maximum(character.class_.id, resource_id, character.level)
        recovery = class_resource_recovery(character.class_.id, resource_id)
        recovery_amount = class_resource_recovery_amount(character.class_.id, resource_id)
        existing = resources.get(resource_id)
        if existing is None:
            current = maximum
        else:
            if not isinstance(existing, dict):
                raise ValueError('class resource must be an object')
            old_maximum = existing.get('maximum')
            old_current = existing.get('current')
            if not isinstance(old_maximum, int) or not isinstance(old_current, int):
                raise ValueError('class resource has invalid counters')
            current = maximum if old_current == old_maximum else min(old_current, maximum)
        resources[resource_id] = {
            'id': resource_id,
            'current': current,
            'maximum': maximum,
            'recovery': recovery,
            **({'recovery_amount': recovery_amount} if recovery_amount is not None else {}),
        }


def resolve_level_up(body: ResolveRequest) -> dict[str, Any]:
    action = body.action
    if not isinstance(action, LevelUpAction):
        raise TypeError('resolve_level_up requires a validated action')
    character_state = _mutable_character_state(body.state, action.character_id)
    character = Character.model_validate(character_state)
    if not level_up_available(character.level, character.experience_points):
        raise ValueError('level up is not available')
    old_level = character.level
    old_derived = character.derived()
    old_current_hp = character.current_hp
    old_max_hp = old_derived['hp']['max']
    new_level = old_level + 1
    character_state['level'] = new_level
    character_state['class'] = {**character_state['class'], 'level': new_level}
    character_state['class_features'] = class_features(character.class_.id, new_level)
    new_preview = Character.model_validate(character_state)
    hp_gain = new_preview.derived()['hp']['max'] - old_max_hp
    if old_current_hp is not None and old_current_hp == old_max_hp:
        character_state['current_hp'] = old_current_hp + hp_gain
    _sync_class_resources(character_state, new_preview)
    updated = Character.model_validate(character_state)
    return _character_resolution(
        {'type': 'level_up', 'character_id': updated.id},
        {
            'character_id': updated.id,
            'previous_level': old_level,
            'level': updated.level,
            'experience_points': updated.experience_points,
            'hp_gain': hp_gain,
            'derived': updated.derived(),
            'class_features': list(updated.class_features),
            'resources': updated.resources,
        },
    )


def resolve_resource(body: ResolveRequest) -> dict[str, Any]:
    action = body.action
    if not isinstance(action, ResourceAction):
        raise TypeError('resolve_resource requires a validated action')
    character = _mutable_character_state(body.state, action.character_id)
    if action.type == 'define_resource':
        if action.resource_id == 'second_wind':
            raise ValueError('class resources are server-owned')
        resource = define_resource(
            character,
            action.resource_id,
            maximum=action.maximum,
            current=action.current,
            recovery=action.recovery,
            recovery_amount=action.recovery_amount,
        )
    elif action.type == 'consume_resource':
        resource = consume_resource(character, action.resource_id, action.amount)
    else:
        resource = recover_resource(character, action.resource_id, action.amount)
    return _character_resolution(
        {'type': action.type, 'resource_id': action.resource_id},
        {'resource': dict(resource)},
    )


def resolve_inventory(body: ResolveRequest) -> dict[str, Any]:
    action = body.action
    if not isinstance(action, InventoryAction):
        raise TypeError('resolve_inventory requires a validated action')
    if body.state.get('combat', {}).get('active'):
        raise ValueError('cannot change equipment during active combat')
    character = _mutable_character_state(body.state, action.character_id)
    if action.type == 'add_item':
        result = add_item(character, action.item_id, action.quantity, item=action.item)
    elif action.type == 'remove_item':
        result = remove_item(character, action.item_id, action.quantity)
    elif action.type == 'equip_item':
        result = equip_item(character, action.item_id, action.slot)
    else:
        result = unequip_item(character, action.slot, action.item_id)
    return _character_resolution(
        {'type': action.type, **({'item_id': action.item_id} if action.item_id else {})},
        {'result': result, 'inventory': character.get('inventory', {}), 'equipped': character.get('equipped', {})},
    )


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
    condition_creature = None
    if action.character_id is not None:
        combat = body.state.get('combat')
        if isinstance(combat, dict):
            combatants = combat.get('combatants')
            if isinstance(combatants, dict):
                candidate = combatants.get(action.character_id)
                if isinstance(candidate, dict):
                    condition_creature = candidate
    roll_mode = 'normal'
    if condition_creature is not None and has_disadvantage(
        condition_creature,
        roll_type='ability_check',
    ):
        roll_mode = 'disadvantage'
    if roll_mode == 'normal':
        roll = roll_dice('d20', randbelow=randbelow)
    else:
        roll = roll_dice('d20', mode=roll_mode, randbelow=randbelow)
    d20_result = roll['selected_roll'] if roll_mode != 'normal' else roll['rolls'][0]
    total = d20_result + derived_modifier
    roll_entry: dict[str, Any] = {'type': 'd20', 'result': d20_result}
    if roll_mode != 'normal':
        roll_entry.update({'mode': roll_mode, 'rolls': roll['rolls']})
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
        'rolls': [roll_entry],
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
    if not action.combatants:
        raise ValueError('combatants cannot be empty')
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
        if resolved_hp < 0 or resolved_hp > resolved_max_hp:
            raise ValueError('hp must be between zero and max_hp')
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
            'action_uses_remaining': 1,
            'action_surge_used_this_turn': False,
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
    _finish_combat_if_needed(combat)
    if combat['active']:
        for index, actor_id in enumerate(order):
            if not combat['combatants'][actor_id].get('unconscious'):
                combat['turn_index'] = index
                combat['current_actor_id'] = actor_id
                _start_turn(combat, combat['combatants'][actor_id])
                break
    combat['available_actions'] = _combat_available_actions(combat)
    body.state['combat'] = combat
    return _combat_resolution(
        {'type': 'start_combat'},
        check={'combatant_ids': order},
        rolls=rolls,
        outcome={
            'combat_started': combat['active'],
            'round': 1,
            'turn_index': combat['turn_index'],
            'current_actor_id': combat['current_actor_id'],
            'turn_order': order,
            'lifecycle_events': (
                ['round_start', 'turn_start']
                if combat['active']
                else []
            ),
        },
        rules_used=[INITIATIVE_RULE_ID, COMBAT_RULE_ID],
    )


def resolve_move(body: ResolveRequest) -> dict[str, Any]:
    action = body.action
    if not isinstance(action, MoveAction):
        raise TypeError('resolve_move requires a validated action')
    combat = _require_combat(body.state)
    actor = _require_current_actor(combat, action.actor_id)
    _validate_movement_state(actor)
    sync_movement_with_conditions(actor)
    _validate_movement_state(actor)
    if actor.get('unconscious'):
        raise ValueError('unconscious actor cannot move')
    if action.distance > 0 and (
        has_condition(actor, 'grappled') or has_condition(actor, 'restrained')
    ):
        raise ValueError('conditioned actor cannot move')
    prone = has_condition(actor, 'prone')
    stand_cost = 0
    if prone:
        stand_cost = actor.get('movement_speed', 0) // 2
        if actor.get('movement_remaining', 0) < stand_cost:
            raise ValueError('not enough movement to stand from prone')
        if action.distance > actor.get('movement_remaining', 0) - stand_cost:
            raise ValueError('movement exceeds remaining movement after standing')
    if action.distance > actor.get('movement_remaining', 0):
        raise ValueError('movement exceeds remaining movement')
    if prone:
        remove_condition(actor, condition_id='prone')
        actor['movement_remaining'] -= stand_cost
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


def resolve_second_wind(
    body: ResolveRequest,
    *,
    randbelow: Callable[[int], int] | None = None,
) -> dict[str, Any]:
    action = body.action
    if not isinstance(action, SecondWindAction):
        raise TypeError('resolve_second_wind requires a validated action')
    combat = _require_combat(body.state)
    actor = _require_bonus_action(combat, action.actor_id)
    character_state = actor.get('character')
    if not isinstance(character_state, dict):
        raise ValueError('second wind requires a character-backed combatant')
    character = Character.model_validate(character_state)
    resource = character.resources.get('second_wind')
    if not isinstance(resource, dict):
        raise ValueError('second wind resource is not available')
    consumed = consume_resource(character_state, 'second_wind')
    roll = roll_dice('1d10', randbelow=randbelow)
    healing = int(roll['total']) + character.level
    hp_before = actor['hp']
    actor['hp'] = min(actor['max_hp'], actor['hp'] + healing)
    actor['bonus_action_available'] = False
    character_state['current_hp'] = actor['hp']
    combat['available_actions'] = _combat_available_actions(combat)
    return _combat_resolution(
        {'type': 'second_wind', 'actor_id': action.actor_id},
        check={'resource': consumed['id'], 'level': character.level},
        rolls=[roll],
        outcome={
            'healing': actor['hp'] - hp_before,
            'hp_before': hp_before,
            'hp_after': actor['hp'],
            'resource_current': consumed['current'],
        },
        rules_used=['fighter.second_wind.v2024'],
    )


def resolve_action_surge(body: ResolveRequest) -> dict[str, Any]:
    action = body.action
    if not isinstance(action, ActionSurgeAction):
        raise TypeError('resolve_action_surge requires a validated action')
    combat = _require_combat(body.state)
    actor = _require_current_actor(combat, action.actor_id)
    if actor.get('unconscious'):
        raise ValueError('unconscious actor cannot use action surge')
    character_state = actor.get('character')
    if not isinstance(character_state, dict):
        raise ValueError('action surge requires a character-backed combatant')
    character = Character.model_validate(character_state)
    if character.class_.id != 'fighter' or character.level < 2:
        raise ValueError('action surge is not available')
    if actor.get('action_surge_used_this_turn', False):
        raise ValueError('action surge already used this turn')
    consumed = consume_resource(character_state, 'action_surge')
    action_uses = actor.get('action_uses_remaining', 1 if actor.get('action_available') else 0)
    actor['action_uses_remaining'] = action_uses + 1
    actor['action_available'] = True
    actor['action_surge_used_this_turn'] = True
    combat['available_actions'] = _combat_available_actions(combat)
    return _combat_resolution(
        {'type': 'action_surge', 'actor_id': action.actor_id},
        check={'resource': consumed['id']},
        rolls=[],
        outcome={
            'action_uses_remaining': actor['action_uses_remaining'],
            'resource_current': consumed['current'],
        },
        rules_used=['fighter.action_surge.v2024'],
    )


def _resolve_single_combat_attack(
    body: ResolveRequest,
    action: AttackAction,
    combat: dict[str, Any],
    actor: dict[str, Any],
    target: dict[str, Any],
    *,
    randbelow: Callable[[int], int] | None = None,
) -> dict[str, Any]:
    if target.get('unconscious'):
        raise ValueError('unconscious target is invalid')
    attacker_disadvantage = has_disadvantage(actor, roll_type='attack')
    target_advantage = has_condition(target, 'restrained')
    if target_advantage and not attacker_disadvantage:
        attack_roll_mode = 'advantage'
    elif attacker_disadvantage and not target_advantage:
        attack_roll_mode = 'disadvantage'
    else:
        attack_roll_mode = 'normal'
    derived_rules: list[str] = []
    caller_supplied_fields = {'attack_bonus', 'target_ac', 'damage'}.intersection(action.model_fields_set)
    if caller_supplied_fields:
        raise ValueError('combat attack values must be derived by the Rule Engine')
    character, derived = _character_for_combatant(body.state, actor)
    requested_weapon_id = action.weapon_id or character.equipped.get('weapon')
    if requested_weapon_id is None:
        raise ValueError('combat attack requires an equipped weapon')
    raw_character = actor.get('character') or body.state.get('character') or {}
    if 'equipped' in raw_character and raw_character.get('equipped', {}).get('weapon') != requested_weapon_id:
        raise ValueError('weapon is not equipped')
    weapon = character.weapon(requested_weapon_id)
    attack_bonus = derived['ability_modifiers'][weapon.ability]
    if weapon.proficient:
        attack_bonus += derived['proficiency_bonus']
    damage = AttackDamage(dice=weapon.damage_dice, modifier=derived['ability_modifiers'][weapon.ability])
    derived_rules = [WEAPON_ATTACK_RULE_ID, WEAPON_DAMAGE_RULE_ID, ABILITY_MODIFIER_RULE_ID, PROFICIENCY_BONUS_RULE_ID]
    resolved_weapon_id = requested_weapon_id
    standalone_action = AttackAction(type='attack', attack_bonus=attack_bonus, target_ac=target['ac'], damage=damage)
    resolution = resolve_attack(
        ResolveRequest(action=standalone_action), randbelow=randbelow, roll_mode=attack_roll_mode,
    )
    hp_before = target['hp']
    damage_result = resolution['outcome']['damage']
    if resolution['outcome']['hit'] and damage_result is not None:
        target['hp'] = max(0, target['hp'] - damage_result)
        target['unconscious'] = target['hp'] == 0
    resolution['action'] = {'type': 'attack', 'actor_id': action.actor_id, 'target_id': action.target_id}
    resolution['check']['target_ac'] = target['ac']
    if derived_rules:
        resolution['check']['weapon_id'] = resolved_weapon_id
        resolution['rules_used'] = derived_rules + resolution['rules_used']
    resolution['outcome'].update({
        'target_hp_before': hp_before,
        'target_hp_after': target['hp'],
        'target_unconscious': target['unconscious'],
    })
    return resolution


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
    action_uses = actor.get('action_uses_remaining', 1 if actor.get('action_available') else 0)
    if action_uses <= 0:
        raise ValueError('action is already consumed')
    character = actor.get('character')
    attack_count = 1
    if isinstance(character, dict):
        validated_character = Character.model_validate(character)
        attack_count = class_attack_count(validated_character.class_.id, validated_character.level)
    resolutions: list[dict[str, Any]] = []
    for _ in range(attack_count):
        if target.get('unconscious'):
            break
        resolutions.append(_resolve_single_combat_attack(body, action, combat, actor, target, randbelow=randbelow))
    actor['action_uses_remaining'] = max(0, action_uses - 1)
    actor['action_available'] = actor['action_uses_remaining'] > 0
    _finish_combat_if_needed(combat)
    combat['available_actions'] = _combat_available_actions(combat)
    if len(resolutions) == 1:
        resolution = resolutions[0]
        resolution['outcome']['combat_active'] = combat['active']
        return resolution
    total_damage = sum(item['outcome']['damage'] or 0 for item in resolutions)
    first = resolutions[0]
    first['action'] = {'type': 'attack', 'actor_id': action.actor_id, 'target_id': action.target_id}
    first['rolls'] = [roll for item in resolutions for roll in item['rolls']]
    first['rules_used'] = list(dict.fromkeys(rule for item in resolutions for rule in item['rules_used']))
    nested_resolutions = deepcopy(resolutions)
    first['outcome'].update({
        'attacks': nested_resolutions,
        'attack_count': len(resolutions),
        'damage': total_damage,
        'hit': any(item['outcome']['hit'] for item in resolutions),
        'critical': any(item['outcome']['critical'] for item in resolutions),
        'combat_active': combat['active'],
    })
    return first


def resolve_end_turn(body: ResolveRequest) -> dict[str, Any]:
    action = body.action
    if not isinstance(action, EndTurnAction):
        raise TypeError('resolve_end_turn requires a validated action')
    combat = _require_combat(body.state)
    _require_current_actor(combat, action.actor_id)
    actor = combat["combatants"][action.actor_id]

    _finish_combat_if_needed(combat)
    if not combat['active']:
        combat['available_actions'] = []
        return _combat_resolution(
            {'type': 'end_turn', 'actor_id': action.actor_id},
            check={}, rolls=[],
            outcome={
                'combat_active': False,
                'combat_ended': True,
                'lifecycle_events': ['turn_end'],
                'expired_conditions': [],
            },
        )

    expired_conditions = advance_condition_durations(
        actor,
        timing="turn_end",
        current_round=combat["round"],
        current_turn_index=combat["turn_index"],
    )
    next_actor, next_index, wrapped = _determine_next_actor(combat)
    if next_actor is None:
        combat['active'] = False
        combat['available_actions'] = []
        return _combat_resolution(
            {'type': 'end_turn', 'actor_id': action.actor_id},
            check={}, rolls=[],
            outcome={
                'combat_active': False,
                'combat_ended': True,
                'lifecycle_events': ['turn_end'],
                'expired_conditions': [
                    condition["id"]
                    for condition in expired_conditions
                ],
            },
        )
    if wrapped:
        round_end_expired = []
        for combatant in combat["combatants"].values():
            round_end_expired.extend(
                advance_condition_durations(
                    combatant,
                    timing="round_end",
                    current_round=combat["round"],
                    current_turn_index=combat["turn_index"],
                )
            )
        expired_conditions.extend(round_end_expired)
        combat['round'] += 1
        for combatant in combat["combatants"].values():
            expired_conditions.extend(
                advance_condition_durations(
                    combatant,
                    timing="round_start",
                    current_round=combat["round"],
                    current_turn_index=0,
                )
            )
    combat['turn_index'] = next_index
    combat['current_actor_id'] = next_actor['id']
    _start_turn(combat, next_actor)
    expired_conditions.extend(
        advance_condition_durations(
            next_actor,
            timing="turn_start",
            current_round=combat["round"],
            current_turn_index=next_index,
        )
    )
    combat['available_actions'] = _combat_available_actions(combat)
    lifecycle_events = ['turn_end']
    if wrapped:
        lifecycle_events.extend(['round_end', 'round_start'])
    lifecycle_events.append('turn_start')
    return _combat_resolution(
        {'type': 'end_turn', 'actor_id': action.actor_id},
        check={}, rolls=[],
        outcome={
            'combat_active': combat['active'],
            'combat_ended': False,
            'round': combat['round'],
            'turn_index': next_index,
            'current_actor_id': next_actor['id'],
            'lifecycle_events': lifecycle_events,
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
    original_state = deepcopy(body.state)
    try:
        if isinstance(body.action, CreateCharacterAction):
            return resolve_create_character(body)
        if isinstance(body.action, SkillCheckAction):
            return resolve_skill_check(body, randbelow=randbelow)
        if isinstance(body.action, AdventureAction):
            return resolve_adventure_action(body)
        if isinstance(body.action, StartCombatAction):
            return resolve_start_combat(body, randbelow=randbelow)
        if isinstance(body.action, MoveAction):
            return resolve_move(body)
        if isinstance(body.action, EndTurnAction):
            return resolve_end_turn(body)
        if isinstance(body.action, RestAction):
            return resolve_rest(body)
        if isinstance(body.action, ResourceAction):
            return resolve_resource(body)
        if isinstance(body.action, ExperienceAction):
            return resolve_add_experience(body)
        if isinstance(body.action, LevelUpAction):
            return resolve_level_up(body)
        if isinstance(body.action, SecondWindAction):
            return resolve_second_wind(body, randbelow=randbelow)
        if isinstance(body.action, ActionSurgeAction):
            return resolve_action_surge(body)
        if isinstance(body.action, InventoryAction):
            return resolve_inventory(body)
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
    except Exception:
        _restore_state_in_place(body.state, original_state)
        raise


def _restore_state_in_place(state: dict[str, Any], snapshot: dict[str, Any]) -> None:
    def restore(current: Any, saved: Any) -> None:
        if isinstance(current, dict) and isinstance(saved, dict):
            for key in list(current):
                if key not in saved:
                    del current[key]
            for key, value in saved.items():
                if key in current and isinstance(current[key], (dict, list)) and isinstance(value, type(current[key])):
                    restore(current[key], value)
                else:
                    current[key] = deepcopy(value)
        elif isinstance(current, list) and isinstance(saved, list):
            current[:] = [deepcopy(value) for value in saved]

    restore(state, snapshot)


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
    roll_mode = 'normal'
    if action.ability == 'dexterity' and action.character_id:
        combat = body.state.get('combat', {})
        combatants = combat.get('combatants', {}) if isinstance(combat, dict) else {}
        combatant = combatants.get(action.character_id) if isinstance(combatants, dict) else None
        if isinstance(combatant, dict) and has_condition(combatant, 'restrained'):
            roll_mode = 'disadvantage'
    if roll_mode == 'normal':
        roll = roll_dice('d20', randbelow=randbelow)
    else:
        roll = roll_dice('d20', randbelow=randbelow, mode=roll_mode)
    d20_result = roll['selected_roll'] if roll_mode != 'normal' else roll['rolls'][0]
    roll_entry = {'type': 'd20', 'result': d20_result}
    if roll_mode != 'normal':
        roll_entry.update({'mode': roll_mode, 'rolls': roll['rolls']})
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
        'rolls': [roll_entry],
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
        damage_rolls = [roll_dice(action.damage.dice, randbelow=randbelow)]
        if critical:
            damage_rolls.append(roll_dice(action.damage.dice, randbelow=randbelow))
        dice_results = [result for damage_roll in damage_rolls for result in damage_roll['rolls']]
        damage_result = max(0, sum(dice_results) + action.damage.modifier)
        rolls.append({
            'type': action.damage.dice,
            'results': dice_results,
            'critical': critical,
        })
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
