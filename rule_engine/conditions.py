from __future__ import annotations

from typing import Any


CONDITION_IDS = frozenset(
    {
        "blinded",
        "charmed",
        "deafened",
        "exhaustion",
        "frightened",
        "grappled",
        "incapacitated",
        "invisible",
        "paralyzed",
        "petrified",
        "poisoned",
        "prone",
        "restrained",
        "stunned",
        "unconscious",
    }
)


DURATION_KINDS = frozenset(
    {
        "turns",
        "rounds",
        "until_end_of_turn",
        "until_start_of_turn",
        "until_rest",
        "permanent",
    }
)


def _validate_condition_id(condition_id: str) -> None:
    if condition_id not in CONDITION_IDS:
        raise ValueError(f"unknown condition: {condition_id}")


def _validate_duration(duration: dict[str, Any]) -> None:
    if not isinstance(duration, dict):
        raise ValueError("duration must be an object")

    kind = duration.get("kind")

    if kind not in DURATION_KINDS:
        raise ValueError(f"unknown duration kind: {kind}")

    if kind in {"turns", "rounds"}:
        remaining = duration.get("remaining")

        if not isinstance(remaining, int) or isinstance(remaining, bool):
            raise ValueError("timed duration requires integer remaining")

        if remaining < 1:
            raise ValueError("timed duration must be positive")


def normalize_conditions(value: Any) -> list[dict[str, Any]]:
    if value is None:
        return []

    if not isinstance(value, list):
        raise ValueError("conditions must be a list")

    normalized: list[dict[str, Any]] = []

    for condition in value:
        if not isinstance(condition, dict):
            raise ValueError("condition entry must be an object")

        condition_id = condition.get("id")
        if not isinstance(condition_id, str):
            raise ValueError("condition id must be a string")

        _validate_condition_id(condition_id)

        duration = condition.get(
            "duration",
            {
                "kind": "permanent",
            },
        )

        _validate_duration(duration)

        source_id = condition.get("source_id")

        if source_id is not None and not isinstance(source_id, str):
            raise ValueError("condition source_id must be a string")

        effects = condition.get("effects", [])

        if not isinstance(effects, list):
            raise ValueError("condition effects must be a list")

        normalized.append(
            {
                "id": condition_id,
                "source_id": source_id,
                "duration": dict(duration),
                "effects": list(effects),
            }
        )

    return normalized


def get_conditions(creature: dict[str, Any]) -> list[dict[str, Any]]:
    conditions = normalize_conditions(creature.get("conditions"))
    creature["conditions"] = conditions
    return conditions


def has_condition(creature: dict[str, Any], condition_id: str) -> bool:
    _validate_condition_id(condition_id)

    return any(
        condition["id"] == condition_id
        for condition in get_conditions(creature)
    )


def add_condition(
    creature: dict[str, Any],
    *,
    condition_id: str,
    source_id: str | None = None,
    duration: dict[str, Any] | None = None,
    effects: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    _validate_condition_id(condition_id)

    resolved_duration = duration or {"kind": "permanent"}
    _validate_duration(resolved_duration)

    resolved_effects = effects or []

    if not isinstance(resolved_effects, list):
        raise ValueError("effects must be a list")

    condition = {
        "id": condition_id,
        "source_id": source_id,
        "duration": dict(resolved_duration),
        "effects": list(resolved_effects),
    }

    conditions = get_conditions(creature)
    conditions.append(condition)

    return condition


def remove_condition(
    creature: dict[str, Any],
    *,
    condition_id: str,
) -> int:
    _validate_condition_id(condition_id)

    conditions = get_conditions(creature)

    before = len(conditions)

    creature["conditions"] = [
        condition
        for condition in conditions
        if condition["id"] != condition_id
    ]

    return before - len(creature["conditions"])


def remove_condition_instance(
    creature: dict[str, Any],
    *,
    condition_index: int,
) -> dict[str, Any]:
    conditions = get_conditions(creature)

    if condition_index < 0 or condition_index >= len(conditions):
        raise ValueError("condition index out of range")

    return conditions.pop(condition_index)


def advance_condition_durations(
    creature: dict[str, Any],
    *,
    timing: str,
) -> list[dict[str, Any]]:
    if timing not in {
        "turn_end",
        "turn_start",
        "round_end",
        "round_start",
    }:
        raise ValueError(f"unknown condition timing: {timing}")

    conditions = get_conditions(creature)
    expired: list[dict[str, Any]] = []
    remaining_conditions: list[dict[str, Any]] = []

    for condition in conditions:
        duration = condition["duration"]
        kind = duration["kind"]

        should_decrement = (
            (kind == "turns" and timing == "turn_end")
            or (kind == "rounds" and timing == "round_end")
        )

        if not should_decrement:
            remaining_conditions.append(condition)
            continue

        duration["remaining"] -= 1

        if duration["remaining"] <= 0:
            expired.append(condition)
        else:
            remaining_conditions.append(condition)

    creature["conditions"] = remaining_conditions

    return expired


def clear_conditions_for_rest(
    creature: dict[str, Any],
    *,
    rest_type: str,
) -> list[dict[str, Any]]:
    if rest_type not in {"short_rest", "long_rest"}:
        raise ValueError(f"unknown rest type: {rest_type}")

    conditions = get_conditions(creature)

    if rest_type == "short_rest":
        remaining = [
            condition
            for condition in conditions
            if condition["duration"]["kind"] != "until_rest"
        ]
    else:
        remaining = [
            condition
            for condition in conditions
            if condition["duration"]["kind"]
            not in {"until_rest", "turns", "rounds"}
        ]

    removed = [
        condition
        for condition in conditions
        if condition not in remaining
    ]

    creature["conditions"] = remaining

    return removed


def has_disadvantage(
    creature: dict[str, Any],
    *,
    roll_type: str,
) -> bool:
    if roll_type not in {
        "attack",
        "ability_check",
    }:
        raise ValueError(f"unknown roll type: {roll_type}")

    if roll_type in {"attack", "ability_check"}:
        return has_condition(creature, "poisoned")

    return False
