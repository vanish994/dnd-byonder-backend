"""Infraestrutura server-owned para concentração e salvaguardas complexas PHB 2024."""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

from rule_engine.dice import roll_dice

CONCENTRATION_RULE_ID = "spell.concentration.phb2024.v1"
COMPLEX_SAVE_RULE_ID = "saving_throw.complex.phb2024.v1"
REACTION_RULE_ID = "combat.reaction.phb2024.v1"


def concentration_dc(damage: int) -> int:
    if not isinstance(damage, int) or isinstance(damage, bool) or damage < 0:
        raise ValueError("concentration damage must be a non-negative integer")
    return min(30, max(10, damage // 2))


def _roll_d20(
    *,
    mode: str = "normal",
    randbelow: Callable[[int], int] | None = None,
) -> dict[str, Any]:
    if mode not in {"normal", "advantage", "disadvantage"}:
        raise ValueError("invalid saving throw roll mode")
    return roll_dice("d20", mode=mode, randbelow=randbelow)


def resolve_concentration_check(
    character: dict[str, Any],
    *,
    damage: int,
    constitution_modifier: int,
    proficient: bool = False,
    advantage: bool = False,
    replace_low_roll_with_ten: bool = False,
    randbelow: Callable[[int], int] | None = None,
) -> dict[str, Any]:
    spellcasting = character.get("spellcasting")
    if not isinstance(spellcasting, dict) or not spellcasting.get("concentration_spell_id"):
        return {
            "checked": False,
            "concentration_broken": False,
            "reason": "no_active_concentration",
            "rules_used": [CONCENTRATION_RULE_ID],
        }
    dc = concentration_dc(damage)
    mode = "advantage" if advantage else "normal"
    roll = _roll_d20(mode=mode, randbelow=randbelow)
    selected = roll["selected_roll"] if mode != "normal" else roll["rolls"][0]
    if replace_low_roll_with_ten and selected < 10:
        selected = 10
    total = selected + constitution_modifier + (2 if proficient else 0)
    success = total >= dc
    result = {
        "checked": True,
        "spell_id": spellcasting["concentration_spell_id"],
        "dc": dc,
        "modifier": constitution_modifier,
        "proficient": proficient,
        "total": total,
        "success": success,
        "concentration_broken": not success,
        "rules_used": [CONCENTRATION_RULE_ID],
        "roll": {"type": "d20", "result": selected},
    }
    if mode != "normal":
        result["roll"]["mode"] = mode
        result["roll"]["rolls"] = roll["rolls"]
    if not success:
        spellcasting["concentration_spell_id"] = None
        active_effects = character.get("active_effects")
        if isinstance(active_effects, list):
            character["active_effects"] = [
                effect for effect in active_effects
                if effect.get("source_id") != result["spell_id"] and effect.get("id") != result["spell_id"]
            ]
    return result


def resolve_complex_saving_throw(
    *,
    modifier: int,
    dc: int,
    ability: str,
    damage_dice: str | None = None,
    damage_modifier: int = 0,
    half_on_success: bool = True,
    condition_on_failure: str | None = None,
    condition_duration: dict[str, Any] | None = None,
    advantage: bool = False,
    disadvantage: bool = False,
    randbelow: Callable[[int], int] | None = None,
) -> dict[str, Any]:
    if advantage and disadvantage:
        mode = "normal"
    elif advantage:
        mode = "advantage"
    elif disadvantage:
        mode = "disadvantage"
    else:
        mode = "normal"
    roll = _roll_d20(mode=mode, randbelow=randbelow)
    selected = roll["selected_roll"] if mode != "normal" else roll["rolls"][0]
    total = selected + modifier
    success = total >= dc
    damage = None
    damage_roll = None
    if damage_dice is not None:
        damage_roll = roll_dice(damage_dice, randbelow=randbelow)
        damage = max(0, int(damage_roll["total"]) + damage_modifier)
        if success and half_on_success:
            damage //= 2
    result: dict[str, Any] = {
        "ability": ability,
        "dc": dc,
        "modifier": modifier,
        "total": total,
        "success": success,
        "damage": damage,
        "condition": None if success else condition_on_failure,
        "rules_used": [COMPLEX_SAVE_RULE_ID],
        "rolls": [{"type": "d20", "result": selected}],
    }
    if mode != "normal":
        result["rolls"][0].update({"mode": mode, "rolls": roll["rolls"]})
    if damage_roll is not None:
        result["rolls"].append({"type": damage_dice, "results": damage_roll["rolls"]})
    if not success and condition_on_failure is not None:
        result["condition_duration"] = condition_duration or {"kind": "until_end_of_turn"}
    return result


def open_reaction_window(
    combat: dict[str, Any],
    *,
    trigger: str,
    triggering_actor_id: str,
    target_actor_id: str,
    payload: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Create a pending reaction; it is not resolved by narrative text."""
    windows = combat.setdefault("reaction_windows", [])
    if not isinstance(windows, list):
        raise ValueError("reaction_windows must be a list")
    window = {
        "id": f"reaction-{len(windows) + 1}",
        "trigger": trigger,
        "triggering_actor_id": triggering_actor_id,
        "target_actor_id": target_actor_id,
        "payload": dict(payload or {}),
        "status": "pending",
    }
    windows.append(window)
    return window


def consume_reaction_window(combat: dict[str, Any], window_id: str, actor_id: str) -> dict[str, Any]:
    windows = combat.get("reaction_windows")
    if not isinstance(windows, list):
        raise ValueError("reaction window does not exist")
    for window in windows:
        if window.get("id") == window_id:
            if window.get("status") != "pending":
                raise ValueError("reaction window is no longer pending")
            if window.get("target_actor_id") != actor_id:
                raise ValueError("actor cannot use this reaction window")
            window["status"] = "consumed"
            return window
    raise ValueError("reaction window does not exist")
