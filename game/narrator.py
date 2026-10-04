from __future__ import annotations

import json
from typing import Any


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def build_narrator_content(
    *,
    campaign_id: str | None = None,
    state: dict[str, Any],
    player_input: str,
    rule_resolution: dict[str, Any],
    available_actions: list[dict[str, Any]] | None = None,
) -> str:
    facts = rule_resolution if rule_resolution.get("status") == "resolved" else {}
    campaign = f"<campaign_id>{_json(campaign_id)}</campaign_id>\n\n" if campaign_id is not None else ""
    actions = (
        f"<available_actions>{_json(available_actions)}</available_actions>\n\n"
        if available_actions is not None
        else ""
    )
    return (
        f"{campaign}"
        f"<estado_da_campanha>{_json(state)}</estado_da_campanha>\n\n"
        f"<FATOS_RESOLVIDOS>{_json(facts)}</FATOS_RESOLVIDOS>\n\n"
        f"{actions}"
        f"<fala_do_jogador>{player_input}</fala_do_jogador>"
    )
