from __future__ import annotations

import json
import re
import unicodedata
from copy import deepcopy
from pathlib import Path
from typing import Any


CATALOG_PATH = Path(__file__).with_name("character_creation_phb2024.json")
PHB2024_CATALOG: dict[str, Any] = json.loads(CATALOG_PATH.read_text(encoding="utf-8"))
if PHB2024_CATALOG.get("edition") != "2024" or PHB2024_CATALOG.get("schema_version") != "character-options-phb2024-v1":
    raise RuntimeError("character creation catalog must be sourced from the 2024 Player's Handbook")

ABILITY_IDS = ("strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma")
SKILL_IDS = (
    "acrobatics", "animal_handling", "arcana", "athletics", "deception", "history",
    "insight", "intimidation", "investigation", "medicine", "nature", "perception",
    "performance", "persuasion", "religion", "sleight_of_hand", "stealth", "survival",
)


def _norm(value: str) -> str:
    value = unicodedata.normalize("NFKD", value).encode("ascii", "ignore").decode("ascii")
    return re.sub(r"[^a-z0-9]+", " ", value.casefold()).strip()


_ABILITY_ALIASES = {
    _norm(alias): ability
    for ability, aliases in {
        "strength": ("Strength", "Força"),
        "dexterity": ("Dexterity", "Destreza"),
        "constitution": ("Constitution", "Constituição"),
        "intelligence": ("Intelligence", "Inteligência"),
        "wisdom": ("Wisdom", "Sabedoria"),
        "charisma": ("Charisma", "Carisma"),
    }.items()
    for alias in aliases
}

_SKILL_ALIASES = {
    _norm(alias): skill
    for skill, aliases in {
        "acrobatics": ("Acrobatics", "Acrobacia"),
        "animal_handling": ("Animal Handling", "Adestramento de Animais", "Adestrar Animais", "Lidar com Animais"),
        "arcana": ("Arcana", "Arcanismo"),
        "athletics": ("Athletics", "Atletismo"),
        "deception": ("Deception", "Enganação"),
        "history": ("History", "História"),
        "insight": ("Insight", "Intuição"),
        "intimidation": ("Intimidation", "Intimidação"),
        "investigation": ("Investigation", "Investigação"),
        "medicine": ("Medicine", "Medicina"),
        "nature": ("Nature", "Natureza"),
        "perception": ("Perception", "Percepção"),
        "performance": ("Performance", "Atuação"),
        "persuasion": ("Persuasion", "Persuasão"),
        "religion": ("Religion", "Religião"),
        "sleight_of_hand": ("Sleight of Hand", "Prestidigitação"),
        "stealth": ("Stealth", "Furtividade"),
        "survival": ("Survival", "Sobrevivência"),
    }.items()
    for alias in aliases
}


def _raw_value(value: Any) -> Any:
    """Unwrap common extraction envelopes without interpreting rule meaning."""
    if isinstance(value, dict):
        for key in ("value", "pt_br", "options", "values", "from", "opcoes", "options_list"):
            if key in value:
                return _raw_value(value[key])
        return [
            _raw_value(item)
            for key, item in value.items()
            if key not in {"source", "note", "notes", "description", "selection"}
        ]
    if isinstance(value, list):
        return [_raw_value(item) for item in value]
    return value


def _text_items(value: Any) -> list[str]:
    if isinstance(value, dict):
        for key in ("source_name", "name", "localized_name", "label", "pt_br", "value", "id", "nome"):
            if key in value:
                return _text_items(value[key])
        for key in ("options", "values", "from", "opcoes", "options_list"):
            if key in value:
                return _text_items(value[key])
        return [text for key, child in value.items() if key not in {"source", "note", "notes", "description", "selection", "category", "chapter"} for text in _text_items(child)]
    if isinstance(value, list):
        result: list[str] = []
        for item in value:
            result.extend(_text_items(item))
        return result
    if isinstance(value, str):
        return [value]
    return []


def _variants(text: str) -> list[str]:
    parts = re.split(r"[(),;/]|\s+(?:and|or|e|ou)\s+", text, flags=re.IGNORECASE)
    return [part.strip() for part in parts if part.strip()]


def _ability_id(value: Any) -> str | None:
    for text in _text_items(value):
        for part in _variants(text):
            result = _ABILITY_ALIASES.get(_norm(part))
            if result:
                return result
    return None


def _skill_id(value: Any) -> str | None:
    for text in _text_items(value):
        for part in _variants(text):
            result = _SKILL_ALIASES.get(_norm(part))
            if result:
                return result
    return None


def _field(fields: dict[str, Any], *names: str) -> Any:
    for name in names:
        if name in fields and fields[name] is not None:
            return fields[name]
    return None


def _ability_list(value: Any) -> list[str]:
    values = _text_items(value)
    result: list[str] = []
    for item in values:
        # Some source records present a list of choices in one comma-delimited string.
        for part in re.split(r"[,;]|\s+(?:and|or|e|ou)\s+", item, flags=re.IGNORECASE):
            ability = _ability_id(part)
            if ability and ability not in result:
                result.append(ability)
    return result


def _background_abilities(fields: dict[str, Any]) -> list[str]:
    root = _field(fields, "ability_scores", "ability_score_options", "ability_score_increases", "ability_choices")
    if root is None:
        return []
    target_keys = {
        "eligible abilities", "eligible scores", "allowed scores", "listed scores",
        "listed options", "eligible scores", "ability options", "abilities", "scores",
    }

    def find(value: Any) -> list[str]:
        if isinstance(value, dict):
            for key, child in value.items():
                if _norm(key) in target_keys:
                    found = _ability_list(child)
                    if found:
                        return found
            for child in value.values():
                found = find(child)
                if found:
                    return found
        elif isinstance(value, list):
            direct = _ability_list(value)
            if len(direct) >= 3:
                return direct
            for child in value:
                found = find(child)
                if found:
                    return found
        return []

    return find(root)


def _skill_choices(fields: dict[str, Any], *, background: bool = False) -> dict[str, Any]:
    raw = _field(fields, "skill_proficiencies", "skills", "pericias")
    if background:
        options = [_skill_id(item) for item in _text_items(raw)]
        return {"count": len([item for item in options if item]), "options": list(dict.fromkeys(item for item in options if item))}
    if isinstance(raw, dict):
        count = raw.get("choose", raw.get("count", raw.get("quantity", raw.get("quantidade_escolhida"))))
        option_raw = raw.get("from", raw.get("options", raw.get("opcoes", raw.get("list"))))
    else:
        option_raw = raw
        count = len(raw) if isinstance(raw, list) else None
    if isinstance(option_raw, str) and ("any skill" in _norm(option_raw) or "qualquer pericia" in _norm(option_raw)):
        options = list(SKILL_IDS)
    else:
        options = [_skill_id(item) for item in _text_items(option_raw)]
        options = list(dict.fromkeys(item for item in options if item))
    if not isinstance(count, int) or isinstance(count, bool):
        count = len(options)
    return {"count": count, "options": options}


def _feature_entries(fields: dict[str, Any]) -> list[dict[str, str]]:
    raw = _field(fields, "level_1_features", "caracteristicas_nivel_1", "features_level_1")
    entries: list[dict[str, str]] = []
    if isinstance(raw, dict):
        candidates = []
        for key, value in raw.items():
            if isinstance(value, dict):
                candidates.append({"name": value.get("name") or value.get("nome") or key, **value})
            else:
                candidates.append({"name": key, "summary": str(value)})
    elif isinstance(raw, list):
        candidates = raw
    else:
        candidates = []
    for candidate in candidates:
        if isinstance(candidate, str):
            name, feature_id, summary = candidate, "", ""
        elif isinstance(candidate, dict):
            name = candidate.get("name") or candidate.get("nome") or candidate.get("localized_name") or candidate.get("feature") or candidate.get("id")
            feature_id = candidate.get("id") or candidate.get("feature_id") or ""
            summary = candidate.get("summary") or candidate.get("description") or candidate.get("effect") or ""
        else:
            continue
        if not isinstance(name, str) or not name.strip():
            continue
        slug = re.sub(r"[^a-z0-9]+", "_", unicodedata.normalize("NFKD", str(feature_id or name)).encode("ascii", "ignore").decode().casefold()).strip("_")
        entries.append({"id": slug, "name": name.strip(), "summary": str(summary).strip()})
    return entries


def _hit_die(fields: dict[str, Any]) -> int:
    raw = _field(fields, "hit_die", "dado_vida", "dado_de_vida")
    if isinstance(raw, dict) and isinstance(raw.get("sides"), int):
        return raw["sides"]
    strings = _text_items(raw)
    match = re.search(r"d\s*(6|8|10|12)", " ".join(strings), re.IGNORECASE)
    if not match:
        raise RuntimeError(f"missing PHB 2024 Hit Point Die for class record: {fields.keys()}")
    return int(match.group(1))


def _saving_throws(fields: dict[str, Any]) -> list[str]:
    raw = _field(fields, "saving_throw_proficiencies", "saving_throws", "testes_resistencia", "testes_de_resistencia")
    result = [_ability_id(item) for item in _text_items(raw)]
    result = list(dict.fromkeys(item for item in result if item))
    if len(result) != 2:
        raise RuntimeError("PHB 2024 class record must resolve exactly two saving throw proficiencies")
    return result


def _proficiency_groups(fields: dict[str, Any], *, kind: str) -> list[str]:
    raw_keys = ("weapon_proficiencies", "weapons", "armas") if kind == "weapon" else ("armor_training", "armor_proficiencies", "armor", "armaduras")
    raw = _field(fields, *raw_keys)
    nested = _field(fields, "proficiencies", "proficiencias")
    if raw is None and isinstance(nested, dict):
        nested_keys = ("weapons", "weapon_proficiencies", "armas") if kind == "weapon" else ("armor", "armor_training", "armaduras")
        raw = _field(nested, *nested_keys)
    if isinstance(raw, dict):
        raw = _field(raw, "proficiencies", "proficiencias", "weapons", "weapons_proficiencies", "armor_training", "armaduras", "weapons")
    texts = " ".join(_norm(item) for item in _text_items(raw))
    if kind == "weapon":
        groups = []
        if "simple" in texts or "simples" in texts:
            groups.append("simple")
        has_martial = "martial" in texts or "marciais" in texts
        has_light = "light" in texts or "leve" in texts
        has_finesse = "finesse" in texts or "acuidade" in texts
        if has_martial and has_light and has_finesse:
            groups.append("martial_finesse_or_light")
        elif has_martial and has_light:
            groups.append("martial_light")
        elif has_martial:
            groups.append("martial")
    else:
        groups = []
        if "light" in texts or "leve" in texts:
            groups.append("light")
        if "medium" in texts or "media" in texts:
            groups.append("medium")
        if "heavy" in texts or "pesada" in texts:
            groups.append("heavy")
        if "shield" in texts or "escudo" in texts:
            groups.append("shields")
        proficiency_data = nested if isinstance(nested, dict) else {}
        shield_training = _field(fields, "shields", "escudos")
        if shield_training is None:
            shield_training = _field(proficiency_data, "shields", "escudos")
        if shield_training is True and "shields" not in groups:
            groups.append("shields")
    return list(dict.fromkeys(groups))


def _equipment(fields: dict[str, Any]) -> Any:
    return _field(fields, "starting_equipment", "equipment", "equipamento_inicial")


def equipment_package_map(value: Any) -> dict[str, Any]:
    if isinstance(value, list):
        packages = {}
        for package in value:
            if isinstance(package, dict) and str(package.get("id", "")).upper() in {"A", "B", "C"}:
                packages[str(package["id"]).upper()] = package
        return packages
    if not isinstance(value, dict):
        return {}
    packages: dict[str, Any] = {}
    for key, package in value.items():
        match = re.fullmatch(r"(?:choice[_ -]?)?([abc])", str(key).casefold())
        if match:
            packages[match.group(1).upper()] = package
    if packages:
        return packages
    nested_options = value.get("options")
    if isinstance(nested_options, dict):
        for key, package in nested_options.items():
            if str(key).upper() in {"A", "B", "C"}:
                packages[str(key).upper()] = package
        if packages:
            return packages
    for key in ("choose_one", "choose_one_of", "options", "alternativas"):
        options = value.get(key)
        if isinstance(options, list):
            for package in options:
                if isinstance(package, dict):
                    option = package.get("option") or package.get("choice") or package.get("escolha") or package.get("id")
                    if isinstance(option, str) and option.upper() in {"A", "B", "C"}:
                        packages[option.upper()] = package
            if packages:
                return packages
    return {}


def _gold_amount(value: Any) -> int:
    if isinstance(value, int) and not isinstance(value, bool):
        return value
    if isinstance(value, str):
        match = re.search(r"\b(\d+)\s*(?:gp|po|gold(?:\s+pieces?)?)\b", value, re.IGNORECASE)
        if match:
            return int(match.group(1))
    return 0


def _package_items(value: Any) -> list[dict[str, Any]]:
    if isinstance(value, dict):
        raw_items = _field(value, "items", "itens", "equipment", "equipamento")
        if raw_items is None:
            return []
        items = raw_items if isinstance(raw_items, list) else [raw_items]
    elif isinstance(value, list):
        items = value
    elif isinstance(value, str):
        items = [value]
    else:
        return []
    result: list[dict[str, Any]] = []
    for item in items:
        if isinstance(item, dict):
            name = item.get("name") or item.get("item") or item.get("id") or item.get("nome")
            quantity = item.get("quantity", item.get("qty", 1))
        else:
            name, quantity = item, 1
        if not isinstance(name, str) or not name.strip() or _gold_amount(name):
            continue
        result.append({"name": name.strip(), "quantity": quantity if isinstance(quantity, int) and quantity > 0 else 1})
    return result


def normalize_equipment_packages(value: Any) -> list[dict[str, Any]]:
    packages = equipment_package_map(value)
    normalized: list[dict[str, Any]] = []
    for option_id, raw_package in packages.items():
        gold = 0
        if isinstance(raw_package, dict):
            for key in ("gold_gp", "coins_gp", "gp", "gold_pieces", "gold", "money", "dinheiro"):
                gold = max(gold, _gold_amount(raw_package.get(key)))
        if isinstance(raw_package, str):
            gold = max(gold, _gold_amount(raw_package))
        if isinstance(raw_package, list):
            for item in raw_package:
                gold += _gold_amount(item)
        # Packages sometimes put the coins in an item list instead of a dedicated field.
        if isinstance(raw_package, dict):
            raw_items = _field(raw_package, "items", "itens", "equipment", "equipamento")
            if isinstance(raw_items, list):
                gold += sum(_gold_amount(item) for item in raw_items if isinstance(item, str))
        normalized.append({
            "id": option_id,
            "items": _package_items(raw_package),
            "gold_gp": gold,
        })
    return sorted(normalized, key=lambda item: item["id"])


def validate_equipment_package_choice(value: Any, option_id: str, *, source: str) -> dict[str, Any]:
    packages = equipment_package_map(value)
    normalized_option = option_id.upper()
    if normalized_option not in packages:
        raise ValueError(f"unsupported {source} equipment package option")
    selected = next(
        package for package in normalize_equipment_packages(value)
        if package["id"] == normalized_option
    )
    return selected


CLASS_RECORDS = {record["id"]: record for record in PHB2024_CATALOG["classes"]}
SPECIES_RECORDS = {record["id"]: record for record in PHB2024_CATALOG["species"]}
BACKGROUND_RECORDS = {record["id"]: record for record in PHB2024_CATALOG["backgrounds"]}


def class_runtime_definition(class_id: str) -> dict[str, Any]:
    record = CLASS_RECORDS[class_id]
    runtime = deepcopy(record["runtime"])
    if not runtime.get("primary_abilities"):
        raise RuntimeError(f"PHB 2024 class {class_id} is missing primary abilities")
    skills = runtime.get("skill_proficiencies", {})
    if skills.get("count", 0) < 1 or len(skills.get("options", [])) < skills.get("count", 0):
        raise RuntimeError(f"PHB 2024 class {class_id} has incomplete skill choices")
    feature_entries = runtime.get("level_1_feature_entries", [])
    if not feature_entries:
        raise RuntimeError(f"PHB 2024 class {class_id} has no level-1 feature metadata")
    return {
        **runtime,
        "id": class_id,
        "label": record["label_pt_br"],
        "source_name": record["source_name"],
        "source_location": record["source_location"],
    }


CLASS_RUNTIME_DEFINITIONS = {class_id: class_runtime_definition(class_id) for class_id in CLASS_RECORDS}


_SPECIES_CHOICE_OPTIONS: dict[str, dict[str, list[str]]] = {
    "aasimar": {"size": ["medium", "small"]},
    "dragonborn": {"draconic_ancestry": ["black", "blue", "brass", "bronze", "copper", "gold", "green", "red", "silver", "white"]},
    "dwarf": {},
    "elf": {
        "elven_lineage": ["drow", "high_elf", "wood_elf"],
        "spellcasting_ability": ["intelligence", "wisdom", "charisma"],
        "keen_senses_skill": ["insight", "perception", "survival"],
    },
    "gnome": {
        "gnomish_lineage": ["forest_gnome", "rock_gnome"],
        "spellcasting_ability": ["intelligence", "wisdom", "charisma"],
    },
    "goliath": {"giant_ancestry": ["cloud", "fire", "frost", "hill", "stone", "storm"]},
    "halfling": {},
    "human": {
        "size": ["medium", "small"],
        "skillful_skill": list(SKILL_IDS),
        "versatile_feat": ["alert", "crafter", "healer", "lucky", "magic_initiate_cleric", "magic_initiate_druid", "magic_initiate_wizard", "musician", "savage_attacker", "skilled", "tavern_brawler", "tough"],
    },
    "orc": {},
    "tiefling": {
        "size": ["medium", "small"],
        "fiendish_legacy": ["abyssal", "chthonic", "infernal"],
        "spellcasting_ability": ["intelligence", "wisdom", "charisma"],
    },
}

_CHOICE_ALIASES = {
    "clouds jaunt cloud giant": "cloud", "cloud": "cloud",
    "fires burn fire giant": "fire", "fire": "fire",
    "frosts chill frost giant": "frost", "frost": "frost",
    "hills tumble hill giant": "hill", "hill": "hill",
    "stones endurance stone giant": "stone", "stone": "stone",
    "storms thunder storm giant": "storm", "storm": "storm",
    "high elf": "high_elf", "wood elf": "wood_elf", "forest gnome": "forest_gnome", "rock gnome": "rock_gnome",
    "magic initiate cleric": "magic_initiate_cleric", "magic initiate druid": "magic_initiate_druid", "magic initiate wizard": "magic_initiate_wizard",
}


def _choice_id(key: str, value: Any) -> str:
    if not isinstance(value, str):
        raise ValueError(f"species choice {key} must be a string")
    normalized = _norm(value)
    normalized = _CHOICE_ALIASES.get(normalized, normalized.replace(" ", "_"))
    return normalized


def validate_species_choices(species_id: str, choices: dict[str, str]) -> dict[str, str]:
    if species_id not in _SPECIES_CHOICE_OPTIONS:
        raise ValueError("unsupported PHB 2024 species")
    allowed = _SPECIES_CHOICE_OPTIONS[species_id]
    if not isinstance(choices, dict):
        raise ValueError("species choices must be an object")
    if set(choices) != set(allowed):
        raise ValueError("species choices must include exactly the PHB 2024 choices for this species")
    normalized = {key: _choice_id(key, value) for key, value in choices.items()}
    for key, value in normalized.items():
        permitted = allowed[key]
        if value not in permitted:
            raise ValueError(f"unsupported {key} choice for {species_id}")
    return normalized


def validate_ability_assignment(method_id: str, base_abilities: dict[str, int]) -> None:
    if set(base_abilities) != set(ABILITY_IDS):
        raise ValueError("base abilities must include exactly the six PHB 2024 abilities")
    if method_id == "standard_array":
        if sorted(base_abilities.values()) != [8, 10, 12, 13, 14, 15]:
            raise ValueError("standard array must use 15, 14, 13, 12, 10, and 8 exactly once")
    elif method_id == "point_buy":
        if any(not 8 <= value <= 15 for value in base_abilities.values()):
            raise ValueError("point buy scores must be between 8 and 15")
        costs = {8: 0, 9: 1, 10: 2, 11: 3, 12: 4, 13: 5, 14: 7, 15: 9}
        if sum(costs[value] for value in base_abilities.values()) > 27:
            raise ValueError("point buy scores exceed the PHB 2024 budget of 27 points")
    elif method_id == "rolled":
        if any(not 3 <= value <= 18 for value in base_abilities.values()):
            raise ValueError("rolled scores must be between 3 and 18")
    else:
        raise ValueError("unsupported PHB 2024 ability score method")


def validate_background_ability_increases(
    background_id: str,
    increases: dict[str, int],
    base_abilities: dict[str, int],
    final_abilities: dict[str, int],
) -> None:
    record = BACKGROUND_RECORDS.get(background_id)
    if record is None:
        raise ValueError("unsupported PHB 2024 background")
    eligible = record["eligible_abilities"]
    if len(eligible) != 3:
        raise RuntimeError(f"PHB 2024 background {background_id} does not have three eligible abilities")
    if not isinstance(increases, dict) or not set(increases).issubset(set(eligible)):
        raise ValueError("background ability increases must use abilities listed by this PHB 2024 background")
    values = sorted(increases.values())
    if values not in ([1, 2], [1, 1, 1]) or sum(increases.values()) != 3:
        raise ValueError("background ability increases must be +2/+1 or +1/+1/+1")
    expected = {ability: base_abilities[ability] + increases.get(ability, 0) for ability in ABILITY_IDS}
    if final_abilities != expected:
        raise ValueError("final abilities must equal the base scores plus the selected background increases")
    if any(value > 20 for value in final_abilities.values()):
        raise ValueError("ability scores cannot exceed 20 during character creation")


def background_definition(background_id: str) -> dict[str, Any]:
    record = BACKGROUND_RECORDS.get(background_id)
    if record is None:
        raise ValueError("unsupported PHB 2024 background")
    if len(record["skill_proficiencies"]) != 2:
        raise RuntimeError(f"PHB 2024 background {background_id} must grant exactly two skill proficiencies")
    return {
        "id": background_id,
        "label": record["label_pt_br"],
        "source_name": record["source_name"],
        "eligible_abilities": list(record["eligible_abilities"]),
        "skill_proficiencies": list(record["skill_proficiencies"]),
        "origin_feat": record["origin_feat"],
        "origin_feat_id": record["origin_feat_id"],
        "origin_feat_label_pt_br": record["origin_feat_label_pt_br"],
        "tool_proficiencies": list(record.get("tool_proficiencies", [])),
        "equipment_packages": normalize_equipment_packages(record["equipment_packages"]),
        "source_location": record["source_location"],
    }


def _class_option(class_id: str) -> dict[str, Any]:
    definition = CLASS_RUNTIME_DEFINITIONS[class_id]
    record = CLASS_RECORDS[class_id]
    return {
        "id": class_id,
        "label": definition["label"],
        "source_name": definition["source_name"],
        "levels": [1],
        "hit_die": definition["hit_die"],
        "primary_abilities": definition["primary_abilities"],
        "skill_choices": definition["skill_proficiencies"],
        "saving_throw_proficiencies": definition["saving_throw_proficiencies"],
        "weapon_proficiencies": definition["weapon_proficiencies"],
        "armor_proficiencies": definition["armor_proficiencies"],
        "level_1_features": deepcopy(definition["level_1_feature_entries"]),
        "equipment_packages": normalize_equipment_packages(definition["starting_equipment"]),
        "source_location": definition["source_location"],
    }


def character_options_phb2024() -> dict[str, Any]:
    alignment_options = [
        {"id": "lawful_good", "label": "Leal e Bom"},
        {"id": "neutral_good", "label": "Neutro e Bom"},
        {"id": "chaotic_good", "label": "Caótico e Bom"},
        {"id": "lawful_neutral", "label": "Leal e Neutro"},
        {"id": "neutral", "label": "Neutro"},
        {"id": "chaotic_neutral", "label": "Caótico e Neutro"},
        {"id": "lawful_evil", "label": "Leal e Mau"},
        {"id": "neutral_evil", "label": "Neutro e Mau"},
        {"id": "chaotic_evil", "label": "Caótico e Mau"},
    ]
    ability_methods = {
        "standard_array": {"id": "standard_array", "label": "Arraya padrão", "values": [15, 14, 13, 12, 10, 8]},
        "point_buy": {
            "id": "point_buy", "label": "Compra por pontos", "budget": 27,
            "minimum": 8, "maximum": 15,
            "costs": {"8": 0, "9": 1, "10": 2, "11": 3, "12": 4, "13": 5, "14": 7, "15": 9},
            "verification_source": PHB2024_CATALOG["ability_score_methods"]["point_cost"].get("verification_source"),
        },
        "rolled": {"id": "rolled", "label": "Rolagem", "dice": "4d6", "drop_lowest": 1, "number_of_scores": 6},
        "background_increases": {"patterns": [[2, 1], [1, 1, 1]], "eligible_source": "selected_background"},
    }
    return {
        "schema_version": PHB2024_CATALOG["schema_version"],
        "ruleset": "dnd-2024-phb",
        "edition": 2024,
        "supported_character_level": 1,
        "classes": [_class_option(class_id) for class_id in CLASS_RECORDS],
        "species": [
            {
                "id": record["id"], "label": record["label_pt_br"], "source_name": record["source_name"],
                "choices": deepcopy(_SPECIES_CHOICE_OPTIONS[record["id"]]),
                "source_location": record["source_location"],
            }
            for record in PHB2024_CATALOG["species"]
        ],
        "backgrounds": [background_definition(background_id) for background_id in BACKGROUND_RECORDS],
        "alignment_options": alignment_options,
        "ability_score_methods": ability_methods,
        "recommended_standard_array": deepcopy(PHB2024_CATALOG["standard_array_by_class"]),
        "abilities": list(ABILITY_IDS),
        "skills": list(SKILL_IDS),
        "language_rules": deepcopy(PHB2024_CATALOG["language_rules"]),
        "species_choice_options": deepcopy(_SPECIES_CHOICE_OPTIONS),
        "origin_feat_choices": _SPECIES_CHOICE_OPTIONS["human"]["versatile_feat"],
    }
