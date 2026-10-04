from __future__ import annotations

import json
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_narrator_content(
    *,
    state: dict[str, Any],
    player_input: str,
    rule_resolution: dict[str, Any],
) -> str:
    facts = rule_resolution if rule_resolution.get("status") == "resolved" else {}
    return (
        f"<estado_da_campanha>{_json(state)}</estado_da_campanha>\n\n"
        f"<FATOS_RESOLVIDOS>{_json(facts)}</FATOS_RESOLVIDOS>\n\n"
        f"<fala_do_jogador>{player_input}</fala_do_jogador>"
    )
