from __future__ import annotations

from copy import deepcopy
from typing import Any


ADVENTURE_CATALOG_SCHEMA_VERSION = "adventure-catalog-v1"
REDWOOD_ADVENTURE_ID = "dragon-delves-death-at-sunset"

# Catalog metadata is server-owned. It is intentionally compact: the full adventure
# text is never sent to the model or trusted from the client.
_ADVENTURES: tuple[dict[str, Any], ...] = (
    {
        "id": REDWOOD_ADVENTURE_ID,
        "title": "Morte ao Pôr do Sol",
        "source": "Dragon Delves",
        "recommended_level": 1,
        "estimated_sessions": "1-2",
        "environment": "floresta",
        "ruleset": "dnd-2024-phb",
        "summary": "Uma investigação começa na Redwood Watch e segue em direção ao bosque.",
        "entry_hook": "Você chega à Redwood Watch para investigar a corrupção e os desaparecimentos.",
        "initial_scene_id": "redwood-watch",
    },
)


def list_adventures() -> dict[str, Any]:
    return {
        "schema_version": ADVENTURE_CATALOG_SCHEMA_VERSION,
        "ruleset": "dnd-2024-phb",
        "adventures": deepcopy(list(_ADVENTURES)),
    }


def get_adventure(adventure_id: str) -> dict[str, Any]:
    for adventure in _ADVENTURES:
        if adventure["id"] == adventure_id:
            return deepcopy(adventure)
    raise ValueError("adventure is not available in the server-owned catalog")
