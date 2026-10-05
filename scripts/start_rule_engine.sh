#!/bin/sh
set -eu

python -c 'from game.migrations import apply_migrations; print({"migrations_applied": apply_migrations()})'
exec uvicorn rule_engine.app:app --host 0.0.0.0 --port "${PORT:-10000}"
