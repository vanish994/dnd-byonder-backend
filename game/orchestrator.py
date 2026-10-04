from __future__ import annotations

from collections.abc import Callable
from copy import deepcopy
from typing import Any

from game.contracts import GameTurnRequest, GameTurnResponse
from services.mimo_narrator import MimoNarratorClient, MimoNarratorError


class InvalidGameAction(ValueError):
    """Raised when the structured action cannot be validated by the Rule Engine."""


class RuleResolutionError(RuntimeError):
    """Raised when the Rule Engine does not return the canonical contract."""


class NarrationError(RuntimeError):
    """Raised when MiMo fails after a mechanical resolution was produced."""

    def __init__(self, message: str, rule_resolution: dict[str, Any]) -> None:
        super().__init__(message)
        self.rule_resolution = rule_resolution


class GameOrchestrator:
    def __init__(
        self,
        narrator: MimoNarratorClient,
        *,
        resolve_action: Callable[[dict[str, Any], dict[str, Any]], dict[str, Any]],
    ) -> None:
        self.narrator = narrator
        self.resolve_action = resolve_action

    def turn(self, request: GameTurnRequest) -> GameTurnResponse:
        state = deepcopy(request.state)
        if request.action is None:
            resolution = {
                "schema_version": "rule-resolution-v1",
                "status": "needs_rule_validation",
                "reason": "No deterministic resolution was emitted because the requested rule has not been bound to a validated mechanic.",
            }
        else:
            try:
                resolution = self.resolve_action(request.action, state)
            except Exception as exc:
                raise InvalidGameAction("structured action failed Rule Engine validation") from exc
            if resolution.get("schema_version") != "rule-resolution-v1":
                raise RuleResolutionError("Rule Engine returned an unsupported resolution contract")
            if resolution.get("status") not in {"resolved", "needs_rule_validation"}:
                raise RuleResolutionError("Rule Engine returned an unsupported resolution status")

        try:
            narration = self.narrator.narrate(
                campaign_id=request.campaign_id,
                state=state,
                player_input=request.player_input,
                rule_resolution=resolution,
            )
        except MimoNarratorError as exc:
            raise NarrationError("MiMo narrator unavailable", resolution) from exc
        return GameTurnResponse(
            campaign_id=request.campaign_id,
            narration=narration,
            rule_resolution=resolution,
            state=state,
            available_actions=deepcopy(request.available_actions),
        )
