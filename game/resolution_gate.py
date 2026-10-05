from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictStr


RESOLUTION_GATE_SCHEMA_VERSION = "resolution-gate-v1"


class ResolutionRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["skill_check", "ability_check"]
    skill: StrictStr | None = Field(default=None, min_length=1, max_length=64)
    ability: Literal[
        "strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma"
    ] | None = None

    def model_post_init(self, _context: object) -> None:
        if self.type == "skill_check" and not self.skill:
            raise ValueError("skill_check requires skill")
        if self.type == "ability_check" and not self.ability:
            raise ValueError("ability_check requires ability")
        if self.type == "skill_check" and self.ability is not None:
            raise ValueError("skill_check cannot include ability")
        if self.type == "ability_check" and self.skill is not None:
            raise ValueError("ability_check cannot include skill")


class AdventureIntent(BaseModel):
    model_config = ConfigDict(extra="forbid")

    type: Literal["adventure_action"]
    intent: Literal["collect_bark_sample"]


class ResolutionGateDecision(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["resolution-gate-v1"] = RESOLUTION_GATE_SCHEMA_VERSION
    requires_resolution: StrictBool
    resolution: ResolutionRequest | None = None
    adventure_action: AdventureIntent | None = None

    def model_post_init(self, _context: object) -> None:
        if self.requires_resolution and self.resolution is None:
            raise ValueError("resolution is required when requires_resolution is true")
        if not self.requires_resolution and self.resolution is not None:
            raise ValueError("resolution must be omitted when requires_resolution is false")
        if self.requires_resolution and self.adventure_action is not None:
            raise ValueError("adventure_action cannot accompany a mechanical resolution")
