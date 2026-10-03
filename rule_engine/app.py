import os
import re
import sqlite3
import uuid
from collections.abc import Callable
from pathlib import Path
from typing import Any, Literal

from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field, StrictInt, constr

from rule_engine.dice import MAX_MODIFIER, DiceExpressionError, roll_dice
from rule_engine.source_policy import STRICT_EDITION_SCOPE, append_strict_source_policy

DB_PATH = Path(
    os.getenv(
        'RULES_DB_PATH',
        Path(__file__).resolve().parent.parent / 'dnd2024_knowledge_base' / 'knowledge_base' / 'dnd_rules.db',
    )
)
API_KEY = os.getenv('RULE_ENGINE_API_KEY', '').strip()
app = FastAPI(title='D&D 2024 Rule Knowledge API', version='0.1.0')

ABILITY_CHECK_RULE_ID = 'ability_check.mvp.v1'
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


class ResolveRequest(BaseModel):
    action: TextAction | AbilityCheckAction
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


def resolve_request(
    body: ResolveRequest,
    *,
    randbelow: Callable[[int], int] | None = None,
):
    if isinstance(body.action, AbilityCheckAction):
        return resolve_explicit_action(body, randbelow=randbelow)

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
