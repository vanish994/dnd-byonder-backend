from __future__ import annotations

from math import floor
from typing import Any, Literal

from pydantic import BaseModel, Field, StrictInt, StrictStr, model_validator

from rule_engine.dice import MAX_MODIFIER

ABILITIES = (
    "strength",
    "dexterity",
    "constitution",
    "intelligence",
    "wisdom",
    "charisma",
)

SKILL_TO_ABILITY = {
    "acrobatics": "dexterity",
    "animal_handling": "wisdom",
    "arcana": "intelligence",
    "athletics": "strength",
    "deception": "charisma",
    "history": "intelligence",
    "insight": "wisdom",
    "intimidation": "charisma",
    "investigation": "intelligence",
    "medicine": "wisdom",
    "nature": "intelligence",
    "perception": "wisdom",
    "performance": "charisma",
    "persuasion": "charisma",
    "religion": "intelligence",
    "sleight_of_hand": "dexterity",
    "stealth": "dexterity",
    "survival": "wisdom",
}

CLASS_HIT_DIE = {"fighter": 10}
WEAPON_CATALOG = {
    "longsword": {
        "id": "longsword",
        "ability": "strength",
        "damage_dice": "1d8",
        "proficient": True,
    }
}


def ability_modifier(score: int) -> int:
    return floor((score - 10) / 2)


def proficiency_bonus(level: int) -> int:
    if level < 1 or level > 20:
        raise ValueError("level must be between 1 and 20")
    return 2 + ((level - 1) // 4)


class ClassFoundation(BaseModel):
    id: Literal["fighter"]
    level: StrictInt = Field(ge=1, le=20)

    class Config:
        extra = "forbid"


class WeaponFoundation(BaseModel):
    id: StrictStr = Field(min_length=1, max_length=64)
    ability: Literal["strength", "dexterity"] = "strength"
    damage_dice: Literal["1d8"] = "1d8"
    proficient: bool = True

    @model_validator(mode="before")
    @classmethod
    def normalize_damage(cls, value: Any) -> Any:
        if isinstance(value, dict):
            data = dict(value)
            damage = data.pop("damage", None)
            if isinstance(damage, dict):
                data.setdefault("damage_dice", damage.get("dice"))
            return data
        return value

    class Config:
        extra = "forbid"


class Proficiencies(BaseModel):
    skills: dict[str, bool] = Field(default_factory=dict)
    saving_throws: dict[str, bool] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value or {}
        data = dict(value)
        for field in ("skills", "saving_throws"):
            raw = data.get(field, {})
            if isinstance(raw, list):
                data[field] = {item: True for item in raw}
            elif isinstance(raw, dict):
                data[field] = {
                    key: (item.get("proficient", False) if isinstance(item, dict) else bool(item))
                    for key, item in raw.items()
                }
        return data

    @model_validator(mode="after")
    def validate_names(self):
        unknown_skills = set(self.skills) - set(SKILL_TO_ABILITY)
        unknown_saves = set(self.saving_throws) - set(ABILITIES)
        if unknown_skills:
            raise ValueError(f"unknown skills: {sorted(unknown_skills)}")
        if unknown_saves:
            raise ValueError(f"unknown saving throws: {sorted(unknown_saves)}")
        return self

    class Config:
        extra = "forbid"


class Character(BaseModel):
    id: StrictStr = Field(min_length=1, max_length=64)
    level: StrictInt = Field(ge=1, le=20)
    abilities: dict[str, StrictInt]
    class_: ClassFoundation = Field(alias="class")
    proficiencies: Proficiencies = Field(default_factory=Proficiencies)
    weapons: dict[str, WeaponFoundation] = Field(default_factory=dict)
    current_hp: StrictInt | None = Field(default=None, ge=0)

    @model_validator(mode="before")
    @classmethod
    def normalize_character(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "class_" in data and "class" not in data:
            data["class"] = data.pop("class_")
        data.setdefault("proficiencies", {})
        data.setdefault("weapons", {})
        return data

    @model_validator(mode="after")
    def validate_character(self):
        if set(self.abilities) != set(ABILITIES):
            missing = sorted(set(ABILITIES) - set(self.abilities))
            extra = sorted(set(self.abilities) - set(ABILITIES))
            raise ValueError(f"abilities must contain exactly the six abilities; missing={missing}, extra={extra}")
        for ability, score in self.abilities.items():
            if score < 1 or score > 30:
                raise ValueError(f"ability score out of range: {ability}")
        if self.class_.level != self.level:
            raise ValueError("class level must match character level")
        if self.current_hp is not None and self.current_hp > self.derived()["hp"]["max"]:
            raise ValueError("current_hp cannot exceed derived max HP")
        return self

    def derived(self) -> dict[str, Any]:
        modifiers = {ability: ability_modifier(self.abilities[ability]) for ability in ABILITIES}
        prof = proficiency_bonus(self.level)
        skill_modifiers = {
            skill: modifiers[ability] + (prof if self.proficiencies.skills.get(skill, False) else 0)
            for skill, ability in SKILL_TO_ABILITY.items()
        }
        saving_throw_modifiers = {
            ability: modifiers[ability] + (prof if self.proficiencies.saving_throws.get(ability, False) else 0)
            for ability in ABILITIES
        }
        max_hp = CLASS_HIT_DIE[self.class_.id] + modifiers["constitution"]
        current_hp = max_hp if self.current_hp is None else self.current_hp
        return {
            "ability_modifiers": modifiers,
            "proficiency_bonus": prof,
            "skill_modifiers": skill_modifiers,
            "saving_throw_modifiers": saving_throw_modifiers,
            "hp": {"current": current_hp, "max": max_hp},
            "ac": {"value": 10 + modifiers["dexterity"], "source": "unarmored"},
            "initiative_modifier": modifiers["dexterity"],
        }

    def weapon(self, weapon_id: str) -> WeaponFoundation:
        data = self.weapons.get(weapon_id)
        if data is None:
            catalog = WEAPON_CATALOG.get(weapon_id)
            if catalog is None:
                raise ValueError(f"unknown weapon: {weapon_id}")
            return WeaponFoundation(**catalog)
        return data

    class Config:
        populate_by_name = True
        extra = "forbid"


def character_to_state(character: Character) -> dict[str, Any]:
    return {
        "id": character.id,
        "level": character.level,
        "class": character.class_.model_dump(),
        "abilities": dict(character.abilities),
        "proficiencies": character.proficiencies.model_dump(),
        "weapons": {key: value.model_dump() for key, value in character.weapons.items()},
        "current_hp": character.current_hp,
    }


def derive_character(value: dict[str, Any] | Character) -> tuple[Character, dict[str, Any]]:
    character = value if isinstance(value, Character) else Character.model_validate(value)
    return character, character.derived()
