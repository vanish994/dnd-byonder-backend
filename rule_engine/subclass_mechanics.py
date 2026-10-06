"""Deterministic PHB 2024 subclass feature resolvers.

Only effects explicitly confirmed in the project's PHB 2024 source are bound here.
Narrative input and client-provided dice/results never enter these resolvers.
"""
from __future__ import annotations

from typing import Any, Callable

from rule_engine.classes import class_resource_maximum, class_subclass_options
from rule_engine.conditions import add_condition, remove_condition
from rule_engine.dice import roll_dice
from rule_engine.resources import consume_resource, define_resource


FEATURE_SUBCLASSES = {
    "physicians_touch": {"class_id": "monk", "subclasses": {"warrior_of_mercy"}, "level": 6},
    "shadow_step": {"class_id": "monk", "subclasses": {"warrior_of_shadow"}, "level": 6},
    "elemental_burst": {"class_id": "monk", "subclasses": {"warrior_of_the_elements"}, "level": 6},
    "wholeness_of_body": {"class_id": "monk", "subclasses": {"warrior_of_the_open_hand"}, "level": 6},
    "misty_escape": {"class_id": "warlock", "subclasses": {"archfey_patron"}, "level": 6},
    "dark_ones_own_luck": {"class_id": "warlock", "subclasses": {"fiend_patron"}, "level": 6},
    "bend_luck": {"class_id": "sorcerer", "subclasses": {"wild_magic_sorcery"}, "level": 6},
    "bastion_of_law": {"class_id": "sorcerer", "subclasses": {"clockwork_sorcery"}, "level": 6},
    "projected_ward": {"class_id": "wizard", "subclasses": {"abjurer"}, "level": 6},
    "expert_divination": {"class_id": "wizard", "subclasses": {"diviner"}, "level": 6},
    "sculpt_spells": {"class_id": "wizard", "subclasses": {"evoker"}, "level": 6},
    "phantasmal_creatures": {"class_id": "wizard", "subclasses": {"illusionist"}, "level": 6},
}

SUBCLASS_SPELLS_BY_LEVEL = {
    ("cleric", "life_domain", 5): ["mass_healing_word", "revivify"],
    ("cleric", "light_domain", 5): ["daylight", "fireball"],
    ("cleric", "trickery_domain", 5): ["hypnotic_pattern", "nondetection"],
    ("cleric", "war_domain", 5): ["crusaders_mantle", "spirit_guardians"],
    ("druid", "circle_of_the_moon", 5): ["conjure_animals"],
    ("druid", "circle_of_the_sea", 5): ["lightning_bolt", "water_breathing"],
    ("paladin", "oath_of_devotion", 5): ["aid", "zone_of_truth"],
    ("paladin", "oath_of_glory", 5): ["enhance_ability", "magic_weapon"],
    ("paladin", "oath_of_the_ancients", 5): ["misty_step", "moonbeam"],
    ("paladin", "oath_of_vengeance", 5): ["hold_person", "misty_step"],
    ("ranger", "fey_wanderer", 5): ["misty_step"],
    ("ranger", "gloom_stalker", 5): ["rope_trick"],
    ("sorcerer", "aberrant_sorcery", 5): ["hunger_of_hadar", "sending"],
    ("sorcerer", "clockwork_sorcery", 5): ["dispel_magic", "protection_from_energy"],
    ("sorcerer", "draconic_sorcery", 5): ["fear", "fly"],
    ("warlock", "archfey_patron", 5): ["blink", "plant_growth"],
    ("warlock", "celestial_patron", 5): ["daylight", "revivify"],
    ("warlock", "fiend_patron", 5): ["fireball", "stinking_cloud"],
    ("warlock", "great_old_one_patron", 5): ["clairvoyance", "hunger_of_hadar"],
}


def subclass_prepared_spells(class_id: str, subclass_id: str | None, level: int) -> list[str]:
    if subclass_id is None:
        return []
    spells: list[str] = []
    for (entry_class, entry_subclass, entry_level), entry_spells in SUBCLASS_SPELLS_BY_LEVEL.items():
        if entry_class == class_id and entry_subclass == subclass_id and entry_level <= level:
            spells.extend(spell for spell in entry_spells if spell not in spells)
    return spells


def _ability_modifier(character: dict[str, Any], ability: str) -> int:
    score = character.get("abilities", {}).get(ability)
    if not isinstance(score, int):
        raise ValueError(f"character is missing {ability} for subclass feature")
    return score // 2 - 5


def _validate_feature(character: dict[str, Any], feature_id: str) -> dict[str, Any]:
    spec = FEATURE_SUBCLASSES.get(feature_id)
    if spec is None:
        raise ValueError("subclass feature is not bound to a deterministic resolver")
    class_data = character.get("class") or {}
    if class_data.get("id") != spec["class_id"]:
        raise ValueError("subclass feature does not belong to the character class")
    subclass_id = character.get("subclass_id")
    if subclass_id not in class_subclass_options(spec["class_id"]):
        raise ValueError("character has no valid PHB 2024 subclass selection")
    if subclass_id not in spec["subclasses"]:
        raise ValueError("subclass feature does not belong to the selected subclass")
    if character.get("level", 0) < spec["level"]:
        raise ValueError("subclass feature is not available at this level")
    return spec


def _ensure_subclass_resource(character: dict[str, Any], resource_id: str, maximum: int, recovery: str) -> dict[str, Any]:
    resources = character.setdefault("resources", {})
    resource = resources.get(resource_id)
    if resource is None:
        resource = define_resource(character, resource_id, maximum=maximum, recovery=recovery)
    return resource


def resolve_subclass_feature(
    character: dict[str, Any],
    *,
    feature_id: str,
    mode: str | None = None,
    target: dict[str, Any] | None = None,
    randbelow: Callable[[int], int] | None = None,
) -> dict[str, Any]:
    """Resolve one explicit feature invocation; unsupported mechanics fail closed."""
    _validate_feature(character, feature_id)
    target = target or {}
    level = int(character["level"])

    if feature_id == "physicians_touch":
        if mode == "harm":
            condition = add_condition(target, condition_id="poisoned", source_id=feature_id, duration={"kind": "turns", "remaining": 2})
            return {"feature_id": feature_id, "mode": mode, "condition": condition}
        if mode == "heal":
            removed = []
            for condition_id in ("blinded", "deafened", "paralyzed", "poisoned", "stunned"):
                removed.extend([condition_id] * remove_condition(target, condition_id=condition_id))
            return {"feature_id": feature_id, "mode": mode, "removed_conditions": removed}
        raise ValueError("physicians_touch requires mode harm or heal")

    if feature_id == "shadow_step":
        return {"feature_id": feature_id, "teleport_range": 60, "advantage_next_melee_attack": True}

    if feature_id == "elemental_burst":
        consume_resource(character, "focus_points", 2)
        damage_roll = roll_dice("3d8", randbelow=randbelow)
        return {"feature_id": feature_id, "focus_spent": 2, "damage_type": mode or "acid", "damage": sum(damage_roll["rolls"]), "save": "dexterity"}

    if feature_id == "wholeness_of_body":
        maximum = class_resource_maximum("monk", "wholeness_of_body", level, ability_modifier=_ability_modifier(character, "wisdom"))
        _ensure_subclass_resource(character, "wholeness_of_body", maximum, "long_rest")
        consume_resource(character, "wholeness_of_body")
        healing = roll_dice("d8", randbelow=randbelow)["rolls"][0] + _ability_modifier(character, "wisdom")
        return {"feature_id": feature_id, "healing": max(1, healing), "resource_current": character["resources"]["wholeness_of_body"]["current"]}

    if feature_id == "bend_luck":
        consume_resource(character, "sorcery_points")
        roll = roll_dice("d4", randbelow=randbelow)["rolls"][0]
        return {"feature_id": feature_id, "sorcery_points_spent": 1, "modifier": roll if mode != "penalty" else -roll}

    if feature_id == "dark_ones_own_luck":
        maximum = max(1, _ability_modifier(character, "charisma"))
        _ensure_subclass_resource(character, "dark_ones_own_luck", maximum, "long_rest")
        consume_resource(character, "dark_ones_own_luck")
        roll = roll_dice("d10", randbelow=randbelow)["rolls"][0]
        return {"feature_id": feature_id, "bonus": roll, "resource_current": character["resources"]["dark_ones_own_luck"]["current"]}

    if feature_id == "bastion_of_law":
        points = int(mode or 1)
        if not 1 <= points <= 5:
            raise ValueError("bastion_of_law requires 1 to 5 sorcery points")
        consume_resource(character, "sorcery_points", points)
        return {"feature_id": feature_id, "sorcery_points_spent": points, "ward_dice": points, "ward_die": "d8"}

    if feature_id == "misty_escape":
        return {"feature_id": feature_id, "trigger": "damage_taken", "reaction": True, "spell": "misty_step", "uses_spell_slot": False}

    if feature_id in {"projected_ward", "expert_divination", "sculpt_spells", "phantasmal_creatures"}:
        return {"feature_id": feature_id, "status": "validated_feature_trigger", "target": target.get("id")}

    raise ValueError("subclass feature resolver is incomplete")
