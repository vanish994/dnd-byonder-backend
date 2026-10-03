"""Safe, bounded dice roller. It returns rolls only; it never adjudicates game rules."""

from __future__ import annotations

import re
import secrets
from typing import Callable, Literal


RollMode = Literal["normal", "advantage", "disadvantage"]
MAX_DICE = 100
MAX_SIDES = 1000
MAX_MODIFIER = 100_000

_DICE_RE = re.compile(
    r"^\s*(?:(?P<count>\d*)d(?P<sides>\d+))\s*"
    r"(?:(?P<sign>[+-])\s*(?P<modifier>\d+))?\s*$",
    re.IGNORECASE,
)


class DiceExpressionError(ValueError):
    """Raised when a dice expression or roll mode is not supported."""


def parse_dice_expression(expression: str) -> tuple[int, int, int, str]:
    """Parse NdS with an optional signed modifier, e.g. d20 or 2d6+3."""
    if not isinstance(expression, str) or len(expression) > 50:
        raise DiceExpressionError("expression must be a string of at most 50 characters")

    match = _DICE_RE.fullmatch(expression)
    if match is None:
        raise DiceExpressionError("use dice notation NdS with an optional +N or -N modifier")

    count = int(match.group("count") or "1")
    sides = int(match.group("sides"))
    modifier = int(match.group("modifier") or "0")
    if match.group("sign") == "-":
        modifier = -modifier

    if not 1 <= count <= MAX_DICE:
        raise DiceExpressionError(f"dice count must be between 1 and {MAX_DICE}")
    if not 2 <= sides <= MAX_SIDES:
        raise DiceExpressionError(f"die sides must be between 2 and {MAX_SIDES}")
    if abs(modifier) > MAX_MODIFIER:
        raise DiceExpressionError(f"modifier magnitude must not exceed {MAX_MODIFIER}")

    normalized = f"{count}d{sides}"
    if modifier > 0:
        normalized += f"+{modifier}"
    elif modifier < 0:
        normalized += str(modifier)
    return count, sides, modifier, normalized


def roll_dice(
    expression: str,
    mode: RollMode = "normal",
    *,
    randbelow: Callable[[int], int] | None = None,
) -> dict[str, object]:
    """Roll bounded dice using secrets.randbelow; injection supports deterministic tests."""
    count, sides, modifier, normalized = parse_dice_expression(expression)
    if mode not in ("normal", "advantage", "disadvantage"):
        raise DiceExpressionError("mode must be normal, advantage, or disadvantage")
    if mode != "normal" and (count != 1 or sides != 20):
        raise DiceExpressionError("advantage/disadvantage is supported only for one d20")

    random_below = randbelow or secrets.randbelow
    roll_count = 2 if mode != "normal" else count
    rolls: list[int] = []
    for _ in range(roll_count):
        value = random_below(sides)
        if not isinstance(value, int) or not 0 <= value < sides:
            raise RuntimeError("random source returned a value outside its requested range")
        rolls.append(value + 1)

    selected_roll: int | None = None
    if mode == "advantage":
        selected_roll = max(rolls)
        total = selected_roll + modifier
    elif mode == "disadvantage":
        selected_roll = min(rolls)
        total = selected_roll + modifier
    else:
        total = sum(rolls) + modifier

    return {
        "expression": normalized,
        "mode": mode,
        "rolls": rolls,
        "selected_roll": selected_roll,
        "modifier": modifier,
        "total": total,
    }
