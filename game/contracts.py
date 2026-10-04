from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field, StrictStr


class GameTurnRequest(BaseModel):
    campaign_id: StrictStr = Field(default="", max_length=128)
    state: dict[str, Any] = Field(default_factory=dict)
    player_input: StrictStr = Field(min_length=1, max_length=4000)
    action: dict[str, Any] | None = None
    available_actions: list[dict[str, Any]] = Field(default_factory=list)


class GameTurnResponse(BaseModel):
    campaign_id: str
    narration: str
    narration_status: Literal['available', 'unavailable'] = 'available'
    rule_resolution: dict[str, Any]
    state: dict[str, Any]
    available_actions: list[dict[str, Any]]
