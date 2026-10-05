from __future__ import annotations

import os
from pathlib import Path

import psycopg


MIGRATIONS_DIR = Path(__file__).resolve().parent.parent / 'migrations'


def apply_migrations(database_url: str | None = None) -> list[str]:
    url = (database_url if database_url is not None else os.getenv('DATABASE_URL', '')).strip()
    if not url:
        raise RuntimeError('DATABASE_URL is required to apply campaign migrations')

    applied: list[str] = []
    with psycopg.connect(url) as connection:
        connection.execute(
            '''CREATE TABLE IF NOT EXISTS schema_migrations (
                   version TEXT PRIMARY KEY,
                   applied_at TIMESTAMPTZ NOT NULL DEFAULT now()
               )'''
        )
        for path in sorted(MIGRATIONS_DIR.glob('*.sql')):
            version = path.name
            exists = connection.execute(
                'SELECT 1 FROM schema_migrations WHERE version = %s', (version,)
            ).fetchone()
            if exists:
                continue
            connection.execute(path.read_text(encoding='utf-8'))
            connection.execute(
                'INSERT INTO schema_migrations (version) VALUES (%s)', (version,)
            )
            applied.append(version)
    return applied
