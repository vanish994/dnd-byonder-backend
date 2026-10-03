import os
import re
import sqlite3
from pathlib import Path
from typing import Any
from fastapi import FastAPI, Header, HTTPException, Query
from pydantic import BaseModel, Field

DB_PATH = Path(os.getenv('RULES_DB_PATH', Path(__file__).resolve().parent.parent / 'dnd2024_knowledge_base' / 'knowledge_base' / 'dnd_rules.db'))
API_KEY = os.getenv('RULE_ENGINE_API_KEY', '').strip()
app = FastAPI(title='D&D 2024 Rule Knowledge API', version='0.1.0')

class SearchRequest(BaseModel):
    query: str = Field(min_length=2, max_length=500)
    limit: int = Field(default=8, ge=1, le=30)
    edition: str | None = None
    document: str | None = None

class ResolveRequest(BaseModel):
    action: str = Field(min_length=1, max_length=200)
    state: dict[str, Any] = Field(default_factory=dict)
    rule_ids: list[str] = Field(default_factory=list)


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
    terms = re.findall(r"[\wÀ-ÿ]+", text.lower())
    if not terms:
        raise HTTPException(status_code=400, detail='query has no searchable terms')
    return ' AND '.join('"' + t.replace('"', '') + '"' for t in terms[:16])


def serialize(row):
    return {
        'chunk_id': row['chunk_id'], 'title': row['title'], 'section': row['section'],
        'page': row['page'], 'line_start': row['line_start'], 'line_end': row['line_end'],
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
    return {'status': 'ok', 'edition_scope': ['2024', '2025', 'supplements_without_explicit_edition'], 'documents': documents, 'chunks': chunks}

@app.post('/v1/rules/search')
def search(body: SearchRequest, x_api_key: str | None = Header(default=None)):
    authorize(x_api_key)
    con = connect()
    try:
        sql = '''SELECT c.chunk_id, c.title, c.section, c.page, c.line_start, c.line_end, c.text
                 FROM chunks_fts f JOIN chunks c ON c.rowid=f.rowid
                 JOIN documents d ON d.doc_id=c.doc_id
                 WHERE chunks_fts MATCH ?'''
        args: list[Any] = [fts_query(body.query)]
        if body.edition:
            sql += ' AND d.edition = ?'; args.append(body.edition)
        if body.document:
            sql += ' AND c.title LIKE ?'; args.append('%' + body.document + '%')
        sql += ' LIMIT ?'; args.append(body.limit)
        rows = con.execute(sql, args).fetchall()
        return {'query': body.query, 'count': len(rows), 'results': [serialize(r) for r in rows], 'source_policy': 'evidence only; candidate rules require validation'}
    finally:
        con.close()

@app.get('/v1/rules/context')
def context(q: str = Query(min_length=2, max_length=500), limit: int = Query(default=8, ge=1, le=30), x_api_key: str | None = Header(default=None)):
    return search(SearchRequest(query=q, limit=limit), x_api_key)

@app.post('/v1/resolve')
def resolve(body: ResolveRequest, x_api_key: str | None = Header(default=None)):
    authorize(x_api_key)
    # Deliberately fail closed: the current corpus contains candidate rules, not a fully validated executable engine.
    return {
        'status': 'needs_rule_validation',
        'action': body.action,
        'facts_resolvidos': {},
        'reason': 'No deterministic resolution was emitted because the requested rule has not been bound to a validated mechanic.',
        'next_step': 'Search the rule knowledge base, validate the source, then add an explicit resolver/test before resolving this action.'
    }
