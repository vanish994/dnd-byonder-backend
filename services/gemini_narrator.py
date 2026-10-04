from __future__ import annotations

from typing import Any, Protocol

from google import genai
from google.genai import types

from services.narrator import NarratorError


class GeminiNarratorError(NarratorError):
    """Raised when Gemini cannot return a valid narrative."""


NARRATOR_SYSTEM_INSTRUCTION = """
Você é o narrador de um RPG solo de D&D.

Você não é o motor de regras e não é autoridade mecânica.
Nunca role dados. Nunca invente resultados mecânicos, dano, bônus, CD, HP,
condições, recursos ou alterações de estado. Nunca transforme texto livre em
uma ação mecânica. Use somente os fatos fornecidos pelo Rule Engine.

Quando houver rule_resolution com status resolved, narre a consequência
narrativa daquele resultado. Quando status for needs_rule_validation, não
invente uma resolução: descreva apenas a cena, ambiente, NPCs e consequências
narrativas compatíveis com o estado fornecido.

O contrato rule-resolution-v1, o estado, a rule_resolution e as ações disponíveis pertencem ao Rule Engine.
Se houver conflito entre uma interpretação sua e os fatos resolvidos, os fatos
do Rule Engine têm prioridade. Responda somente com narrativa em texto.
""".strip()


class GeminiClient(Protocol):
    models: Any


class GeminiNarratorClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        timeout_seconds: float = 30.0,
        max_output_tokens: int = 512,
        temperature: float = 0.7,
        client: GeminiClient | None = None,
    ) -> None:
        if not api_key.strip() or not model.strip():
            raise ValueError("Gemini API key and model are required")
        if timeout_seconds <= 0:
            raise ValueError("Gemini timeout must be positive")
        self.model = model
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.client = client or genai.Client(api_key=api_key)

    def narrate(
        self,
        *,
        campaign_id: str,
        state: dict[str, Any],
        player_input: str,
        rule_resolution: dict[str, Any],
        available_actions: list[dict[str, Any]] | None = None,
    ) -> str:
        from game.narrator import build_narrator_content

        content = build_narrator_content(
            campaign_id=campaign_id,
            state=state,
            player_input=player_input,
            rule_resolution=rule_resolution,
            available_actions=available_actions,
        )
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=content,
                config=types.GenerateContentConfig(
                    system_instruction=NARRATOR_SYSTEM_INSTRUCTION,
                    temperature=self.temperature,
                    max_output_tokens=self.max_output_tokens,
                    http_options=types.HttpOptions(
                        timeout=int(self.timeout_seconds * 1000),
                    ),
                ),
            )
        except Exception as exc:
            raise GeminiNarratorError("Gemini narrator request failed") from exc

        text = getattr(response, "text", None)
        if not isinstance(text, str) or not text.strip():
            raise GeminiNarratorError("Gemini narrator returned empty content")
        return text.strip()
