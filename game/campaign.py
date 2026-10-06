from __future__ import annotations

from typing import Any, Literal
from pydantic import BaseModel, ConfigDict, Field, StrictStr, field_validator


class CampaignSetup(BaseModel):
    """Player preferences only; no mechanical values are accepted here."""
    model_config = ConfigDict(extra='forbid')
    campaign_name: StrictStr = Field(min_length=1, max_length=80)
    tone: Literal['heroic', 'dark', 'mystery', 'political', 'comedic', 'adventure'] = 'adventure'
    focus: list[Literal['exploration', 'investigation', 'social', 'combat', 'balanced']] = Field(default_factory=lambda: ['balanced'], min_length=1, max_length=5)
    difficulty_preference: Literal['gentle', 'standard', 'demanding'] = 'standard'
    setting_prompt: StrictStr = Field(min_length=1, max_length=500)
    themes_to_avoid: list[StrictStr] = Field(default_factory=list, max_length=8)
    campaign_summary: StrictStr | None = Field(default=None, max_length=500)
    character_goal: StrictStr | None = Field(default=None, max_length=300)

    @field_validator('campaign_name', 'setting_prompt', 'campaign_summary', 'character_goal')
    @classmethod
    def trim_text(cls, value: str | None) -> str | None:
        return value.strip() if value is not None else value

    @field_validator('themes_to_avoid')
    @classmethod
    def trim_themes(cls, values: list[str]) -> list[str]:
        cleaned = [value.strip() for value in values if value.strip()]
        if any(len(value) > 80 for value in cleaned):
            raise ValueError('theme is too long')
        return cleaned


class CampaignSeed(BaseModel):
    model_config = ConfigDict(extra='forbid')
    schema_version: Literal['campaign-seed-v1']
    title: StrictStr = Field(min_length=1, max_length=100)
    premise: StrictStr = Field(min_length=1, max_length=900)
    opening_location: StrictStr = Field(min_length=1, max_length=160)
    opening_description: StrictStr = Field(min_length=1, max_length=1200)
    initial_tension: StrictStr = Field(min_length=1, max_length=700)
    known_facts: list[StrictStr] = Field(default_factory=list, max_length=8)
    rumors: list[StrictStr] = Field(default_factory=list, max_length=8)
    npcs: list[dict[str, StrictStr]] = Field(default_factory=list, max_length=6)
    initial_objectives: list[StrictStr] = Field(default_factory=list, max_length=6)
    opening_question: StrictStr = Field(min_length=1, max_length=300)

    @field_validator('npcs')
    @classmethod
    def validate_npcs(cls, values: list[dict[str, str]]) -> list[dict[str, str]]:
        required = {'name', 'role', 'motivation'}
        if any(set(item) != required for item in values):
            raise ValueError('NPC entries must contain only name, role and motivation')
        return values


def build_dynamic_state(*, character: dict[str, Any], setup: CampaignSetup, seed: CampaignSeed) -> dict[str, Any]:
    """Narrative state only. Mechanical authority remains in the Rule Engine."""
    return {
        'schema_version': 'campaign-state-v1',
        'ruleset': 'dnd-2024-phb',
        'character': character,
        'campaign': {
            'title': seed.title,
            'name': setup.campaign_name,
            'tone': setup.tone,
            'focus': list(setup.focus),
            'difficulty_preference': setup.difficulty_preference,
            'premise': seed.premise,
            'setting_prompt': setup.setting_prompt,
            'character_goal': setup.character_goal,
        },
        'narrative_memory': {
            'known_facts': list(seed.known_facts),
            'rumors': list(seed.rumors),
            'npcs': list(seed.npcs),
            'locations': [seed.opening_location],
            'active_quests': list(seed.initial_objectives),
            'timeline': [],
        },
        'scene': {
            'id': 'generated-opening',
            'type': 'exploration',
            'title': seed.title,
            'location': seed.opening_location,
            'description': seed.opening_description,
            'tension': seed.initial_tension,
            'opening_seed': seed.opening_question,
            'available_actions': [],
        },
        'narrative_context': {'recent_dialogue': []},
    }
