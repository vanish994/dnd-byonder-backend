from __future__ import annotations

from typing import Final


MAX_LEVEL: Final = 20
XP_BY_LEVEL: Final[dict[int, int]] = {
    1: 0,
    2: 300,
    3: 900,
    4: 2_700,
    5: 6_500,
    6: 14_000,
    7: 23_000,
    8: 34_000,
    9: 48_000,
    10: 64_000,
    11: 85_000,
    12: 100_000,
    13: 120_000,
    14: 140_000,
    15: 165_000,
    16: 195_000,
    17: 225_000,
    18: 265_000,
    19: 305_000,
    20: 355_000,
}


def validate_level(level: int) -> int:
    if isinstance(level, bool) or not isinstance(level, int) or not 1 <= level <= MAX_LEVEL:
        raise ValueError(f"level must be between 1 and {MAX_LEVEL}")
    return level


def validate_experience_points(experience_points: int) -> int:
    if isinstance(experience_points, bool) or not isinstance(experience_points, int) or experience_points < 0:
        raise ValueError("experience points must be a non-negative integer")
    return experience_points


def proficiency_bonus_for_level(level: int) -> int:
    validate_level(level)
    return 2 + ((level - 1) // 4)


def level_for_experience(experience_points: int) -> int:
    validate_experience_points(experience_points)
    level = 1
    for candidate, threshold in XP_BY_LEVEL.items():
        if experience_points >= threshold:
            level = candidate
        else:
            break
    return level


def experience_for_level(level: int) -> int:
    validate_level(level)
    return XP_BY_LEVEL[level]


def next_level_experience(level: int) -> int | None:
    validate_level(level)
    if level == MAX_LEVEL:
        return None
    return XP_BY_LEVEL[level + 1]


def level_up_available(level: int, experience_points: int) -> bool:
    validate_level(level)
    validate_experience_points(experience_points)
    threshold = next_level_experience(level)
    return threshold is not None and experience_points >= threshold
