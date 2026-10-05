from __future__ import annotations

import uuid
from math import floor
from typing import Any, Literal

from pydantic import BaseModel, Field, StrictInt, StrictStr, model_validator

from game.contracts import GuidedCharacterRequest, PHB2024GuidedCharacterRequest
from rule_engine.classes import class_definition, class_features, class_resource_maximum, class_resource_recovery, class_resource_recovery_amount
from rule_engine.character_creation_catalog import (
    ABILITY_IDS,
    BACKGROUND_RECORDS,
    CLASS_RECORDS,
    CLASS_RUNTIME_DEFINITIONS,
    PHB2024_CATALOG,
    SPECIES_RECORDS,
    background_definition,
    validate_ability_assignment,
    validate_background_ability_increases,
    validate_equipment_package_choice,
    validate_species_choices,
)
from rule_engine.dice import MAX_MODIFIER
from rule_engine.equipment import WEAPON_CATALOG, equipped_definition, validate_inventory
from rule_engine.progression import MAX_LEVEL, experience_for_level, proficiency_bonus_for_level, validate_experience_points
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

# Guided creation is deliberately narrower than the existing Character and combat models.
STANDARD_ARRAY = (15, 14, 13, 12, 10, 8)
FIGHTER_LEVELS = tuple(range(1, MAX_LEVEL + 1))
FIGHTER_SKILL_COUNT = 2
# 2024 Fighter proficiency choices: https://www.dndbeyond.com/classes/2190879-fighter
FIGHTER_SKILLS = (
    "acrobatics", "animal_handling", "athletics", "history", "insight",
    "intimidation", "persuasion", "perception", "survival",
)
FIGHTER_SAVING_THROWS = ("strength", "constitution")
FIGHTER_WEAPONS = ("longsword",)
WEAPON_PROFICIENCY_GROUPS = {"longsword": "martial"}

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
    return proficiency_bonus_for_level(level)


class ClassFoundation(BaseModel):
    id: StrictStr = Field(min_length=1, max_length=64)
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
    experience_points: StrictInt = Field(default=0, ge=0)
    abilities: dict[str, StrictInt]
    class_: ClassFoundation = Field(alias="class")
    proficiencies: Proficiencies = Field(default_factory=Proficiencies)
    weapons: dict[str, WeaponFoundation] = Field(default_factory=dict)
    inventory: dict[str, Any] = Field(default_factory=dict)
    equipped: dict[str, str | None] = Field(default_factory=lambda: {"weapon": None, "armor": None})
    resources: dict[str, Any] = Field(default_factory=dict)
    conditions: list[dict[str, Any]] = Field(default_factory=list)
    class_features: list[StrictStr] = Field(default_factory=list)
    species_id: StrictStr | None = None
    species_choices: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    background_id: StrictStr | None = None
    alignment_id: StrictStr | None = None
    ability_method_id: StrictStr | None = None
    base_abilities: dict[StrictStr, StrictInt] = Field(default_factory=dict)
    background_ability_increases: dict[StrictStr, StrictInt] = Field(default_factory=dict)
    class_skill_choices: list[StrictStr] = Field(default_factory=list)
    species_skill_choices: list[StrictStr] = Field(default_factory=list)
    languages: list[StrictStr] = Field(default_factory=list)
    class_choices: dict[StrictStr, StrictStr] = Field(default_factory=dict)
    starting_equipment: dict[str, Any] = Field(default_factory=dict)
    origin_feat: dict[str, Any] = Field(default_factory=dict)
    current_hp: StrictInt | None = Field(default=None, ge=0)

    @model_validator(mode="before")
    @classmethod
    def normalize_character(cls, value: Any) -> Any:
        if not isinstance(value, dict):
            return value
        data = dict(value)
        if "class_" in data and "class" not in data:
            data["class"] = data.pop("class_")
        generation = data.pop("ability_generation", None)
        if generation is not None:
            expected_fields = {"method_id", "base_abilities", "background_increases"}
            if not isinstance(generation, dict) or set(generation) != expected_fields:
                raise ValueError("invalid PHB 2024 ability_generation metadata")
            normalized_fields = {
                "ability_method_id": generation["method_id"],
                "base_abilities": generation["base_abilities"],
                "background_ability_increases": generation["background_increases"],
            }
            for field, generated_value in normalized_fields.items():
                if field in data and data[field] != generated_value:
                    raise ValueError("ability_generation conflicts with canonical character fields")
                data[field] = generated_value
        data.setdefault("proficiencies", {})
        data.setdefault("weapons", {})
        data.setdefault("inventory", {})
        data.setdefault("equipped", {"weapon": None, "armor": None})
        data.setdefault("resources", {})
        data.setdefault("conditions", [])
        data.setdefault("class_features", [])
        data.setdefault("species_choices", {})
        data.setdefault("base_abilities", {})
        data.setdefault("background_ability_increases", {})
        data.setdefault("class_skill_choices", [])
        data.setdefault("species_skill_choices", [])
        data.setdefault("languages", [])
        data.setdefault("class_choices", {})
        data.setdefault("starting_equipment", {})
        data.setdefault("origin_feat", {})
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
        class_definition(self.class_.id)
        validate_experience_points(self.experience_points)
        class_data = class_definition(self.class_.id)
        if self.level > class_data.get("max_supported_level", MAX_LEVEL):
            raise ValueError("class progression is not implemented at this level")
        selected_skills = {skill for skill, proficient in self.proficiencies.skills.items() if proficient}
        allowed_skills = set(class_data["skill_proficiencies"]["options"])
        if self.species_id is None and self.background_id is None:
            if self.class_.id != "fighter":
                raise ValueError("non-Fighter classes require complete PHB 2024 character creation metadata")
            if not selected_skills <= allowed_skills:
                raise ValueError("character has a skill proficiency not granted by class")
            if len(selected_skills) > class_data["skill_proficiencies"]["count"]:
                raise ValueError("character has too many class skill proficiencies")
        else:
            if self.species_id not in SPECIES_RECORDS or self.background_id not in BACKGROUND_RECORDS:
                raise ValueError("species and background must come from the PHB 2024 catalog")
            if self.level != 1:
                raise ValueError("PHB 2024 guided creation currently supports level 1 only")
            if self.alignment_id not in {
                "lawful_good", "neutral_good", "chaotic_good", "lawful_neutral", "neutral",
                "chaotic_neutral", "lawful_evil", "neutral_evil", "chaotic_evil",
            }:
                raise ValueError("alignment must be one of the PHB 2024 options")
            if self.ability_method_id is None or set(self.base_abilities) != set(ABILITY_IDS):
                raise ValueError("PHB 2024 ability generation details are required")
            validate_ability_assignment(self.ability_method_id, self.base_abilities)
            validate_background_ability_increases(
                self.background_id, self.background_ability_increases,
                self.base_abilities, self.abilities,
            )
            normalized_species_choices = validate_species_choices(self.species_id, self.species_choices)
            if normalized_species_choices != self.species_choices:
                raise ValueError("species choices must use canonical PHB 2024 option ids")
            if self.class_choices:
                raise ValueError("class-specific selections are not part of this creation payload yet")
            class_skill_choices = list(self.class_skill_choices)
            if len(class_skill_choices) != class_data["skill_proficiencies"]["count"]:
                raise ValueError("incorrect number of PHB 2024 class skill choices")
            if len(set(class_skill_choices)) != len(class_skill_choices) or not set(class_skill_choices) <= allowed_skills:
                raise ValueError("class skill choices must be unique options granted by the selected class")
            background_skills = set(background_definition(self.background_id)["skill_proficiencies"])
            species_skills = set()
            if self.species_id == "human":
                species_skills.add(self.species_choices["skillful_skill"])
            elif self.species_id == "elf":
                species_skills.add(self.species_choices["keen_senses_skill"])
            if set(self.species_skill_choices) != species_skills:
                raise ValueError("species skill proficiencies must match the selected PHB 2024 species choices")
            expected_skills = set(class_skill_choices) | background_skills | species_skills
            if selected_skills != expected_skills:
                raise ValueError("skill proficiencies must match class, background, and species choices")
            language_rules = PHB2024_CATALOG["language_rules"]
            language_options = {option["id"] for option in language_rules["additional_options"]}
            if (
                len(self.languages) != language_rules["additional_choice_count"] + len(language_rules["required"])
                or len(set(self.languages)) != len(self.languages)
                or not set(language_rules["required"]).issubset(self.languages)
                or not set(self.languages).issubset(language_options | set(language_rules["required"]))
            ):
                raise ValueError("languages must include Common and exactly two distinct PHB 2024 choices")
            class_package = validate_equipment_package_choice(
                CLASS_RUNTIME_DEFINITIONS[self.class_.id]["starting_equipment"],
                self.starting_equipment.get("class_option", ""), source="class",
            )
            background_package_source = BACKGROUND_RECORDS[self.background_id]["equipment_packages"]
            background_package = validate_equipment_package_choice(
                background_package_source,
                self.starting_equipment.get("background_option", ""), source="background",
            )
            expected_items = class_package["items"] + background_package["items"]
            expected_gold = class_package["gold_gp"] + background_package["gold_gp"]
            if self.starting_equipment != {
                "class_option": self.starting_equipment.get("class_option"),
                "background_option": self.starting_equipment.get("background_option"),
                "class_package": class_package,
                "background_package": background_package,
                "items": expected_items,
                "gold_gp": expected_gold,
            }:
                raise ValueError("starting equipment must match the selected PHB 2024 packages")
            expected_origin_feat = background_definition(self.background_id)
            if self.origin_feat != {
                "id": expected_origin_feat["origin_feat_id"],
                "name": expected_origin_feat["origin_feat"],
                "label_pt_br": expected_origin_feat["origin_feat_label_pt_br"],
            }:
                raise ValueError("origin feat must match the selected PHB 2024 background")
        expected_saves = set(class_data["saving_throw_proficiencies"])
        actual_saves = {ability for ability, proficient in self.proficiencies.saving_throws.items() if proficient}
        if actual_saves != expected_saves:
            raise ValueError("saving throw proficiencies must match class")
        self.proficiencies.skills = {skill: True for skill in sorted(selected_skills)}
        self.proficiencies.saving_throws = {ability: True for ability in sorted(expected_saves)}
        for weapon_id, weapon in self.weapons.items():
            catalog_weapon = WEAPON_CATALOG.get(weapon_id)
            if catalog_weapon is None:
                raise ValueError("weapon must come from the authoritative catalog")
            if weapon.model_dump() != WeaponFoundation(**catalog_weapon).model_dump():
                raise ValueError("weapon definition must match the authoritative catalog")
            if WEAPON_PROFICIENCY_GROUPS.get(weapon_id) not in class_data["weapon_proficiencies"]:
                raise ValueError("class is not proficient with weapon")
        expected_features = class_features(self.class_.id, self.level)
        if self.class_features and self.class_features != expected_features:
            raise ValueError("class features do not match class level")
        validate_inventory({"inventory": self.inventory, "equipped": self.equipped})
        validate_resources({"resources": self.resources})
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
        class_data = class_definition(self.class_.id)
        first_level_hp = class_data["hit_die"] + modifiers["constitution"]
        later_level_hp = max(1, class_data["fixed_hp_per_level"] + modifiers["constitution"])
        max_hp = first_level_hp + ((self.level - 1) * later_level_hp)
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
        "experience_points": character.experience_points,
        "class": character.class_.model_dump(),
        "abilities": dict(character.abilities),
        "proficiencies": character.proficiencies.model_dump(),
        "weapons": {key: value.model_dump() for key, value in character.weapons.items()},
        "current_hp": character.current_hp,
        "class_features": list(character.class_features),
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
    if character.species_id is not None:
        state["species_id"] = character.species_id
        state["species_choices"] = dict(character.species_choices)
    if character.background_id is not None:
        state["background_id"] = character.background_id
        state["origin_feat"] = dict(character.origin_feat)
    if character.alignment_id is not None:
        state["alignment_id"] = character.alignment_id
    if character.ability_method_id is not None:
        state["ability_generation"] = {
            "method_id": character.ability_method_id,
            "base_abilities": dict(character.base_abilities),
            "background_increases": dict(character.background_ability_increases),
        }
    if character.class_skill_choices:
        state["class_skill_choices"] = list(character.class_skill_choices)
    if character.species_skill_choices:
        state["species_skill_choices"] = list(character.species_skill_choices)
    if character.languages:
        state["languages"] = list(character.languages)
    if character.class_choices:
        state["class_choices"] = dict(character.class_choices)
    if character.starting_equipment:
        state["starting_equipment"] = character.starting_equipment
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
        "experience_points": experience_for_level(selection.level),
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
        "class_features": class_features(selection.class_id, selection.level),
        "resources": {
            "second_wind": {
                "id": "second_wind",
                "current": class_resource_maximum(selection.class_id, "second_wind", selection.level),
                "maximum": class_resource_maximum(selection.class_id, "second_wind", selection.level),
                "recovery": class_resource_recovery(selection.class_id, "second_wind"),
                "recovery_amount": class_resource_recovery_amount(selection.class_id, "second_wind"),
            },
        },
    }
    if selection.level >= 2:
        character_data["resources"]["action_surge"] = {
            "id": "action_surge",
            "current": class_resource_maximum(selection.class_id, "action_surge", selection.level),
            "maximum": class_resource_maximum(selection.class_id, "action_surge", selection.level),
            "recovery": class_resource_recovery(selection.class_id, "action_surge"),
            "recovery_amount": class_resource_recovery_amount(selection.class_id, "action_surge"),
        }
    character = Character.model_validate(character_data)
    return Character.model_validate({**character_data, "current_hp": character.derived()["hp"]["max"]})


def derive_character(value: dict[str, Any] | Character) -> tuple[Character, dict[str, Any]]:
    character = value if isinstance(value, Character) else Character.model_validate(value)
    return character, character.derived()
