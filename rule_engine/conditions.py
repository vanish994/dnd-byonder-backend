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
_MOVEMENT_BLOCKING_CONDITIONS = frozenset({"grappled", "restrained"})
_SAVED_MOVEMENT_KEY = "_movement_remaining_before_condition_block"


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

TIMING_PHASES = frozenset(
    {
        "turn_start",
        "turn_end",
        "round_start",
        "round_end",
    }
)
_CONTEXTUAL_DURATION_KINDS = frozenset(
    {"rounds", "until_end_of_turn", "until_start_of_turn"}
)
_MISSING = object()


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


def _validate_timing(timing: dict[str, Any]) -> None:
    if not isinstance(timing, dict):
        raise ValueError("condition timing must be an object")

    applied_round = timing.get("applied_round")
    applied_turn_index = timing.get("applied_turn_index")
    applied_phase = timing.get("applied_phase")

    if (
        not isinstance(applied_round, int)
        or isinstance(applied_round, bool)
        or applied_round < 1
    ):
        raise ValueError("condition timing requires a positive applied_round")
    if (
        not isinstance(applied_turn_index, int)
        or isinstance(applied_turn_index, bool)
        or applied_turn_index < 0
    ):
        raise ValueError("condition timing requires a valid applied_turn_index")
    if applied_phase not in TIMING_PHASES:
        raise ValueError(f"unknown condition timing phase: {applied_phase}")


def _validate_contextual_timing(
    duration: dict[str, Any],
    timing: dict[str, Any] | None,
) -> None:
    if duration["kind"] in _CONTEXTUAL_DURATION_KINDS and timing is None:
        raise ValueError(
            f"{duration['kind']} duration requires application timing"
        )
    if timing is not None:
        _validate_timing(timing)


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
        timing = condition.get("_timing")
        _validate_contextual_timing(duration, timing)

        source_id = condition.get("source_id")

        if source_id is not None and not isinstance(source_id, str):
            raise ValueError("condition source_id must be a string")

        effects = condition.get("effects", [])

        if not isinstance(effects, list):
            raise ValueError("condition effects must be a list")

        normalized_condition = {
            "id": condition_id,
            "source_id": source_id,
            "duration": dict(duration),
            "effects": list(effects),
        }
        if timing is not None:
            normalized_condition["_timing"] = dict(timing)
        normalized.append(normalized_condition)

    return normalized


def get_conditions(creature: dict[str, Any]) -> list[dict[str, Any]]:
    return normalize_conditions(creature.get("conditions"))


def has_condition(creature: dict[str, Any], condition_id: str) -> bool:
    _validate_condition_id(condition_id)

    return any(
        condition["id"] == condition_id
        for condition in get_conditions(creature)
    )


def sync_movement_with_conditions(creature: dict[str, Any]) -> None:
    if "movement_speed" not in creature or "movement_remaining" not in creature:
        return

    blocked = any(
        condition["id"] in _MOVEMENT_BLOCKING_CONDITIONS
        for condition in get_conditions(creature)
    )
    if blocked:
        if _SAVED_MOVEMENT_KEY not in creature:
            creature[_SAVED_MOVEMENT_KEY] = creature["movement_remaining"]
        creature["movement_remaining"] = 0
        return

    if _SAVED_MOVEMENT_KEY in creature:
        creature["movement_remaining"] = creature.pop(_SAVED_MOVEMENT_KEY)


def add_condition(
    creature: dict[str, Any],
    *,
    condition_id: str,
    source_id: str | None = None,
    duration: dict[str, Any] | None | object = _MISSING,
    effects: list[dict[str, Any]] | None = None,
    timing: dict[str, Any] | None = None,
) -> dict[str, Any]:
    _validate_condition_id(condition_id)

    resolved_duration = (
        {"kind": "permanent"}
        if duration is _MISSING or duration is None
        else duration
    )
    _validate_duration(resolved_duration)
    _validate_contextual_timing(resolved_duration, timing)

    resolved_effects = [] if effects is None else effects

    if not isinstance(resolved_effects, list):
        raise ValueError("effects must be a list")

    condition = {
        "id": condition_id,
        "source_id": source_id,
        "duration": dict(resolved_duration),
        "effects": list(resolved_effects),
    }
    if timing is not None:
        condition["_timing"] = dict(timing)

    conditions = get_conditions(creature)
    conditions.append(condition)
    creature["conditions"] = conditions
    sync_movement_with_conditions(creature)

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
    sync_movement_with_conditions(creature)

    return before - len(creature["conditions"])


def remove_condition_instance(
    creature: dict[str, Any],
    *,
    condition_index: int,
) -> dict[str, Any]:
    conditions = get_conditions(creature)

    if condition_index < 0 or condition_index >= len(conditions):
        raise ValueError("condition index out of range")

    removed = conditions.pop(condition_index)
    creature["conditions"] = conditions
    sync_movement_with_conditions(creature)
    return removed


def _cursor(timing: dict[str, Any]) -> tuple[int, int]:
    return timing["applied_round"], timing["applied_turn_index"]


def advance_condition_durations(
    creature: dict[str, Any],
    *,
    timing: str,
    current_round: int | None = None,
    current_turn_index: int | None = None,
) -> list[dict[str, Any]]:
    if timing not in TIMING_PHASES:
        raise ValueError(f"unknown condition timing: {timing}")
    if current_round is not None and (
        not isinstance(current_round, int)
        or isinstance(current_round, bool)
        or current_round < 1
    ):
        raise ValueError("current_round must be a positive integer")
    if current_turn_index is not None and (
        not isinstance(current_turn_index, int)
        or isinstance(current_turn_index, bool)
        or current_turn_index < 0
    ):
        raise ValueError("current_turn_index must be a non-negative integer")

    conditions = get_conditions(creature)
    expired: list[dict[str, Any]] = []
    remaining_conditions: list[dict[str, Any]] = []
    current_cursor = (
        (current_round, current_turn_index)
        if current_round is not None and current_turn_index is not None
        else None
    )

    for condition in conditions:
        duration = condition["duration"]
        kind = duration["kind"]
        condition_timing = condition.get("_timing")
        should_expire = False

        if kind == "turns" and timing == "turn_end":
            duration["remaining"] -= 1
            should_expire = duration["remaining"] <= 0
        elif kind in _CONTEXTUAL_DURATION_KINDS:
            if condition_timing is None or current_cursor is None:
                raise ValueError(
                    f"{kind} duration requires lifecycle timing context"
                )
            applied_cursor = _cursor(condition_timing)
            if kind == "until_end_of_turn":
                should_expire = (
                    timing == "turn_end" and current_cursor >= applied_cursor
                )
            elif kind == "until_start_of_turn":
                should_expire = (
                    timing == "turn_start" and current_cursor > applied_cursor
                )
            elif kind == "rounds":
                if (
                    timing == "round_end"
                    and current_round is not None
                    and current_round > condition_timing["applied_round"]
                ):
                    duration["remaining"] -= 1
                    should_expire = duration["remaining"] <= 0
        if should_expire:
            expired.append(condition)
        else:
            remaining_conditions.append(condition)

    creature["conditions"] = remaining_conditions
    sync_movement_with_conditions(creature)
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
    sync_movement_with_conditions(creature)

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

    if roll_type == "attack":
        return has_condition(creature, "poisoned") or has_condition(creature, "restrained")

    if roll_type == "ability_check":
        return has_condition(creature, "poisoned")

    return False
