from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from game.contracts import GameTurnRequest, GameTurnResponse
from game.narrator import fallback_narration, record_narration, record_narrative_turn
from services.narrator import NarratorError, NarratorProvider


class InvalidGameAction(ValueError):
    """Raised when the structured action cannot be validated by the Rule Engine."""


class RuleResolutionError(RuntimeError):
    """Raised when the Rule Engine does not return the canonical contract."""


class NarrationError(RuntimeError):
    """Retained for compatibility with callers that classify narration failures."""

    def __init__(self, message: str, rule_resolution: dict[str, Any]) -> None:
        super().__init__(message)
        self.rule_resolution = rule_resolution


def _bind_gate_intent(
    gate_resolution: dict[str, Any],
    available_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Bind an LLM intent to exactly one server-authorized action."""
    matches: list[dict[str, Any]] = []
    intent_type = gate_resolution.get("type")
    for candidate in available_actions:
        if not isinstance(candidate, dict) or candidate.get("type") != intent_type:
            continue
        if intent_type == "skill_check" and candidate.get("skill") == gate_resolution.get("skill"):
            matches.append(candidate)
        elif intent_type == "ability_check" and candidate.get("ability") == gate_resolution.get("ability"):
            matches.append(candidate)
    if len(matches) != 1:
        raise InvalidGameAction("Resolution Gate intent is not authorized by current snapshot")
    return deepcopy(matches[0])


def _bind_adventure_intent(
    adventure_intent: dict[str, Any],
    available_actions: list[dict[str, Any]],
) -> dict[str, Any]:
    """Bind a state-changing adventure intent to one canonical action."""
    matches = [
        candidate for candidate in available_actions
        if isinstance(candidate, dict)
        and candidate.get("type") == "adventure_action"
        and candidate.get("intent") == adventure_intent.get("intent")
    ]
    if len(matches) != 1:
        raise RuleResolutionError("Adventure intent is not authorized by current snapshot")
    return deepcopy(matches[0])


def acknowledge_intent(_player_input: str, state: dict[str, Any]) -> str:
    """Acknowledge intent without inventing mechanics, with a one-shot opening."""
    scene = state.get("scene")
    if isinstance(scene, dict) and scene.get("opening_seed"):
        character = state.get("character")
        character_name = character.get("name") if isinstance(character, dict) else None
        greeting = f"{character_name}, " if isinstance(character_name, str) and character_name else ""
        return (
            f"{greeting}você chega à Redwood Watch com uma missão clara: investigar a corrupção "
            "que se espalha pela floresta e os desaparecimentos que deixaram a região em alerta. "
            "À frente, Kaynen observa a passagem para o bosque de sequoias e mede suas intenções. "
            "O silêncio entre as árvores parece guardar a primeira pista — e a investigação começa agora."
        )
    return "Sua intenção foi registrada. Nenhuma consequência mecânica foi resolvida neste momento."


class GameOrchestrator:
    def __init__(
        self,
        narrator: NarratorProvider,
        *,
        resolve_action: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
        interpret_intent: Callable[..., dict[str, Any]] | None = None,
    ) -> None:
        self.narrator = narrator
        self.resolve_action = resolve_action
        self.interpret_intent = interpret_intent

    def turn(self, request: GameTurnRequest, *, request_id: str | None = None) -> GameTurnResponse:
        state = deepcopy(request.state)
        combat = state.get("combat")
        scene = state.get("scene")
        if isinstance(combat, dict) and "available_actions" in combat:
            available_actions = deepcopy(combat["available_actions"])
        elif isinstance(scene, dict) and isinstance(scene.get("available_actions"), list):
            available_actions = deepcopy(scene["available_actions"])
        else:
            available_actions = deepcopy(request.available_actions)

        action = request.action
        resolution_gate: dict[str, Any] | None = None
        if action is None and self.interpret_intent is not None:
            try:
                resolution_gate = self.interpret_intent(
                    campaign_id=request.campaign_id,
                    state=state,
                    player_input=request.player_input,
                    available_actions=available_actions,
                    request_id=request_id,
                )
            except Exception as exc:
                raise RuleResolutionError("Resolution Gate could not be evaluated") from exc
            if resolution_gate.get("requires_resolution") is True:
                gate_resolution = resolution_gate.get("resolution")
                if not isinstance(gate_resolution, dict):
                    raise RuleResolutionError("Resolution Gate returned no resolution request")
                try:
                    action = _bind_gate_intent(gate_resolution, available_actions)
                except InvalidGameAction:
                    # A model suggestion is not an authorization. If the current
                    # snapshot has no matching action, acknowledge without rolling.
                    # This keeps creative/trivial input safe and avoids inventing a
                    # DC, modifier, roll, consequence, or state transition.
                    action = None
            elif isinstance(resolution_gate.get("adventure_action"), dict):
                action = _bind_adventure_intent(
                    resolution_gate["adventure_action"],
                    available_actions,
                )

        if action is None:
            resolution = {
                "schema_version": "rule-resolution-v1",
                "status": "needs_rule_validation",
                "reason": "No mechanical resolution is required for this narrative intent.",
            }
            if resolution_gate and resolution_gate.get("requires_resolution") is True:
                resolution["reason"] = "The requested mechanical intent is not authorized by the current snapshot."
        else:
            try:
                resolution = self.resolve_action(action, state)
            except Exception as exc:
                raise InvalidGameAction("structured action failed Rule Engine validation") from exc
            if resolution.get("schema_version") != "rule-resolution-v1":
                raise RuleResolutionError("Rule Engine returned an unsupported resolution contract")
            if resolution.get("status") not in {"resolved", "needs_rule_validation"}:
                raise RuleResolutionError("Rule Engine returned an unsupported resolution status")
        if resolution_gate is not None:
            resolution["resolution_gate"] = deepcopy(resolution_gate)

        if action is not None and isinstance(scene, dict):
            scene["last_action"] = action.get("type")
        combat = state.get("combat")
        scene = state.get("scene")
        if isinstance(combat, dict) and "available_actions" in combat:
            available_actions = deepcopy(combat["available_actions"])
        elif isinstance(scene, dict) and isinstance(scene.get("available_actions"), list):
            available_actions = deepcopy(scene["available_actions"])

        record_narrative_turn(
            state,
            player_input=request.player_input,
            rule_resolution=resolution,
        )
        if resolution.get("status") != "resolved":
            narration = acknowledge_intent(request.player_input, state)
            # Keep the public contract compatible: the response is available,
            # but it was produced deterministically without the narrator.
            narration_status = "available"
        else:
            try:
                narration = self.narrator.narrate(
                    campaign_id=request.campaign_id,
                    state=state,
                    player_input=request.player_input,
                    rule_resolution=resolution,
                    available_actions=available_actions,
                    request_id=request_id,
                )
            except NarratorError:
                narration = fallback_narration(resolution, request.player_input)
                narration_status = "unavailable"
            else:
                narration_status = "available"

        record_narration(state, narration)
        scene = state.get("scene")
        if isinstance(scene, dict):
            scene.pop("opening_seed", None)
        return GameTurnResponse(
            campaign_id=request.campaign_id,
            narration=narration,
            narration_status=narration_status,
            rule_resolution=resolution,
            state=state,
            available_actions=available_actions,
        )
