from __future__ import annotations

from typing import Any, Protocol


class NarratorError(RuntimeError):
    """Raised when a configured narrator cannot produce valid text."""


class NarratorProvider(Protocol):
    def narrate(
        self,
        *,
        campaign_id: str,
        state: dict[str, Any],
        player_input: str,
        rule_resolution: dict[str, Any],
        available_actions: list[dict[str, Any]] | None = None,
    ) -> str:
        """Narrate already-resolved facts without changing game state."""
