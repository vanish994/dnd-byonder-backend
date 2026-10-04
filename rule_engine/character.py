from __future__ import annotations

import uuid
from math import floor
from typing import Any, Literal

from pydantic import BaseModel, Field, StrictInt, StrictStr, model_validator

from game.contracts import GuidedCharacterRequest
from rule_engine.dice import MAX_MODIFIER
from rule_engine.equipment import WEAPON_CATALOG, equipped_definition, validate_inventory
from rule_engine.resources import validate_resources

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
# Guided creation is deliberately narrower than the existing Character and combat models.
STANDARD_ARRAY = (15, 14, 13, 12, 10, 8)
FIGHTER_LEVELS = (1,)
FIGHTER_SKILL_COUNT = 2
# 2024 Fighter proficiency choices: https://www.dndbeyond.com/classes/2190879-fighter
FIGHTER_SKILLS = (
    "acrobatics", "animal_handling", "athletics", "history", "insight",
    "intimidation", "persuasion", "perception", "survival",
)
FIGHTER_SAVING_THROWS = ("strength", "constitution")
FIGHTER_WEAPONS = ("longsword",)

ABILITY_PRESENTATION = {
    "strength": ("STR", "Força", "Usada em ataques físicos e esforços de força."),
    "dexterity": ("DEX", "Destreza", "Ajuda na defesa, na iniciativa e em movimentos ágeis."),
    "constitution": ("CON", "Constituição", "Ajuda a determinar seus pontos de vida."),
    "intelligence": ("INT", "Inteligência", "Ajuda a recordar fatos e raciocinar."),
    "wisdom": ("WIS", "Sabedoria", "Ajuda a perceber o ambiente e interpretar sinais."),
    "charisma": ("CHA", "Carisma", "Ajuda a conversar, convencer e inspirar."),
}
FIGHTER_SKILL_LABELS = {
    "acrobatics": "Acrobacia",
    "animal_handling": "Lidar com Animais",
    "athletics": "Atletismo",
    "history": "História",
    "insight": "Intuição",
    "intimidation": "Intimidação",
    "persuasion": "Persuasão",
    "perception": "Percepção",
    "survival": "Sobrevivência",
}


def character_options() -> dict[str, Any]:
    """Return a fresh, versioned public catalog driven by the supported rules."""
    return {
        "schema_version": "character-options-v1",
        "classes": [{
            "id": "fighter",
            "label": "Guerreiro",
            "levels": list(FIGHTER_LEVELS),
            "skill_choices": {"count": FIGHTER_SKILL_COUNT, "options": list(FIGHTER_SKILLS)},
            "saving_throw_proficiencies": list(FIGHTER_SAVING_THROWS),
            "weapon_options": list(FIGHTER_WEAPONS),
        }],
        "levels": list(FIGHTER_LEVELS),
        "standard_array": list(STANDARD_ARRAY),
        "abilities": [
            {"id": ability, "abbreviation": ABILITY_PRESENTATION[ability][0],
             "label": ABILITY_PRESENTATION[ability][1],
             "description": ABILITY_PRESENTATION[ability][2]}
            for ability in ABILITIES
        ],
        "skills": [
            {"id": skill, "label": FIGHTER_SKILL_LABELS[skill], "ability": SKILL_TO_ABILITY[skill]}
            for skill in FIGHTER_SKILLS
        ],
        "weapons": [
            {**WEAPON_CATALOG[weapon_id], "label": "Espada longa"}
            for weapon_id in FIGHTER_WEAPONS
        ],
        "selection_rules": {
            "ability_assignment": {
                "mode": "standard_array", "use_all_values": True, "each_ability_once": True,
            },
            "skills": {"unique": True},
            "weapons": {"count": 1},
        },
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
    kind: Literal["weapon"] = "weapon"
    slot: Literal["weapon"] = "weapon"
    ability: Literal["strength", "dexterity"] = "strength"
    damage_dice: StrictStr = Field(default="1d8", min_length=2, max_length=20)
    damage_type: StrictStr = "slashing"
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
    name: StrictStr | None = Field(default=None, min_length=1, max_length=64)
    level: StrictInt = Field(ge=1, le=20)
    abilities: dict[str, StrictInt]
    class_: ClassFoundation = Field(alias="class")
    proficiencies: Proficiencies = Field(default_factory=Proficiencies)
    weapons: dict[str, WeaponFoundation] = Field(default_factory=dict)
    inventory: dict[str, Any] = Field(default_factory=dict)
    equipped: dict[str, str | None] = Field(default_factory=lambda: {"weapon": None, "armor": None})
    resources: dict[str, Any] = Field(default_factory=dict)
    conditions: list[dict[str, Any]] = Field(default_factory=list)
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
        data.setdefault("inventory", {})
        data.setdefault("equipped", {"weapon": None, "armor": None})
        data.setdefault("resources", {})
        data.setdefault("conditions", [])
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
        validate_inventory({"inventory": self.inventory, "equipped": self.equipped})
        validate_resources({"resources": self.resources})
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
        weapons = {}
        for weapon_id, weapon in self.weapons.items():
            modifier = modifiers[weapon.ability]
            weapons[weapon_id] = {
                **weapon.model_dump(exclude={"kind", "slot", "damage_type"}),
                "attack_bonus": modifier + (prof if weapon.proficient else 0),
                "damage_modifier": modifier,
            }
        ac = 10 + modifiers["dexterity"]
        ac_source = "unarmored"
        equipment_state = {
            "inventory": self.inventory,
            "equipped": self.equipped,
        }
        armor = equipped_definition(equipment_state, "armor")
        if armor is not None:
            dexterity_bonus_max = armor.get("dexterity_bonus_max")
            dexterity_bonus = modifiers["dexterity"]
            if dexterity_bonus_max is not None:
                dexterity_bonus = min(dexterity_bonus, dexterity_bonus_max)
            ac = armor["armor_class"] + dexterity_bonus
            ac_source = armor["id"]
        return {
            "ability_modifiers": modifiers,
            "proficiency_bonus": prof,
            "skill_modifiers": skill_modifiers,
            "saving_throw_modifiers": saving_throw_modifiers,
            "hp": {"current": current_hp, "max": max_hp},
            "ac": {"value": ac, "source": ac_source},
            "initiative_modifier": modifiers["dexterity"],
            "weapons": weapons,
        }

    def weapon(self, weapon_id: str) -> WeaponFoundation:
        data = self.weapons.get(weapon_id)
        if data is None:
            inventory_entry = self.inventory.get(weapon_id)
            if isinstance(inventory_entry, dict):
                item = inventory_entry.get("item")
                if isinstance(item, dict) and item.get("kind") == "weapon":
                    return WeaponFoundation(**item)
            catalog = WEAPON_CATALOG.get(weapon_id)
            if catalog is None:
                raise ValueError(f"unknown weapon: {weapon_id}")
            return WeaponFoundation(**catalog)
        return data

    class Config:
        populate_by_name = True
        extra = "forbid"


def character_to_state(character: Character) -> dict[str, Any]:
    state = {
        "id": character.id,
        "level": character.level,
        "class": character.class_.model_dump(),
        "abilities": dict(character.abilities),
        "proficiencies": character.proficiencies.model_dump(),
        "weapons": {key: value.model_dump() for key, value in character.weapons.items()},
        "current_hp": character.current_hp,
    }
    if character.inventory:
        state["inventory"] = character.inventory
    if character.equipped != {"weapon": None, "armor": None}:
        state["equipped"] = character.equipped
    if character.resources:
        state["resources"] = character.resources
    if character.conditions:
        state["conditions"] = character.conditions
    if character.name is not None:
        state["name"] = character.name
    return state


def build_guided_character(selection: GuidedCharacterRequest) -> Character:
    """Validate the selected catalog values and construct the existing mechanics model."""
    if selection.class_id != "fighter":
        raise ValueError("unsupported class")
    if selection.level not in FIGHTER_LEVELS:
        raise ValueError("unsupported level for fighter")
    if set(selection.abilities) != set(ABILITIES):
        raise ValueError("abilities must include exactly the catalog abilities")
    if sorted(selection.abilities.values()) != sorted(STANDARD_ARRAY):
        raise ValueError("abilities must use the complete standard array exactly once")
    if len(selection.skills) != FIGHTER_SKILL_COUNT:
        raise ValueError("incorrect number of fighter skills")
    if len(set(selection.skills)) != len(selection.skills):
        raise ValueError("fighter skills must be unique")
    if not set(selection.skills).issubset(FIGHTER_SKILLS):
        raise ValueError("unsupported fighter skill")
    if selection.weapon_id not in FIGHTER_WEAPONS:
        raise ValueError("unsupported fighter weapon")

    character_data = {
        "id": f"character-{uuid.uuid4().hex}",
        "name": selection.name,
        "level": selection.level,
        "class": {"id": selection.class_id, "level": selection.level},
        "abilities": dict(selection.abilities),
        "proficiencies": {
            "skills": {skill: True for skill in selection.skills},
            "saving_throws": {ability: True for ability in FIGHTER_SAVING_THROWS},
        },
        "weapons": {selection.weapon_id: WEAPON_CATALOG[selection.weapon_id]},
        "inventory": {
            selection.weapon_id: {
                "item_id": selection.weapon_id,
                "quantity": 1,
                "item": WEAPON_CATALOG[selection.weapon_id],
            }
        },
        "equipped": {"weapon": selection.weapon_id, "armor": None},
    }
    character = Character.model_validate(character_data)
    return Character.model_validate({**character_data, "current_hp": character.derived()["hp"]["max"]})


def derive_character(value: dict[str, Any] | Character) -> tuple[Character, dict[str, Any]]:
    character = value if isinstance(value, Character) else Character.model_validate(value)
    return character, character.derived()
