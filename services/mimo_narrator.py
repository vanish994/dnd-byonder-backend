from __future__ import annotations

from typing import Any
from urllib.parse import urljoin

import httpx

from services.narrator import NarratorError


class MimoNarratorError(NarratorError):
    """Raised when the narrator proxy cannot produce a valid narration."""


class MimoNarratorClient:
    def __init__(
        self,
        base_url: str,
        model: str,
        api_key: str,
        timeout_seconds: float = 30.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not base_url.strip() or not model.strip() or not api_key.strip():
            raise ValueError("MiMo base URL, model and API key are required")
        self.base_url = base_url.rstrip("/")
        self.model = model
        self.api_key = api_key
        self.timeout = httpx.Timeout(timeout_seconds)
        self.transport = transport

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
        from game.narrator import build_narrator_content

        payload = {
            "model": self.model,
            "user": campaign_id,
            "stream": False,
            "messages": [
                {
                    "role": "user",
                    "content": build_narrator_content(
                        campaign_id=campaign_id,
                        state=state,
                        player_input=player_input,
                        rule_resolution=rule_resolution,
                        available_actions=available_actions,
                    ),
                }
            ],
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.post(
                    urljoin(f"{self.base_url}/", "v1/chat/completions"),
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            raise MimoNarratorError("MiMo narrator request failed") from exc

        try:
            content = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            raise MimoNarratorError("MiMo narrator returned an invalid response") from exc
        if not isinstance(content, str) or not content.strip():
            raise MimoNarratorError("MiMo narrator returned empty content")
        return content.strip()
