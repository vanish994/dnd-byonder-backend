"""Universal PHB 2024 character model.

The model describes canonical character state. It does not infer missing class,
spell, species, or subclass mechanics; those are resolved only by catalog-backed
Rule Engine modules as they become implemented.
"""
from __future__ import annotations

from copy import deepcopy
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, StrictInt, StrictStr, model_validator

from rule_engine.canonical_catalog import CANONICAL_CATALOG
from rule_engine.progression import MAX_LEVEL, validate_experience_points

UNIVERSAL_CHARACTER_SCHEMA = "universal-character-v1"
ABILITIES = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
SKILLS = (
    "acrobatics", "animal_handling", "arcana", "athletics", "deception", "history",
    "insight", "intimidation", "investigation", "medicine", "nature", "perception",
    "performance", "persuasion", "religion", "sleight_of_hand", "stealth", "survival",
)


class CanonicalSelection(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: StrictStr = Field(min_length=1, max_length=128)
    kind: Literal["class", "species", "background"]
    evidence_hash: StrictStr = Field(min_length=64, max_length=64)

    @model_validator(mode="after")
    def validate_catalog_record(self) -> "CanonicalSelection":
        record = CANONICAL_CATALOG.get(self.kind, self.id)
        if record.provenance.evidence_hash != self.evidence_hash:
            raise ValueError(f"{self.kind} selection provenance does not match canonical catalog")
        return self


class AbilityScores(BaseModel):
    model_config = ConfigDict(extra="forbid")

    strength: StrictInt = Field(ge=1, le=30)
    dexterity: StrictInt = Field(ge=1, le=30)
    constitution: StrictInt = Field(ge=1, le=30)
    intelligence: StrictInt = Field(ge=1, le=30)
    wisdom: StrictInt = Field(ge=1, le=30)
    charisma: StrictInt = Field(ge=1, le=30)

    def as_dict(self) -> dict[str, int]:
        return self.model_dump()


class ProficiencyState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    skills: dict[StrictStr, bool] = Field(default_factory=dict)
    saving_throws: dict[StrictStr, bool] = Field(default_factory=dict)
    weapons: list[StrictStr] = Field(default_factory=list)
    armor: list[StrictStr] = Field(default_factory=list)
    tools: list[StrictStr] = Field(default_factory=list)
    languages: list[StrictStr] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_ids(self) -> "ProficiencyState":
        unknown_skills = set(self.skills) - set(SKILLS)
        unknown_saves = set(self.saving_throws) - set(ABILITIES)
        if unknown_skills or unknown_saves:
            raise ValueError("proficiency state contains an unknown PHB 2024 skill or saving throw")
        return self


class SpellcastingState(BaseModel):
    model_config = ConfigDict(extra="forbid")

    ability: StrictStr | None = None
    cantrips_known: list[StrictStr] = Field(default_factory=list)
    spells_known: list[StrictStr] = Field(default_factory=list)
    spells_prepared: list[StrictStr] = Field(default_factory=list)
    spell_slots: dict[StrictInt, StrictInt] = Field(default_factory=dict)
    concentration_spell_id: StrictStr | None = None

    @model_validator(mode="after")
    def validate_state(self) -> "SpellcastingState":
        if self.ability is not None and self.ability not in ABILITIES:
            raise ValueError("spellcasting ability must be a canonical ability")
        if len(set(self.cantrips_known)) != len(self.cantrips_known):
            raise ValueError("cantrips_known must not contain duplicates")
        if len(set(self.spells_known)) != len(self.spells_known):
            raise ValueError("spells_known must not contain duplicates")
        if len(set(self.spells_prepared)) != len(self.spells_prepared):
            raise ValueError("spells_prepared must not contain duplicates")
        if any(level < 1 or level > 9 or slots < 0 for level, slots in self.spell_slots.items()):
            raise ValueError("spell slots must use levels 1 through 9 and non-negative counts")
        if self.concentration_spell_id and self.concentration_spell_id not in self.spells_known + self.spells_prepared:
            raise ValueError("concentration spell must be known or prepared")
        return self


class ActiveEffect(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: StrictStr = Field(min_length=1, max_length=128)
    source_id: StrictStr = Field(min_length=1, max_length=128)
    duration: dict[str, Any]
    timing: dict[str, Any] | None = None
    effects: list[dict[str, Any]] = Field(default_factory=list)


class UniversalCharacter(BaseModel):
    model_config = ConfigDict(extra="forbid")

    schema_version: Literal["universal-character-v1"] = UNIVERSAL_CHARACTER_SCHEMA
    ruleset: Literal["dnd-2024-phb"] = "dnd-2024-phb"
    id: StrictStr = Field(min_length=1, max_length=128)
    name: StrictStr | None = Field(default=None, min_length=1, max_length=64)
    level: StrictInt = Field(default=1, ge=1, le=MAX_LEVEL)
    experience_points: StrictInt = Field(default=0, ge=0)
    class_selection: CanonicalSelection
    subclass_selection: CanonicalSelection | None = None
    species_selection: CanonicalSelection | None = None
    background_selection: CanonicalSelection | None = None
    species_choices: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    class_choices: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    ability_method_id: StrictStr | None = None
    base_abilities: AbilityScores
    ability_score_increases: dict[StrictStr, StrictInt] = Field(default_factory=dict)
    abilities: AbilityScores
    proficiencies: ProficiencyState = Field(default_factory=ProficiencyState)
    inventory: dict[str, Any] = Field(default_factory=dict)
    equipped: dict[str, str | None] = Field(default_factory=dict)
    hit_points: dict[str, StrictInt] = Field(default_factory=dict)
    derived: dict[str, Any] = Field(default_factory=dict)
    class_features: list[StrictStr] = Field(default_factory=list)
    resources: dict[str, dict[str, Any]] = Field(default_factory=dict)
    spellcasting: SpellcastingState = Field(default_factory=SpellcastingState)
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    active_effects: list[ActiveEffect] = Field(default_factory=list)
    provenance: list[dict[str, Any]] = Field(default_factory=list)

    @model_validator(mode="after")
    def validate_character(self) -> "UniversalCharacter":
        validate_experience_points(self.experience_points)
        if self.level > 1 and self.experience_points == 0:
            raise ValueError("level above 1 requires canonical experience points")
        if self.class_selection.kind != "class":
            raise ValueError("class_selection must reference a class record")
        if self.subclass_selection is not None and self.subclass_selection.kind != "subclass":
            raise ValueError("subclass_selection must reference a subclass catalog record")
        if self.species_selection is not None and self.species_selection.kind != "species":
            raise ValueError("species_selection must reference a species record")
        if self.background_selection is not None and self.background_selection.kind != "background":
            raise ValueError("background_selection must reference a background record")
        if set(self.ability_score_increases) - set(ABILITIES):
            raise ValueError("ability score increases contain an unknown ability")
        return self

    @classmethod
    def from_legacy_state(cls, value: dict[str, Any]) -> "UniversalCharacter":
        """Adapt the current character snapshot without inventing missing mechanics."""
        source = deepcopy(value)
        class_data = source.get("class") or {}
        class_id = class_data.get("id")
        class_record = CANONICAL_CATALOG.get("class", class_id)
        species_id = source.get("species_id")
        background_id = source.get("background_id")
        derived = source.get("derived") or {}
        derived_hp = derived.get("hp") if isinstance(derived, dict) else None
        current_hp = source.get("current_hp")
        if current_hp is None and isinstance(derived_hp, dict):
            current_hp = derived_hp.get("current")
        if current_hp is None:
            current_hp = source.get("hp", 0)
        maximum_hp = derived_hp.get("max", current_hp) if isinstance(derived_hp, dict) else source.get("hp", current_hp)
        spellcasting = deepcopy(source.get("spellcasting", {}))
        if isinstance(spellcasting, dict) and isinstance(spellcasting.get("spell_slots"), dict):
            spellcasting["spell_slots"] = {
                int(level) if str(level).isdigit() else level: slots
                for level, slots in spellcasting["spell_slots"].items()
            }
        selections = {
            "class_selection": {
                "id": class_id,
                "kind": "class",
                "evidence_hash": class_record.provenance.evidence_hash,
            },
            "base_abilities": source.get("base_abilities") or source.get("abilities"),
            "abilities": source.get("abilities"),
            "id": source["id"],
            "name": source.get("name"),
            "level": source.get("level", class_data.get("level", 1)),
            "experience_points": source.get("experience_points", 0),
            "ability_method_id": source.get("ability_method_id"),
            "ability_score_increases": source.get("background_ability_increases", {}),
            "species_choices": source.get("species_choices", {}),
            "class_choices": source.get("class_choices", {}),
            "proficiencies": source.get("proficiencies", {}),
            "inventory": source.get("inventory", {}),
            "equipped": source.get("equipped", {}),
            "hit_points": {"current": current_hp, "maximum": maximum_hp},
            "derived": derived,
            "class_features": source.get("class_features", []),
            "resources": source.get("resources", {}),
            "spellcasting": spellcasting,
            "conditions": source.get("conditions", []),
            "provenance": [{"source": "legacy-character-snapshot", "schema": "pre-universal-character"}],
        }
        if species_id is not None:
            record = CANONICAL_CATALOG.get("species", species_id)
            selections["species_selection"] = {"id": species_id, "kind": "species", "evidence_hash": record.provenance.evidence_hash}
        if background_id is not None:
            record = CANONICAL_CATALOG.get("background", background_id)
            selections["background_selection"] = {"id": background_id, "kind": "background", "evidence_hash": record.provenance.evidence_hash}
        subclass_id = source.get("subclass_id")
        if subclass_id is not None:
            record = CANONICAL_CATALOG.get("subclass", subclass_id)
            selections["subclass_selection"] = {"id": subclass_id, "kind": "subclass", "evidence_hash": record.provenance.evidence_hash}
        return cls.model_validate(selections)

    def to_snapshot(self) -> dict[str, Any]:
        return self.model_dump(mode="json", exclude_none=True)

    def runtime_selection(self) -> dict[str, Any]:
        """Return only catalog-backed choices for the next Rule Engine resolver."""
        return {
            "class_id": self.class_selection.id,
            "subclass_id": self.subclass_selection.id if self.subclass_selection else None,
            "species_id": self.species_selection.id if self.species_selection else None,
            "background_id": self.background_selection.id if self.background_selection else None,
            "level": self.level,
            "abilities": self.abilities.as_dict(),
            "spellcasting": self.spellcasting.model_dump(mode="json"),
        }
