from __future__ import annotations

from copy import deepcopy
from typing import Any

from rule_engine.character_creation_catalog import CLASS_RUNTIME_DEFINITIONS


# The registry contains only rules confirmed in the project's PHB 2024 source.
CLASS_REGISTRY: dict[str, dict[str, Any]] = {
    "fighter": {
        "id": "fighter", "label": "Guerreiro", "hit_die": 10,
        "fixed_hp_per_level": 6, "primary_abilities": ["strength", "dexterity"],
        "saving_throw_proficiencies": ["strength", "constitution"],
        "skill_proficiencies": {"count": 2, "options": ["acrobatics", "animal_handling", "athletics", "history", "insight", "intimidation", "persuasion", "perception", "survival"]},
        "weapon_proficiencies": ["simple", "martial"],
        "armor_proficiencies": ["light", "medium", "heavy", "shields"],
        "features_by_level": {
            1: ["fighting_style", "second_wind", "weapon_mastery"],
            2: ["action_surge", "tactical_mind"], 3: ["fighter_subclass"],
            4: ["ability_score_improvement"], 5: ["extra_attack", "tactical_shift"],
            6: ["ability_score_improvement"], 7: ["fighter_subclass_feature"],
            8: ["ability_score_improvement"], 9: ["indomitable", "tactical_master"],
            10: ["fighter_subclass_feature"], 11: ["two_extra_attacks"],
            12: ["ability_score_improvement"], 13: ["indomitable", "studied_attacks"],
            14: ["ability_score_improvement"], 15: ["fighter_subclass_feature"],
            16: ["ability_score_improvement"], 17: ["action_surge", "indomitable"],
            18: ["fighter_subclass_feature"], 19: ["epic_boon"], 20: ["three_extra_attacks"],
        },
        "resources_by_level": {
            "second_wind": {"recovery": "short_rest", "recovery_amount": 1, "maximum_by_level": {1: 2, 2: 2, 3: 2, 4: 3, 5: 3, 6: 3, 7: 3, 8: 3, 9: 3, 10: 4, 11: 4, 12: 4, 13: 4, 14: 4, 15: 4, 16: 4, 17: 4, 18: 4, 19: 4, 20: 4}},
            "action_surge": {"recovery": "short_rest", "recovery_amount": 1, "maximum_by_level": {2: 1, 3: 1, 4: 1, 5: 1, 6: 1, 7: 1, 8: 1, 9: 1, 10: 1, 11: 1, 12: 1, 13: 1, 14: 1, 15: 1, 16: 1, 17: 2, 18: 2, 19: 2, 20: 2}},
        },
        "attacks_by_level": {1: 1, 5: 2, 11: 3, 20: 4},
        "subclass_options": ["battle_master", "champion", "eldritch_knight", "psi_warrior"],
    },
}

# Progression records below are limited to PHB 2024 levels 1–3 confirmed in the
# project's Players_Handbook_2024.txt. Unresolved subclass effects remain
# explicit options and are not silently converted into mechanics.
_PROGRESSIONS: dict[str, dict[str, Any]] = {
    "barbarian": {"features_by_level": {1: ["rage", "unarmored_defense", "weapon_mastery"], 2: ["danger_sense", "reckless_attack"], 3: ["primal_knowledge", "barbarian_subclass"]}, "resources_by_level": {"rages": {"recovery": "short_rest_and_long_rest", "maximum_by_level": {1: 2, 2: 2, 3: 3}}, "rage_damage": {"display_only": True, "maximum_by_level": {1: 2, 2: 2, 3: 2}}}, "subclass_options": ["path_of_the_berserker", "path_of_the_wild_heart", "path_of_the_world_tree", "path_of_the_zealot"]},
    "bard": {"features_by_level": {1: ["bardic_inspiration", "spellcasting"], 2: ["expertise", "jack_of_all_trades"], 3: ["bard_subclass"]}, "resources_by_level": {"bardic_inspiration": {"recovery": "long_rest", "maximum_formula": "max(1, charisma_modifier)"}}, "spellcasting_by_level": {1: {"cantrips": 2, "prepared": 4, "slots": {1: 2}}, 2: {"cantrips": 2, "prepared": 5, "slots": {1: 3}}, 3: {"cantrips": 2, "prepared": 6, "slots": {1: 4, 2: 2}}}, "spellcasting_ability": "charisma", "subclass_options": ["college_of_dance", "college_of_glamour", "college_of_lore", "college_of_valor"]},
    "cleric": {"features_by_level": {1: ["spellcasting", "divine_order"], 2: ["channel_divinity"], 3: ["cleric_subclass"]}, "resources_by_level": {"channel_divinity": {"recovery": "short_rest_and_long_rest", "maximum_by_level": {2: 2, 3: 2}}}, "spellcasting_by_level": {1: {"cantrips": 3, "prepared": 4, "slots": {1: 2}}, 2: {"cantrips": 3, "prepared": 5, "slots": {1: 3}}, 3: {"cantrips": 3, "prepared": 6, "slots": {1: 4, 2: 2}}}, "spellcasting_ability": "wisdom", "subclass_options": ["life_domain", "light_domain", "trickery_domain", "war_domain"]},
    "druid": {"features_by_level": {1: ["spellcasting", "druidic", "primal_order"], 2: ["wild_shape", "wild_companion"], 3: ["druid_subclass"]}, "resources_by_level": {"wild_shape": {"recovery": "short_rest_and_long_rest", "maximum_by_level": {2: 2, 3: 2}}}, "spellcasting_by_level": {1: {"cantrips": 2, "prepared": 4, "slots": {1: 2}}, 2: {"cantrips": 2, "prepared": 5, "slots": {1: 3}}, 3: {"cantrips": 2, "prepared": 6, "slots": {1: 4, 2: 2}}}, "spellcasting_ability": "wisdom", "subclass_options": ["circle_of_the_land", "circle_of_the_moon", "circle_of_the_sea", "circle_of_the_stars"]},
    "monk": {"features_by_level": {1: ["artes_marciais", "defesa_sem_armadura"], 2: ["monks_focus", "uncanny_metabolism", "unarmored_movement"], 3: ["deflect_attacks", "monk_subclass"]}, "resources_by_level": {"focus_points": {"recovery": "short_rest_and_long_rest", "maximum_by_level": {2: 2, 3: 3}}}, "subclass_options": ["warrior_of_mercy", "warrior_of_shadow", "warrior_of_the_elements", "warrior_of_the_open_hand"]},
    "paladin": {"features_by_level": {1: ["lay_on_hands", "spellcasting", "weapon_mastery"], 2: ["fighting_style", "paladins_smite"], 3: ["channel_divinity", "paladin_subclass"]}, "resources_by_level": {"lay_on_hands": {"recovery": "long_rest", "maximum_formula": "5 * level"}, "channel_divinity": {"recovery": "short_rest_and_long_rest", "maximum_by_level": {3: 2}}}, "spellcasting_by_level": {1: {"cantrips": 0, "prepared": 2, "slots": {1: 2}}, 2: {"cantrips": 0, "prepared": 3, "slots": {1: 2}}, 3: {"cantrips": 0, "prepared": 4, "slots": {1: 3}}}, "spellcasting_ability": "charisma", "subclass_options": ["oath_of_devotion", "oath_of_glory", "oath_of_the_ancients", "oath_of_vengeance"]},
    "ranger": {"features_by_level": {1: ["conjuracao", "inimigo_favorito", "maestria_em_armas"], 2: ["deft_explorer", "fighting_style"], 3: ["ranger_subclass"]}, "resources_by_level": {"favored_enemy": {"recovery": "long_rest", "maximum_by_level": {1: 2, 2: 2, 3: 2}}}, "spellcasting_by_level": {1: {"cantrips": 0, "prepared": 2, "slots": {1: 2}}, 2: {"cantrips": 0, "prepared": 3, "slots": {1: 2}}, 3: {"cantrips": 0, "prepared": 4, "slots": {1: 3}}}, "spellcasting_ability": "wisdom", "subclass_options": ["beast_master", "fey_wanderer", "gloom_stalker", "hunter"]},
    "rogue": {"features_by_level": {1: ["expertise", "sneak_attack", "thieves_cant", "weapon_mastery"], 2: ["cunning_action"], 3: ["rogue_subclass", "steady_aim"]}, "subclass_options": ["arcane_trickster", "assassin", "soulknife", "thief"]},
    "sorcerer": {"features_by_level": {1: ["spellcasting", "innate_sorcery"], 2: ["font_of_magic", "metamagic"], 3: ["sorcerer_subclass"]}, "resources_by_level": {"innate_sorcery": {"recovery": "long_rest", "maximum_by_level": {1: 2, 2: 2, 3: 2}}, "sorcery_points": {"recovery": "long_rest", "maximum_by_level": {2: 2, 3: 3}}}, "spellcasting_by_level": {1: {"cantrips": 4, "prepared": 2, "slots": {1: 2}}, 2: {"cantrips": 4, "prepared": 4, "slots": {1: 3}}, 3: {"cantrips": 4, "prepared": 6, "slots": {1: 4, 2: 2}}}, "spellcasting_ability": "charisma", "subclass_options": ["aberrant_sorcery", "clockwork_sorcery", "draconic_sorcery", "wild_magic_sorcery"]},
    "warlock": {"features_by_level": {1: ["eldritch_invocations", "pact_magic"], 2: ["magical_cunning"], 3: ["warlock_subclass"]}, "resources_by_level": {"eldritch_invocations": {"display_only": True, "maximum_by_level": {1: 1, 2: 3, 3: 3}}, "pact_slots": {"recovery": "short_rest_and_long_rest", "maximum_by_level": {1: 1, 2: 2, 3: 2}}}, "spellcasting_by_level": {1: {"cantrips": 2, "prepared": 2, "slots": {1: 1}}, 2: {"cantrips": 2, "prepared": 3, "slots": {1: 2}}, 3: {"cantrips": 2, "prepared": 4, "slots": {2: 2}}}, "spellcasting_ability": "charisma", "subclass_options": ["archfey_patron", "celestial_patron", "fiend_patron", "great_old_one_patron"]},
    "wizard": {"features_by_level": {1: ["conjuracao_spellcasting", "adepto_ritualista_ritual_adept", "recuperacao_arcana_arcane_recovery"], 2: ["scholar"], 3: ["wizard_subclass"]}, "spellcasting_by_level": {1: {"cantrips": 3, "prepared": 4, "slots": {1: 2}}, 2: {"cantrips": 3, "prepared": 5, "slots": {1: 3}}, 3: {"cantrips": 3, "prepared": 6, "slots": {1: 4, 2: 2}}}, "spellcasting_ability": "intelligence", "subclass_options": ["abjurer", "diviner", "evoker", "illusionist"]},
}

# PHB 2024 levels 4–6. Subclass effects are stored as conditional metadata;
# the Rule Engine never selects one without an explicit character choice.
_PROGRESSIONS_4_6: dict[str, dict[str, Any]] = {
    "barbarian": {"features_by_level": {4: ["ability_score_improvement"], 5: ["extra_attack", "fast_movement"], 6: ["barbarian_subclass_feature"]}, "attacks_by_level": {5: 2}, "resources_by_level": {"rages": {"recovery": "short_rest_and_long_rest", "maximum_by_level": {4: 3, 5: 3, 6: 4}}, "rage_damage": {"display_only": True, "maximum_by_level": {4: 2, 5: 2, 6: 2}}}, "subclass_features_by_level": {6: ["mindless_rage", "aspect_of_the_wilds", "branches_of_the_tree", "fanatical_focus"]}},
    "bard": {"features_by_level": {4: ["ability_score_improvement"], 5: ["font_of_inspiration"], 6: ["bard_subclass_feature"]}, "spellcasting_by_level": {4: {"cantrips": 3, "prepared": 7, "slots": {1: 4, 2: 3}}, 5: {"cantrips": 3, "prepared": 9, "slots": {1: 4, 2: 3, 3: 2}}, 6: {"cantrips": 3, "prepared": 10, "slots": {1: 4, 2: 3, 3: 3}}}, "resources_by_level": {"bardic_inspiration": {"recovery": "long_rest", "maximum_formula": "max(1, charisma_modifier)", "die_by_level": {4: "d6", 5: "d8", 6: "d8"}}}, "subclass_features_by_level": {6: ["inspiring_movement", "tandem_footwork", "mantle_of_majesty", "magical_discoveries", "bard_extra_attack"]}},
    "cleric": {"features_by_level": {4: ["ability_score_improvement"], 5: ["sear_undead"], 6: ["cleric_subclass_feature"]}, "resources_by_level": {"channel_divinity": {"recovery": "short_rest_and_long_rest", "maximum_by_level": {4: 2, 5: 2, 6: 3}}}, "spellcasting_by_level": {4: {"cantrips": 4, "prepared": 7, "slots": {1: 4, 2: 3, 3: 2}}, 5: {"cantrips": 4, "prepared": 9, "slots": {1: 4, 2: 3, 3: 3}}, 6: {"cantrips": 4, "prepared": 10, "slots": {1: 4, 2: 3, 3: 3}}}, "subclass_features_by_level": {5: ["life_domain_spells", "light_domain_spells", "trickery_domain_spells", "war_domain_spells"], 6: ["blessed_healer", "warding_flare_improvement", "tricksters_transposition", "war_gods_blessing"]}},
    "druid": {"features_by_level": {4: ["ability_score_improvement"], 5: ["wild_resurgence"], 6: ["druid_subclass_feature"]}, "resources_by_level": {"wild_shape": {"recovery": "short_rest_and_long_rest", "maximum_by_level": {4: 2, 5: 2, 6: 3}}}, "spellcasting_by_level": {4: {"cantrips": 3, "prepared": 7, "slots": {1: 4, 2: 3, 3: 0}}, 5: {"cantrips": 3, "prepared": 9, "slots": {1: 4, 2: 3, 3: 2}}, 6: {"cantrips": 3, "prepared": 10, "slots": {1: 4, 2: 3, 3: 3}}}, "subclass_features_by_level": {5: ["circle_spells"], 6: ["natural_recovery", "improved_circle_forms", "aquatic_affinity", "cosmic_omen"]}},
    "fighter": {"subclass_features_by_level": {4: ["combat_superiority", "improved_critical", "eldritch_knight_spellcasting", "psionic_power"]}},
    "monk": {"features_by_level": {4: ["ability_score_improvement", "slow_fall"], 5: ["extra_attack", "stunning_strike"], 6: ["empowered_strikes", "monk_subclass_feature"]}, "attacks_by_level": {5: 2}, "resources_by_level": {"focus_points": {"recovery": "short_rest_and_long_rest", "maximum_by_level": {4: 4, 5: 5, 6: 6}}, "wholeness_of_body": {"recovery": "long_rest", "maximum_formula": "max(1, wisdom_modifier)", "display_only": True}}, "subclass_features_by_level": {6: ["physicians_touch", "shadow_step", "elemental_burst", "wholeness_of_body"]}},
    "paladin": {"features_by_level": {4: ["ability_score_improvement"], 5: ["extra_attack", "faithful_steed"], 6: ["aura_of_protection"]}, "attacks_by_level": {5: 2}, "resources_by_level": {"lay_on_hands": {"recovery": "long_rest", "maximum_formula": "5 * level"}, "channel_divinity": {"recovery": "short_rest_and_long_rest", "maximum_by_level": {4: 2, 5: 2, 6: 2}}, "paladins_smite": {"display_only": True, "maximum_by_level": {2: 1}}}, "spellcasting_by_level": {4: {"cantrips": 0, "prepared": 5, "slots": {1: 3}}, 5: {"cantrips": 0, "prepared": 6, "slots": {1: 4, 2: 2}}, 6: {"cantrips": 0, "prepared": 6, "slots": {1: 4, 2: 2}}}, "subclass_features_by_level": {5: ["oath_spells"]}},
    "ranger": {"features_by_level": {4: ["ability_score_improvement"], 5: ["extra_attack"], 6: ["roving"]}, "attacks_by_level": {5: 2}, "resources_by_level": {"favored_enemy": {"recovery": "long_rest", "maximum_by_level": {4: 2, 5: 3, 6: 3}}}, "spellcasting_by_level": {4: {"cantrips": 0, "prepared": 5, "slots": {1: 3}}, 5: {"cantrips": 0, "prepared": 6, "slots": {1: 4, 2: 2}}, 6: {"cantrips": 0, "prepared": 6, "slots": {1: 4, 2: 2}}}},
    "rogue": {"features_by_level": {4: ["ability_score_improvement"], 5: ["cunning_strike", "uncanny_dodge"], 6: ["expertise"]}, "subclass_features_by_level": {5: ["soulknife_energy_dice_progression"]}},
    "sorcerer": {"features_by_level": {4: ["ability_score_improvement"], 5: ["sorcerous_restoration"], 6: ["sorcerer_subclass_feature"]}, "resources_by_level": {"innate_sorcery": {"recovery": "long_rest", "maximum_by_level": {4: 2, 5: 2, 6: 2}}, "sorcery_points": {"recovery": "long_rest", "maximum_by_level": {4: 4, 5: 5, 6: 6}}, "sorcerous_restoration": {"display_only": True, "maximum_by_level": {5: 2, 6: 3}}}, "spellcasting_by_level": {4: {"cantrips": 5, "prepared": 7, "slots": {1: 4, 2: 3}}, 5: {"cantrips": 5, "prepared": 9, "slots": {1: 4, 2: 3, 3: 2}}, 6: {"cantrips": 5, "prepared": 10, "slots": {1: 4, 2: 3, 3: 3}}}, "subclass_features_by_level": {5: ["psionic_spells", "clockwork_spells", "draconic_spells"], 6: ["psionic_sorcery", "psychic_defenses", "bastion_of_law", "elemental_affinity", "bend_luck"]}},
    "warlock": {"features_by_level": {4: ["ability_score_improvement"], 5: ["eldritch_invocations"], 6: ["warlock_subclass_feature"]}, "resources_by_level": {"eldritch_invocations": {"display_only": True, "maximum_by_level": {4: 3, 5: 5, 6: 5}}, "pact_slots": {"recovery": "short_rest_and_long_rest", "maximum_by_level": {4: 2, 5: 2, 6: 2}}}, "spellcasting_by_level": {4: {"cantrips": 3, "prepared": 5, "slots": {2: 2}}, 5: {"cantrips": 3, "prepared": 6, "slots": {3: 2}}, 6: {"cantrips": 3, "prepared": 7, "slots": {3: 2}}}, "subclass_features_by_level": {5: ["archfey_spells", "celestial_spells", "fiend_spells", "great_old_one_spells"], 6: ["misty_escape", "radiant_soul", "dark_ones_own_luck", "clairvoyant_combatant"]}},
    "wizard": {"features_by_level": {4: ["ability_score_improvement"], 5: ["memorize_spell"], 6: ["wizard_subclass_feature"]}, "spellcasting_by_level": {4: {"cantrips": 4, "prepared": 7, "slots": {1: 4, 2: 3}}, 5: {"cantrips": 4, "prepared": 9, "slots": {1: 4, 2: 3, 3: 2}}, 6: {"cantrips": 4, "prepared": 10, "slots": {1: 4, 2: 3, 3: 3}}}, "subclass_features_by_level": {6: ["projected_ward", "expert_divination", "sculpt_spells", "phantasmal_creatures"]}},
}

for _class_id, _definition in CLASS_RUNTIME_DEFINITIONS.items():
    if _class_id != "fighter":
        CLASS_REGISTRY[_class_id] = deepcopy(_definition)
    progression = _PROGRESSIONS.get(_class_id, {})
    extension = _PROGRESSIONS_4_6.get(_class_id, {})
    for key in ("features_by_level", "resources_by_level", "subclass_features_by_level", "attacks_by_level"):
        merged_values = deepcopy(CLASS_REGISTRY[_class_id].get(key, {}))
        for section in (progression.get(key, {}), extension.get(key, {})):
            for item_id, item_value in section.items():
                if key == "resources_by_level" and item_id in merged_values and isinstance(item_value, dict):
                    current = {**merged_values[item_id], **deepcopy(item_value)}
                    if "maximum_by_level" in merged_values[item_id] or "maximum_by_level" in item_value:
                        current["maximum_by_level"] = {
                            **merged_values[item_id].get("maximum_by_level", {}),
                            **item_value.get("maximum_by_level", {}),
                        }
                    merged_values[item_id] = current
                else:
                    merged_values[item_id] = deepcopy(item_value)
        if merged_values:
            CLASS_REGISTRY[_class_id][key] = merged_values
    for key in ("spellcasting_by_level", "spellcasting_ability", "subclass_options"):
        if key in progression:
            CLASS_REGISTRY[_class_id][key] = deepcopy(progression[key])
        if key in extension:
            CLASS_REGISTRY[_class_id][key] = {
                **CLASS_REGISTRY[_class_id].get(key, {}),
                **deepcopy(extension[key]),
            } if key == "spellcasting_by_level" else deepcopy(extension[key])
    if "features_by_level" in CLASS_REGISTRY[_class_id]:
        CLASS_REGISTRY[_class_id]["features_by_level"] = {
            int(level) if str(level).isdigit() else level: features
            for level, features in CLASS_REGISTRY[_class_id]["features_by_level"].items()
        }
    for spec in CLASS_REGISTRY[_class_id].get("resources_by_level", {}).values():
        if "maximum_by_level" in spec:
            spec["maximum_by_level"] = {
                int(level) if str(level).isdigit() else level: maximum
                for level, maximum in spec["maximum_by_level"].items()
            }
    if "attacks_by_level" in CLASS_REGISTRY[_class_id]:
        CLASS_REGISTRY[_class_id]["attacks_by_level"] = {
            int(level) if str(level).isdigit() else level: attacks
            for level, attacks in CLASS_REGISTRY[_class_id]["attacks_by_level"].items()
        }
    default_max_level = 20 if _class_id == "fighter" else 1
    CLASS_REGISTRY[_class_id]["max_supported_level"] = max(6, CLASS_REGISTRY[_class_id].get("max_supported_level", default_max_level))


def class_definition(class_id: str) -> dict[str, Any]:
    definition = CLASS_REGISTRY.get(class_id)
    if definition is None:
        raise ValueError(f"unsupported class: {class_id}")
    return deepcopy(definition)


def _validate_supported_level(definition: dict[str, Any], level: int) -> None:
    if not isinstance(level, int) or level < 1 or level > definition.get("max_supported_level", 1):
        raise ValueError(f"class progression is not implemented at level {level}")


def class_features(class_id: str, level: int) -> list[str]:
    definition = class_definition(class_id)
    _validate_supported_level(definition, level)
    features: list[str] = []
    for feature_level in range(1, level + 1):
        for feature in definition.get("features_by_level", {}).get(feature_level, []):
            if feature not in features:
                features.append(feature)
    return features


def _resource_spec(class_id: str, resource_id: str) -> dict[str, Any]:
    resource = class_definition(class_id).get("resources_by_level", {}).get(resource_id)
    if resource is None:
        raise ValueError(f"unsupported class resource: {resource_id}")
    return resource


def class_resource_maximum(class_id: str, resource_id: str, level: int, *, ability_modifier: int | None = None) -> int:
    definition = class_definition(class_id)
    _validate_supported_level(definition, level)
    resource = _resource_spec(class_id, resource_id)
    if "maximum_formula" in resource:
        if resource["maximum_formula"] in {"max(1, charisma_modifier)", "max(1, wisdom_modifier)"}:
            if ability_modifier is None:
                raise ValueError(f"ability modifier is required for resource: {resource_id}")
            return max(1, ability_modifier)
        if resource["maximum_formula"] == "5 * level":
            return 5 * level
        raise ValueError(f"unsupported resource formula: {resource_id}")
    available = [item_level for item_level in resource["maximum_by_level"] if item_level <= level]
    if not available:
        raise ValueError(f"resource is not available at level {level}")
    return resource["maximum_by_level"][max(available)]


def class_resource_recovery(class_id: str, resource_id: str) -> str:
    recovery = _resource_spec(class_id, resource_id)["recovery"]
    return "short_rest" if recovery == "short_rest_and_long_rest" else recovery


def class_resource_recovery_amount(class_id: str, resource_id: str) -> int | None:
    return _resource_spec(class_id, resource_id).get("recovery_amount")


def class_resource_ids(class_id: str, level: int) -> list[str]:
    definition = class_definition(class_id)
    _validate_supported_level(definition, level)
    return [resource_id for resource_id, spec in definition.get("resources_by_level", {}).items()
            if not spec.get("display_only") and (any(item_level <= level for item_level in spec.get("maximum_by_level", {})) or "maximum_formula" in spec)]


def class_spellcasting(class_id: str, level: int) -> dict[str, Any] | None:
    definition = class_definition(class_id)
    _validate_supported_level(definition, level)
    available = {item_level: value for item_level, value in definition.get("spellcasting_by_level", {}).items() if item_level <= level}
    if not available:
        return None
    return deepcopy(available[max(available)])


def class_subclass_options(class_id: str) -> list[str]:
    return list(class_definition(class_id).get("subclass_options", []))


def class_subclass_features(class_id: str, level: int) -> list[str]:
    definition = class_definition(class_id)
    _validate_supported_level(definition, level)
    features: list[str] = []
    for feature_level in range(1, level + 1):
        for feature in definition.get("subclass_features_by_level", {}).get(feature_level, []):
            if feature not in features:
                features.append(feature)
    return features


def class_attack_count(class_id: str, level: int) -> int:
    definition = class_definition(class_id)
    _validate_supported_level(definition, level)
    available = [item_level for item_level in definition.get("attacks_by_level", {}) if item_level <= level]
    if not available:
        return 1
    return definition["attacks_by_level"][max(available)]
