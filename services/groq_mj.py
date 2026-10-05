from __future__ import annotations

import json
import logging
import time
from typing import Any
from urllib.parse import urljoin

import httpx

from game.resolution_gate import ResolutionGateDecision
from services.narrator import NarratorError
from services.groq_narrator import _safe_error_detail

logger = logging.getLogger(__name__)


class GroqIntentError(NarratorError):
    """Raised when Groq cannot return a valid Resolution Gate decision."""


INTENT_SYSTEM_INSTRUCTION = """
Você é o cérebro de decisão narrativa de um Mestre de Jogo de D&D 2024.
Interprete a intenção do jogador considerando o estado, a cena, as criaturas,
o ambiente e as consequências relevantes. Decida apenas se é necessária uma
resolução mecânica. Não narre, não role dados e não produza nenhum valor
mecânico. Não produza HP, dano, ouro, CD, modificador, total, sucesso, falha
ou alteração de estado.

Responda SOMENTE um objeto JSON válido conforme este contrato:
{
  "schema_version": "resolution-gate-v1",
  "requires_resolution": false
}

Quando houver incerteza e consequência relevante, use:
{
  "schema_version": "resolution-gate-v1",
  "requires_resolution": true,
  "resolution": {"type": "skill_check", "skill": "stealth"}
}

Use somente type=skill_check ou type=ability_check. Para skill_check, informe
apenas a perícia; para ability_check, informe apenas a habilidade. O Backend e
o Rule Engine determinarão o restante da mecânica. A ausência de uma ação em
available_actions não torna uma intenção livre inválida. Ações triviais não
devem gerar rolagem.
""".strip()


class GroqIntentInterpreter:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout_seconds: float = 20.0,
        max_output_tokens: int = 256,
        temperature: float = 0.0,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key.strip() or not model.strip() or not base_url.strip():
            raise ValueError("Groq API key, model and base URL are required")
        if timeout_seconds <= 0 or max_output_tokens <= 0 or temperature < 0:
            raise ValueError("invalid Groq interpreter configuration")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds)
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.transport = transport

    def interpret(
        self,
        *,
        campaign_id: str,
        state: dict[str, Any],
        player_input: str,
        available_actions: list[dict[str, Any]] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
        from game.narrator import build_narrative_context

        context = build_narrative_context(
            state=state,
            player_input=player_input,
            rule_resolution={
                "schema_version": "rule-resolution-v1",
                "status": "needs_rule_validation",
                "reason": "resolution gate pending",
            },
            available_actions=available_actions,
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": INTENT_SYSTEM_INSTRUCTION},
                {"role": "user", "content": json.dumps(context, ensure_ascii=False, sort_keys=True)},
            ],
            "response_format": {"type": "json_object"},
            "stream": False,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
        }
        headers = {"Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json"}
        started = time.perf_counter()
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.post(
                    urljoin(f"{self.base_url}/", "chat/completions"),
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
            text = body["choices"][0]["message"]["content"]
            decision = ResolutionGateDecision.model_validate(json.loads(text))
        except (httpx.HTTPError, ValueError, KeyError, IndexError, TypeError) as exc:
            status, detail = _safe_error_detail(exc)
            logger.warning(
                "INTENT_REQUEST_FAILED provider=groq duration_ms=%.1f error_status=%s error_detail=%s request_id=%s",
                (time.perf_counter() - started) * 1000,
                status,
                detail,
                request_id or "none",
            )
            raise GroqIntentError("Groq intent decision was invalid") from exc
        logger.info(
            "INTENT_REQUEST_COMPLETED provider=groq duration_ms=%.1f requires_resolution=%s request_id=%s",
            (time.perf_counter() - started) * 1000,
            decision.requires_resolution,
            request_id or "none",
        )
        return decision.model_dump(mode="json", exclude_none=True)
