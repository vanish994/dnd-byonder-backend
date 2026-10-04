from __future__ import annotations

from copy import deepcopy
from typing import Any


# The registry contains only rules present in the project's 2024 source material.
# Feature identifiers are authoritative metadata until a dedicated resolver exists.
CLASS_REGISTRY: dict[str, dict[str, Any]] = {
    "fighter": {
        "id": "fighter",
        "label": "Guerreiro",
        "hit_die": 10,
        "fixed_hp_per_level": 6,
        "primary_abilities": ["strength", "dexterity"],
        "saving_throw_proficiencies": ["strength", "constitution"],
        "skill_proficiencies": {
            "count": 2,
            "options": [
                "acrobatics", "animal_handling", "athletics", "history",
                "insight", "intimidation", "persuasion", "perception", "survival",
            ],
        },
        "weapon_proficiencies": ["simple", "martial"],
        "armor_proficiencies": ["light", "medium", "heavy", "shields"],
        "starting_equipment": {
            "A": ["chain_mail", "greatsword", "flail", "javelin", "dungeoneers_pack", "gp:4"],
            "B": ["studded_leather", "scimitar", "shortsword", "longbow", "arrows:20", "quiver", "dungeoneers_pack", "gp:11"],
            "C": ["gp:155"],
        },
        "features_by_level": {
            1: ["fighting_style", "second_wind", "weapon_mastery"],
            2: ["action_surge", "tactical_mind"],
            3: ["fighter_subclass"],
            4: ["ability_score_improvement"],
            5: ["extra_attack", "tactical_shift"],
            6: ["ability_score_improvement"],
            7: ["fighter_subclass_feature"],
            8: ["ability_score_improvement"],
            9: ["indomitable", "tactical_master"],
            10: ["fighter_subclass_feature"],
            11: ["two_extra_attacks"],
            12: ["ability_score_improvement"],
            13: ["indomitable", "studied_attacks"],
            14: ["ability_score_improvement"],
            15: ["fighter_subclass_feature"],
            16: ["ability_score_improvement"],
            17: ["action_surge", "indomitable"],
            18: ["fighter_subclass_feature"],
            19: ["epic_boon"],
            20: ["three_extra_attacks"],
        },
        "resources_by_level": {
            "second_wind": {
                "recovery": "short_rest",
                "recovery_amount": 1,
                "maximum_by_level": {
                    1: 2, 2: 2, 3: 2, 4: 3, 5: 3, 6: 3, 7: 3, 8: 3, 9: 3,
                    10: 4, 11: 4, 12: 4, 13: 4, 14: 4, 15: 4, 16: 4,
                    17: 4, 18: 4, 19: 4, 20: 4,
                },
            },
            "action_surge": {
                "recovery": "short_rest",
                "recovery_amount": 1,
                "maximum_by_level": {
                    2: 1, 3: 1, 4: 1, 5: 1, 6: 1, 7: 1, 8: 1, 9: 1,
                    10: 1, 11: 1, 12: 1, 13: 1, 14: 1, 15: 1, 16: 1,
                    17: 2, 18: 2, 19: 2, 20: 2,
                },
            },
        },
        "attacks_by_level": {1: 1, 5: 2, 11: 3, 20: 4},
    },
}


def class_definition(class_id: str) -> dict[str, Any]:
    definition = CLASS_REGISTRY.get(class_id)
    if definition is None:
        raise ValueError(f"unsupported class: {class_id}")
    return deepcopy(definition)


def class_features(class_id: str, level: int) -> list[str]:
    definition = class_definition(class_id)
    features: list[str] = []
    for feature_level in range(1, level + 1):
        for feature in definition["features_by_level"].get(feature_level, []):
            if feature not in features:
                features.append(feature)
    return features


def class_resource_maximum(class_id: str, resource_id: str, level: int) -> int:
    definition = class_definition(class_id)
    resource = definition.get("resources_by_level", {}).get(resource_id)
    if resource is None:
        raise ValueError(f"unsupported class resource: {resource_id}")
    available = [item_level for item_level in resource["maximum_by_level"] if item_level <= level]
    if not available:
        raise ValueError(f"resource is not available at level {level}")
    return resource["maximum_by_level"][max(available)]


def class_resource_recovery(class_id: str, resource_id: str) -> str:
    definition = class_definition(class_id)
    resource = definition.get("resources_by_level", {}).get(resource_id)
    if resource is None:
        raise ValueError(f"unsupported class resource: {resource_id}")
    return resource["recovery"]


def class_resource_recovery_amount(class_id: str, resource_id: str) -> int | None:
    definition = class_definition(class_id)
    resource = definition.get("resources_by_level", {}).get(resource_id)
    if resource is None:
        raise ValueError(f"unsupported class resource: {resource_id}")
    return resource.get("recovery_amount")


def class_attack_count(class_id: str, level: int) -> int:
    definition = class_definition(class_id)
    available = [item_level for item_level in definition.get("attacks_by_level", {}) if item_level <= level]
    if not available:
        return 1
    return definition["attacks_by_level"][max(available)]
