from __future__ import annotations

import json
from copy import deepcopy
from typing import Any


NARRATIVE_CONTEXT_SCHEMA_VERSION = "narrative-context-v1"
MAX_RECENT_EVENTS = 8
MAX_RECENT_DIALOGUE = 10


def _json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def _record(value: Any) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _scene_context(state: dict[str, Any]) -> dict[str, Any]:
    scene = _record(state.get("scene"))
    context = _record(state.get("narrative_context"))
    result: dict[str, Any] = {
        "id": scene.get("id", context.get("scene_id")),
        "type": scene.get("type", context.get("scene_type")),
        "title": scene.get("title", context.get("location")),
        "description": scene.get("description"),
    }
    return {key: value for key, value in result.items() if value is not None}


def _combat_context(state: dict[str, Any]) -> dict[str, Any] | None:
    combat = _record(state.get("combat"))
    if not combat:
        return None
    combatants = []
    for combatant_id, raw_combatant in _record(combat.get("combatants")).items():
        combatant = _record(raw_combatant)
        character = _record(combatant.get("character"))
        combatants.append({
            "id": combatant_id,
            "name": character.get("name") or combatant.get("name") or combatant_id,
            "side": combatant.get("side"),
            "hp": combatant.get("hp"),
            "max_hp": combatant.get("max_hp"),
            "unconscious": combatant.get("unconscious", False),
            "conditions": [
                _record(condition).get("id")
                for condition in combatant.get("conditions", [])
                if _record(condition).get("id")
            ],
            "position": combatant.get("position"),
        })
    return {
        "active": combat.get("active"),
        "round": combat.get("round"),
        "current_actor_id": combat.get("current_actor_id"),
        "turn_order": deepcopy(combat.get("turn_order", [])),
        "combatants": combatants,
    }


def _character_context(state: dict[str, Any]) -> dict[str, Any]:
    character = _record(state.get("character"))
    character_class = _record(character.get("class"))
    fields = {
        "name": character.get("name"),
        "class": character_class.get("id"),
        "level": character_class.get("level", character.get("level")),
        "species": character.get("species_id"),
        "background": character.get("background_id"),
        "alignment": character.get("alignment_id"),
    }
    return {key: value for key, value in fields.items() if isinstance(value, (str, int))}


def _mechanical_summary(rule_resolution: dict[str, Any]) -> dict[str, Any]:
    if rule_resolution.get("status") != "resolved":
        return {
            "status": rule_resolution.get("status", "needs_rule_validation"),
            "reason": rule_resolution.get("reason"),
        }
    summary = {
        "status": "resolved",
        "action": deepcopy(rule_resolution.get("action", {})),
        "check": deepcopy(rule_resolution.get("check", {})),
        "outcome": deepcopy(rule_resolution.get("outcome", {})),
    }
    return {key: value for key, value in summary.items() if value not in ({}, None)}


def _narration_policy(rule_resolution: dict[str, Any]) -> str:
    if rule_resolution.get("status") == "resolved":
        return "resolved_consequence_only"
    return "acknowledgment_only"


def _narrative_context(state: dict[str, Any]) -> dict[str, Any]:
    existing = _record(state.get("narrative_context"))
    return {
        "schema_version": NARRATIVE_CONTEXT_SCHEMA_VERSION,
        "scene": _scene_context(state),
        "objective": deepcopy(existing.get("objective")),
        "known_threats": deepcopy(existing.get("known_threats", [])),
        "discoveries": deepcopy(existing.get("discoveries", [])),
        "relationships": deepcopy(existing.get("relationships", [])),
        "atmosphere": deepcopy(existing.get("atmosphere")),
        "moment": deepcopy(existing.get("moment")),
        "recent_events": deepcopy(existing.get("recent_events", []))[-MAX_RECENT_EVENTS:],
        "recent_dialogue": deepcopy(existing.get("recent_dialogue", []))[-MAX_RECENT_DIALOGUE:],
    }


def record_narrative_turn(
    state: dict[str, Any],
    *,
    player_input: str,
    rule_resolution: dict[str, Any],
    narration: str | None = None,
) -> None:
    """Persist only narrative memory; never make it a source of mechanical truth."""
    context = _narrative_context(state)
    action = _record(rule_resolution.get("action"))
    event = {
        "action_type": action.get("type") if action else None,
        "player_input": player_input,
        "mechanical": _mechanical_summary(rule_resolution),
    }
    context["recent_events"] = [*context["recent_events"], event][-MAX_RECENT_EVENTS:]
    dialogue = context["recent_dialogue"]
    if not dialogue or dialogue[-1] != {"speaker": "player", "text": player_input}:
        dialogue = [*dialogue, {"speaker": "player", "text": player_input}]
    if narration is not None:
        dialogue = [*dialogue, {"speaker": "mestre", "text": narration}]
    context["recent_dialogue"] = dialogue[-MAX_RECENT_DIALOGUE:]
    state["narrative_context"] = context


def record_narration(state: dict[str, Any], narration: str) -> None:
    """Append provider text without creating another mechanical event."""
    context = _narrative_context(state)
    context["recent_dialogue"] = [
        *context["recent_dialogue"],
        {"speaker": "mestre", "text": narration},
    ][-MAX_RECENT_DIALOGUE:]
    state["narrative_context"] = context


def build_narrative_context(
    *,
    state: dict[str, Any],
    player_input: str,
    rule_resolution: dict[str, Any],
    available_actions: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    """Build bounded, structured context that the narrator is allowed to see."""
    context = _narrative_context(state)
    context["character"] = _character_context(state)
    scene = _record(state.get("scene"))
    opening_seed = scene.get("opening_seed")
    if isinstance(opening_seed, str):
        context["opening_seed"] = opening_seed
    context["combat"] = _combat_context(state)
    context["current_input"] = player_input
    context["current_mechanics"] = _mechanical_summary(rule_resolution)
    context["narration_policy"] = _narration_policy(rule_resolution)
    if available_actions is not None:
        context["available_actions"] = deepcopy(available_actions)
    return context


def build_narrator_content(
    *,
    campaign_id: str | None = None,
    state: dict[str, Any],
    player_input: str,
    rule_resolution: dict[str, Any],
    available_actions: list[dict[str, Any]] | None = None,
) -> str:
    context = build_narrative_context(
        state=state,
        player_input=player_input,
        rule_resolution=rule_resolution,
        available_actions=available_actions,
    )
    campaign = f"<campaign_id>{_json(campaign_id)}</campaign_id>\n\n" if campaign_id is not None else ""
    facts = _mechanical_summary(rule_resolution)
    return (
        f"{campaign}"
        f"<CONTEXTO_NARRATIVO>{_json(context)}</CONTEXTO_NARRATIVO>\n\n"
        f"<FATOS_MECANICOS_AUTORIZADOS>{_json(facts)}</FATOS_MECANICOS_AUTORIZADOS>"
    )


def fallback_narration(rule_resolution: dict[str, Any], player_input: str) -> str:
    """Return truthful local narration when Gemini is unavailable."""
    if rule_resolution.get("status") != "resolved":
        return "A cena aguarda uma resolução mecânica antes de avançar."
    action = _record(rule_resolution.get("action"))
    outcome = _record(rule_resolution.get("outcome"))
    action_type = action.get("type")
    if action_type == "attack":
        if outcome.get("hit") is True:
            damage = outcome.get("damage")
            if isinstance(damage, int):
                return f"Seu ataque atinge o alvo e causa {damage} de dano."
            return "Seu ataque atinge o alvo."
        return "Seu ataque não encontra uma abertura."
    if action_type == "move" and isinstance(outcome.get("distance"), int):
        return f"Você se move {outcome['distance']} pés, mantendo a posição alcançada."
    if action_type in {"ability_check", "skill_check", "saving_throw"}:
        if outcome.get("success") is True:
            return "Sua tentativa é bem-sucedida."
        if outcome.get("success") is False:
            return "Sua tentativa não alcança o resultado necessário."
    if action_type == "end_turn":
        return "O turno termina, e a situação avança para o próximo momento."
    if action_type == "start_combat":
        return "O confronto começa. Os combatentes se posicionam para agir."
    return "A resolução mecânica foi concluída, e a cena avança."
