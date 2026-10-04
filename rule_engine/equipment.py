from __future__ import annotations

from copy import deepcopy
from typing import Any

WEAPON_CATALOG: dict[str, dict[str, Any]] = {
    "longsword": {
        "id": "longsword",
        "kind": "weapon",
        "slot": "weapon",
        "ability": "strength",
        "damage_dice": "1d8",
        "damage_type": "slashing",
        "proficient": True,
    },
}

ARMOR_CATALOG: dict[str, dict[str, Any]] = {
    "leather": {
        "id": "leather",
        "kind": "armor",
        "slot": "armor",
        "armor_class": 11,
        "dexterity_bonus_max": None,
    },
}

ITEM_CATALOG = {**WEAPON_CATALOG, **ARMOR_CATALOG}
SLOTS = frozenset({"weapon", "armor"})


def _inventory(character: dict[str, Any]) -> dict[str, dict[str, Any]]:
    inventory = character.setdefault("inventory", {})
    if not isinstance(inventory, dict):
        raise ValueError("inventory must be an object")
    return inventory


def _equipped(character: dict[str, Any]) -> dict[str, str | None]:
    equipped = character.setdefault("equipped", {"weapon": None, "armor": None})
    if not isinstance(equipped, dict):
        raise ValueError("equipped must be an object")
    equipped.setdefault("weapon", None)
    equipped.setdefault("armor", None)
    return equipped


def item_definition(item_id: str, item: dict[str, Any] | None = None) -> dict[str, Any]:
    catalog_definition = ITEM_CATALOG.get(item_id)
    if catalog_definition is None:
        raise ValueError("unknown item")
    if item is not None and item != catalog_definition:
        raise ValueError("item definition must match the authoritative catalog")
    definition = deepcopy(catalog_definition)
    if not isinstance(definition, dict):
        raise ValueError("unknown item")
    if definition.get("id", item_id) != item_id:
        raise ValueError("item definition id does not match item id")
    definition["id"] = item_id
    kind = definition.get("kind")
    if kind not in {"weapon", "armor", "item"}:
        raise ValueError("item kind must be weapon, armor, or item")
    if kind in {"weapon", "armor"} and definition.get("slot") not in SLOTS:
        raise ValueError("equipable item has an invalid slot")
    if kind == "weapon":
        if definition.get("ability") not in {"strength", "dexterity"}:
            raise ValueError("weapon ability is invalid")
        if not isinstance(definition.get("damage_dice"), str) or not definition["damage_dice"]:
            raise ValueError("weapon damage dice are required")
    if kind == "armor":
        armor_class = definition.get("armor_class")
        dexterity_bonus_max = definition.get("dexterity_bonus_max")
        if not isinstance(armor_class, int) or isinstance(armor_class, bool) or armor_class < 0:
            raise ValueError("armor class must be a non-negative integer")
        if dexterity_bonus_max is not None and (
            not isinstance(dexterity_bonus_max, int)
            or isinstance(dexterity_bonus_max, bool)
            or dexterity_bonus_max < 0
        ):
            raise ValueError("armor dexterity cap must be a non-negative integer")
    return definition


def _entry(item_id: str, value: Any) -> tuple[int, dict[str, Any]]:
    if isinstance(value, int) and not isinstance(value, bool):
        return value, item_definition(item_id)
    if not isinstance(value, dict):
        raise ValueError("inventory entry must be an object")
    quantity = value.get("quantity")
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity < 0:
        raise ValueError("item quantity must be a non-negative integer")
    return quantity, item_definition(item_id, value.get("item"))


def validate_inventory(character: dict[str, Any]) -> None:
    inventory = _inventory(character)
    for item_id, value in inventory.items():
        quantity, _definition = _entry(item_id, value)
        if quantity == 0:
            raise ValueError("inventory cannot contain zero-quantity entries")
    equipped = _equipped(character)
    for slot, item_id in equipped.items():
        if slot not in SLOTS:
            raise ValueError("unknown equipment slot")
        if item_id is None:
            continue
        if item_id not in inventory:
            raise ValueError("equipped item is not in inventory")
        quantity, definition = _entry(item_id, inventory[item_id])
        if quantity <= 0 or definition.get("slot") != slot:
            raise ValueError("equipped item is invalid for slot")


def add_item(
    character: dict[str, Any],
    item_id: str,
    quantity: int = 1,
    *,
    item: dict[str, Any] | None = None,
) -> dict[str, Any]:
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise ValueError("item quantity must be a positive integer")
    inventory = _inventory(character)
    definition = item_definition(item_id, item)
    current = inventory.get(item_id)
    if current is None:
        inventory[item_id] = {"item_id": item_id, "quantity": quantity, "item": definition}
    else:
        existing_quantity, existing_definition = _entry(item_id, current)
        if existing_definition != definition:
            raise ValueError("item definition conflicts with inventory")
        inventory[item_id] = {
            "item_id": item_id,
            "quantity": existing_quantity + quantity,
            "item": existing_definition,
        }
    return inventory[item_id]


def remove_item(character: dict[str, Any], item_id: str, quantity: int = 1) -> dict[str, Any]:
    if not isinstance(quantity, int) or isinstance(quantity, bool) or quantity <= 0:
        raise ValueError("item quantity must be a positive integer")
    inventory = _inventory(character)
    current = inventory.get(item_id)
    if current is None:
        raise ValueError("item does not exist in inventory")
    existing_quantity, definition = _entry(item_id, current)
    if existing_quantity < quantity:
        raise ValueError("insufficient item quantity")
    remaining = existing_quantity - quantity
    equipped = _equipped(character)
    if remaining == 0 and item_id in equipped.values():
        raise ValueError("cannot remove an equipped item")
    if remaining:
        inventory[item_id] = {"item_id": item_id, "quantity": remaining, "item": definition}
    else:
        del inventory[item_id]
    return {"item_id": item_id, "quantity": remaining}


def equip_item(character: dict[str, Any], item_id: str, slot: str | None = None) -> dict[str, Any]:
    inventory = _inventory(character)
    if item_id not in inventory:
        raise ValueError("cannot equip an item that is not in inventory")
    quantity, definition = _entry(item_id, inventory[item_id])
    if quantity <= 0 or definition.get("kind") not in {"weapon", "armor"}:
        raise ValueError("item is not equipable")
    resolved_slot = slot or definition.get("slot")
    if resolved_slot not in SLOTS or definition.get("slot") != resolved_slot:
        raise ValueError("item cannot be equipped in this slot")
    _equipped(character)[resolved_slot] = item_id
    return {"slot": resolved_slot, "item_id": item_id}


def unequip_item(character: dict[str, Any], slot: str | None = None, item_id: str | None = None) -> dict[str, Any]:
    equipped = _equipped(character)
    if slot is None and item_id is not None:
        slot = next((key for key, value in equipped.items() if value == item_id), None)
    if slot not in SLOTS:
        raise ValueError("equipment slot is required")
    if item_id is not None and equipped.get(slot) != item_id:
        raise ValueError("item is not equipped")
    removed = equipped.get(slot)
    equipped[slot] = None
    return {"slot": slot, "item_id": removed}


def equipped_definition(character: dict[str, Any], slot: str) -> dict[str, Any] | None:
    item_id = _equipped(character).get(slot)
    if item_id is None:
        return None
    inventory = _inventory(character)
    entry = inventory.get(item_id)
    if entry is None:
        raise ValueError("equipped item is not in inventory")
    _quantity, definition = _entry(item_id, entry)
    return definition
