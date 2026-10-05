from __future__ import annotations

from typing import Any, Protocol


class NarratorError(RuntimeError):
    """Raised when the narrator cannot produce valid text."""


class UnavailableNarratorProvider:
    """Signal through the normal fallback path when no narrator is available."""

    def narrate(
        self,
        *,
        campaign_id: str,
        state: dict[str, Any],
        player_input: str,
        rule_resolution: dict[str, Any],
        available_actions: list[dict[str, Any]] | None = None,
        request_id: str | None = None,
    ) -> str:
        raise NarratorError("narrator provider is unavailable")


class NarratorProvider(Protocol):
    def narrate(
        self,
        *,
        campaign_id: str,
        state: dict[str, Any],
        player_input: str,
        rule_resolution: dict[str, Any],
        available_actions: list[dict[str, Any]] | None = None,
        request_id: str | None = None,
    ) -> str:
        """Narrate already-resolved facts without changing game state."""
