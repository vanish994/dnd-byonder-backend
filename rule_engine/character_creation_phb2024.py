from __future__ import annotations

import uuid
from typing import Any

from game.contracts import PHB2024GuidedCharacterRequest
from rule_engine.character import Character, class_features
from rule_engine.classes import class_resource_maximum, class_resource_recovery, class_resource_recovery_amount
from rule_engine.character_creation_catalog import (
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
from rule_engine.progression import experience_for_level


def build_phb2024_character(selection: PHB2024GuidedCharacterRequest) -> Character:
    """Build a level-1 character from strictly validated PHB 2024 choices."""
    if selection.level != 1:
        raise ValueError("PHB 2024 character creation currently supports level 1 only")
    if selection.class_id not in CLASS_RECORDS:
        raise ValueError("unsupported PHB 2024 class")
    if selection.species_id not in SPECIES_RECORDS:
        raise ValueError("unsupported PHB 2024 species")
    if selection.background_id not in BACKGROUND_RECORDS:
        raise ValueError("unsupported PHB 2024 background")
    if selection.class_choices:
        raise ValueError("class-specific selections are not part of this creation payload yet")

    class_data = CLASS_RUNTIME_DEFINITIONS[selection.class_id]
    class_definition = class_data
    skill_choice_rules = class_definition["skill_proficiencies"]
    if len(selection.skills) != skill_choice_rules["count"]:
        raise ValueError("incorrect number of class skill choices")
    if len(set(selection.skills)) != len(selection.skills):
        raise ValueError("class skill choices must be unique")
    if not set(selection.skills).issubset(skill_choice_rules["options"]):
        raise ValueError("unsupported class skill choice")

    validate_ability_assignment(selection.ability_method_id, selection.base_abilities)
    validate_background_ability_increases(
        selection.background_id,
        selection.background_ability_increases,
        selection.base_abilities,
        selection.abilities,
    )
    species_choices = validate_species_choices(selection.species_id, selection.species_choices)

    alignment_ids = {
        "lawful_good", "neutral_good", "chaotic_good", "lawful_neutral", "neutral",
        "chaotic_neutral", "lawful_evil", "neutral_evil", "chaotic_evil",
    }
    if selection.alignment_id not in alignment_ids:
        raise ValueError("unsupported PHB 2024 alignment")

    language_rules = PHB2024_CATALOG["language_rules"]
    language_options = {option["id"] for option in language_rules["additional_options"]}
    if (
        len(selection.language_choices) != language_rules["additional_choice_count"]
        or len(set(selection.language_choices)) != len(selection.language_choices)
        or not set(selection.language_choices).issubset(language_options)
    ):
        raise ValueError("choose exactly two distinct additional PHB 2024 languages")
    languages = list(language_rules["required"]) + list(selection.language_choices)

    background = background_definition(selection.background_id)
    background_skills = set(background["skill_proficiencies"])
    species_skills: set[str] = set()
    if selection.species_id == "human":
        species_skills.add(species_choices["skillful_skill"])
    elif selection.species_id == "elf":
        species_skills.add(species_choices["keen_senses_skill"])
    all_skills = set(selection.skills) | background_skills | species_skills

    class_package = validate_equipment_package_choice(
        class_data["starting_equipment"], selection.class_equipment_option, source="class",
    )
    background_package_source = BACKGROUND_RECORDS[selection.background_id]["equipment_packages"]
    background_package = validate_equipment_package_choice(
        background_package_source, selection.background_equipment_option, source="background",
    )
    starting_equipment = {
        "class_option": selection.class_equipment_option.upper(),
        "background_option": selection.background_equipment_option.upper(),
        "class_package": class_package,
        "background_package": background_package,
        "items": class_package["items"] + background_package["items"],
        "gold_gp": class_package["gold_gp"] + background_package["gold_gp"],
    }
    origin_feat = {
        "id": background["origin_feat_id"],
        "name": background["origin_feat"],
        "label_pt_br": background["origin_feat_label_pt_br"],
    }
    resources: dict[str, Any] = {}
    if selection.class_id == "fighter":
        resources["second_wind"] = {
            "id": "second_wind",
            "current": class_resource_maximum("fighter", "second_wind", 1),
            "maximum": class_resource_maximum("fighter", "second_wind", 1),
            "recovery": class_resource_recovery("fighter", "second_wind"),
            "recovery_amount": class_resource_recovery_amount("fighter", "second_wind"),
        }

    character_data = {
        "id": f"character-{uuid.uuid4().hex}",
        "name": selection.name,
        "level": 1,
        "experience_points": experience_for_level(1),
        "class": {"id": selection.class_id, "level": 1},
        "abilities": dict(selection.abilities),
        "proficiencies": {
            "skills": {skill: True for skill in sorted(all_skills)},
            "saving_throws": {ability: True for ability in class_data["saving_throw_proficiencies"]},
        },
        "weapons": {},
        "inventory": {},
        "equipped": {"weapon": None, "armor": None},
        "class_features": class_features(selection.class_id, 1),
        "resources": resources,
        "species_id": selection.species_id,
        "species_choices": species_choices,
        "background_id": selection.background_id,
        "alignment_id": selection.alignment_id,
        "ability_method_id": selection.ability_method_id,
        "base_abilities": dict(selection.base_abilities),
        "background_ability_increases": dict(selection.background_ability_increases),
        "class_skill_choices": list(selection.skills),
        "species_skill_choices": sorted(species_skills),
        "languages": languages,
        "class_choices": {},
        "starting_equipment": starting_equipment,
        "origin_feat": origin_feat,
    }
    character = Character.model_validate(character_data)
    return Character.model_validate({**character_data, "current_hp": character.derived()["hp"]["max"]})
