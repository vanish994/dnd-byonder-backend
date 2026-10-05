from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from game.migrations import apply_migrations


def main() -> int:
    try:
        applied = apply_migrations()
    except Exception as exc:  # pragma: no cover - CLI boundary
        print(f'Falha ao aplicar migrações C2 ({type(exc).__name__}); detalhes de conexão omitidos.', file=sys.stderr)
        return 1
    if applied:
        print(f'Migrações aplicadas: {", ".join(applied)}')
    else:
        print('Banco já está no schema atual.')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())
