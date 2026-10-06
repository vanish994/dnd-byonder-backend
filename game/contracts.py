from __future__ import annotations

from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, field_validator

from game.campaign import CampaignSetup


class GuidedCharacterRequest(BaseModel):
    """Player choices only; identity, proficiencies, equipment and HP are server-owned."""

    model_config = ConfigDict(extra='forbid')

    name: StrictStr = Field(min_length=1, max_length=64)
    class_id: StrictStr
    level: StrictInt
    abilities: dict[StrictStr, StrictInt]
    skills: list[StrictStr]
    weapon_id: StrictStr

    @field_validator('name')
    @classmethod
    def trim_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError('name cannot be blank')
        return name


class PHB2024GuidedCharacterRequest(BaseModel):
    """Strict player choices for first-level 2024 PHB character creation."""

    model_config = ConfigDict(extra='forbid')

    name: StrictStr = Field(min_length=1, max_length=64)
    adventure_id: StrictStr = 'dragon-delves-death-at-sunset'
    class_id: StrictStr
    level: StrictInt = 1
    species_id: StrictStr
    species_choices: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    background_id: StrictStr
    alignment_id: StrictStr
    ability_method_id: StrictStr
    base_abilities: dict[StrictStr, StrictInt]
    background_ability_increases: dict[StrictStr, StrictInt]
    abilities: dict[StrictStr, StrictInt]
    skills: list[StrictStr]
    language_choices: list[StrictStr]
    class_equipment_option: StrictStr
    background_equipment_option: StrictStr
    class_choices: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    campaign_setup: CampaignSetup | None = None

    @field_validator('name')
    @classmethod
    def trim_name(cls, value: str) -> str:
        name = value.strip()
        if not name:
            raise ValueError('name cannot be blank')
        return name


class AbilityOption(BaseModel):
    id: str
    abbreviation: str
    label: str
    description: str


class SkillOption(BaseModel):
    id: str
    label: str
    ability: str


class WeaponOption(BaseModel):
    id: str
    label: str
    ability: str
    damage_dice: str
    proficient: bool


class SkillChoices(BaseModel):
    count: int
    options: list[str]


class ClassOption(BaseModel):
    id: str
    label: str
    levels: list[int]
    skill_choices: SkillChoices
    saving_throw_proficiencies: list[str]
    weapon_options: list[str]


class AbilityAssignmentRule(BaseModel):
    mode: Literal['standard_array']
    use_all_values: bool
    each_ability_once: bool


class SkillSelectionRule(BaseModel):
    unique: bool


class WeaponSelectionRule(BaseModel):
    count: int


class SelectionRules(BaseModel):
    ability_assignment: AbilityAssignmentRule
    skills: SkillSelectionRule
    weapons: WeaponSelectionRule


class CharacterOptionsResponse(BaseModel):
    schema_version: Literal['character-options-v1']
    classes: list[ClassOption]
    levels: list[int]
    standard_array: list[int]
    abilities: list[AbilityOption]
    skills: list[SkillOption]
    weapons: list[WeaponOption]
    selection_rules: SelectionRules


class CharacterValidationResponse(BaseModel):
    schema_version: Literal['character-creation-v1']
    valid: Literal[True]
    character: dict[str, Any]
    derived: dict[str, Any]
    rule_resolution: dict[str, Any]


class CharacterCreationResponse(CharacterValidationResponse):
    campaign_id: str
    state: dict[str, Any]
    available_actions: list[dict[str, Any]]


class PHB2024CharacterCreationResponse(BaseModel):
    schema_version: Literal['character-creation-phb2024-v1']
    ruleset: Literal['dnd-2024-phb']
    valid: Literal[True]
    character: dict[str, Any]
    derived: dict[str, Any]
    rule_resolution: dict[str, Any]
    campaign_id: str
    session_id: str
    revision: StrictInt
    state: dict[str, Any]
    available_actions: list[dict[str, Any]]


class SessionTurnRequest(BaseModel):
    """Public command for a session; canonical state never arrives from the client."""

    model_config = ConfigDict(extra='forbid')

    session_id: UUID
    expected_revision: StrictInt = Field(ge=0)
    player_input: StrictStr = Field(min_length=1, max_length=4000)
    action: dict[str, Any] | None = None


class GameTurnRequest(BaseModel):
    campaign_id: StrictStr = Field(default='', max_length=128)
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


class PersistedGameTurnResponse(GameTurnResponse):
    session_id: str
    revision: StrictInt


class SessionResumeResponse(BaseModel):
    campaign_id: str
    session_id: str
    ruleset: Literal['dnd-2024-phb']
    revision: StrictInt
    character: dict[str, Any]
    derived: dict[str, Any]
    state: dict[str, Any]
    available_actions: list[dict[str, Any]]
    history: list[dict[str, Any]]
