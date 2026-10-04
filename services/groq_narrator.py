from __future__ import annotations

import logging
import re
import time
from typing import Any
from urllib.parse import urljoin

import httpx

from services.narrator import NarratorError

logger = logging.getLogger(__name__)


class GroqNarratorError(NarratorError):
    """Raised when Groq cannot return valid narrative text."""


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


def _safe_error_detail(exc: Exception) -> tuple[str, str]:
    status = getattr(exc, "status_code", None) or getattr(exc, "response", None)
    if hasattr(status, "status_code"):
        status = status.status_code
    status = status or getattr(exc, "code", None) or "unknown"
    detail = str(getattr(exc, "message", "") or exc).replace("\n", " ")[:240]
    detail = re.sub(r"(?i)authorization\s*[:=]\s*(?:bearer\s+)?\S+", "Authorization=[REDACTED]", detail)
    detail = re.sub(r"(?i)bearer\s+\S+", "Bearer [REDACTED]", detail)
    detail = re.sub(r"(?i)(api[_ -]?key)\s*[:=]\s*\S+", r"\1=[REDACTED]", detail)
    detail = re.sub(r"AIza[0-9A-Za-z_-]+|sk-[A-Za-z0-9_-]+", "[REDACTED]", detail)
    return str(status), detail


class GroqNarratorClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://api.groq.com/openai/v1",
        timeout_seconds: float = 30.0,
        max_output_tokens: int = 512,
        temperature: float = 0.7,
        transport: httpx.BaseTransport | None = None,
    ) -> None:
        if not api_key.strip() or not model.strip() or not base_url.strip():
            raise ValueError("Groq API key, model and base URL are required")
        if timeout_seconds <= 0:
            raise ValueError("Groq timeout must be positive")
        if max_output_tokens <= 0:
            raise ValueError("Groq max output tokens must be positive")
        if temperature < 0:
            raise ValueError("Groq temperature must not be negative")
        self.api_key = api_key
        self.model = model
        self.base_url = base_url.rstrip("/")
        self.timeout = httpx.Timeout(timeout_seconds)
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
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

        content = build_narrator_content(
            campaign_id=campaign_id,
            state=state,
            player_input=player_input,
            rule_resolution=rule_resolution,
            available_actions=available_actions,
        )
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": NARRATOR_SYSTEM_INSTRUCTION},
                {"role": "user", "content": content},
            ],
            "stream": False,
            "temperature": self.temperature,
            "max_tokens": self.max_output_tokens,
        }
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json",
        }
        started = time.perf_counter()
        action = rule_resolution.get("action")
        action_type = action.get("type") if isinstance(action, dict) else "none"
        logger.info(
            "NARRATOR_REQUEST_STARTED provider=groq model=%s action=%s campaign_id=%s request_id=%s",
            self.model,
            action_type,
            campaign_id,
            request_id or "none",
        )
        try:
            with httpx.Client(timeout=self.timeout, transport=self.transport) as client:
                response = client.post(
                    urljoin(f"{self.base_url}/", "chat/completions"),
                    headers=headers,
                    json=payload,
                )
                response.raise_for_status()
                body = response.json()
        except (httpx.HTTPError, ValueError) as exc:
            status, detail = _safe_error_detail(exc)
            logger.warning(
                "NARRATOR_REQUEST_FAILED provider=groq duration_ms=%.1f error_type=%s error_status=%s error_detail=%s request_id=%s",
                (time.perf_counter() - started) * 1000,
                type(exc).__name__,
                status,
                detail,
                request_id or "none",
            )
            raise GroqNarratorError("Groq narrator request failed") from exc
        try:
            text = body["choices"][0]["message"]["content"]
        except (KeyError, IndexError, TypeError) as exc:
            logger.warning(
                "NARRATOR_RESPONSE_PARSE_FAILED provider=groq duration_ms=%.1f request_id=%s",
                (time.perf_counter() - started) * 1000,
                request_id or "none",
            )
            raise GroqNarratorError("Groq narrator returned an invalid response") from exc
        if not isinstance(text, str) or not text.strip():
            logger.warning(
                "NARRATOR_RESPONSE_PARSE_FAILED provider=groq duration_ms=%.1f response_chars=0 request_id=%s",
                (time.perf_counter() - started) * 1000,
                request_id or "none",
            )
            raise GroqNarratorError("Groq narrator returned empty content")
        logger.info(
            "NARRATOR_RESPONSE_PARSED provider=groq duration_ms=%.1f response_chars=%d request_id=%s",
            (time.perf_counter() - started) * 1000,
            len(text.strip()),
            request_id or "none",
        )
        return text.strip()
