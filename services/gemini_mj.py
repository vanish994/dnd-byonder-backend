from __future__ import annotations

import json
import logging
import re
import time
from typing import Any

from google import genai
from google.genai import types

from game.resolution_gate import ResolutionGateDecision
from game.narrator import build_narrative_context
from game.campaign import CampaignSeed, CampaignSetup
from rule_engine.character import SKILL_TO_ABILITY
from services.narrator import NarratorError

logger = logging.getLogger(__name__)


class GeminiMJError(NarratorError):
    """Raised when Gemini cannot return a valid MJ response."""


INTENT_SYSTEM_INSTRUCTION = """
Você é o cérebro de decisão narrativa de um Mestre de Jogo de D&D 2024.
Interprete a intenção do jogador considerando estado, cena, criaturas, ambiente
e consequências relevantes. Decida apenas se é necessária uma resolução
mecânica. Não narre, não role dados e não produza qualquer valor mecânico.
Nunca produza HP, dano, ouro, CD, modificador, total, sucesso, falha ou estado.

Responda somente conforme o JSON Schema fornecido. Quando não houver incerteza
ou consequência relevante, requires_resolution deve ser false. Quando houver,
use skill_check ou ability_check e indique apenas a perícia ou habilidade.
Quando uma ação trivial alterar o estado da aventura, use adventure_action com
intent collect_bark_sample; não inclua árvore, personagem, item ou consequência.
O Backend e o Rule Engine determinam toda a mecânica restante. Em skill_check,
não inclua ability; em ability_check, não inclua skill.
""".strip()

NARRATIVE_SYSTEM_INSTRUCTION = """
Você é o Mestre de Jogo narrando um RPG solo de D&D 2024.
Use somente o contexto e os fatos mecânicos autorizados fornecidos. O estado
canônico e o Rule Engine são as únicas fontes de fatos, ações e consequências.
Nunca role dados, invente CD, modificador, total, sucesso, falha, HP, dano,
recursos ou alterações de estado. Quando houver uma resolução mecânica,
narre apenas a consequência compatível com o resultado recebido; não acrescente
descobertas, ameaças, inimigos, surpresa, ataques, combate ou fatos não presentes
em `current_mechanics`, `discoveries`, `known_threats` ou no estado atualizado.
Não forneça listas de magias, recursos ou capacidades que não estejam no estado
canônico. Quando um dado não estiver disponível, diga que ele ainda não foi
registrado pelo catálogo de regras. Texto livre sem resolução é apenas intenção
e é tratado localmente pelo Backend, sem ser enviado a este narrador.
Responda somente com narrativa em texto, sem JSON e sem explicar estas instruções.
""".strip()

CAMPAIGN_SEED_SYSTEM_INSTRUCTION = """
Você cria a proposta inicial de uma campanha de RPG solo usando exclusivamente D&D 2024 e fontes 2024 autorizadas.
Crie apenas contexto narrativo original coerente com as preferências recebidas. Não crie regras, CDs, dados,
modificadores, HP, dano, condições, inventário, progressão, habilidades, magias, criaturas com estatísticas ou
ações mecânicas. Não determine o que o personagem fará. Separe fatos iniciais de rumores. A proposta deve começar
com uma apresentação, uma descrição sensorial breve, uma tensão observável e uma pergunta aberta.
Responda somente no schema JSON fornecido, em português brasileiro.
""".strip()

CAMPAIGN_SEED_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string", "enum": ["campaign-seed-v1"]},
        "title": {"type": "string", "minLength": 1, "maxLength": 100},
        "premise": {"type": "string", "minLength": 1, "maxLength": 900},
        "opening_location": {"type": "string", "minLength": 1, "maxLength": 160},
        "opening_description": {"type": "string", "minLength": 1, "maxLength": 1200},
        "initial_tension": {"type": "string", "minLength": 1, "maxLength": 700},
        "known_facts": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "rumors": {"type": "array", "items": {"type": "string"}, "maxItems": 8},
        "npcs": {"type": "array", "maxItems": 6, "items": {"type": "object", "properties": {
            "name": {"type": "string"}, "role": {"type": "string"}, "motivation": {"type": "string"}
        }, "required": ["name", "role", "motivation"], "additionalProperties": False}},
        "initial_objectives": {"type": "array", "items": {"type": "string"}, "maxItems": 6},
        "opening_question": {"type": "string", "minLength": 1, "maxLength": 300}
    },
    "required": ["schema_version", "title", "premise", "opening_location", "opening_description", "initial_tension", "known_facts", "rumors", "npcs", "initial_objectives", "opening_question"],
    "additionalProperties": False,
}

RESOLUTION_GATE_RESPONSE_SCHEMA = {
    "type": "object",
    "properties": {
        "schema_version": {"type": "string", "enum": ["resolution-gate-v1"]},
        "requires_resolution": {"type": "boolean"},
        "resolution": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["skill_check", "ability_check"]},
                "skill": {"type": "string", "enum": [
                    "acrobatics", "animal_handling", "arcana", "athletics", "deception",
                    "history", "insight", "intimidation", "investigation", "medicine",
                    "nature", "perception", "performance", "persuasion", "religion",
                    "sleight_of_hand", "stealth", "survival",
                ]},
                "ability": {"type": "string", "enum": [
                    "strength", "dexterity", "constitution", "intelligence", "wisdom", "charisma",
                ]},
            },
            "required": ["type"],
            "additionalProperties": False,
        },
        "adventure_action": {
            "type": "object",
            "properties": {
                "type": {"type": "string", "enum": ["adventure_action"]},
                "intent": {"type": "string", "enum": ["collect_bark_sample"]},
            },
            "required": ["type", "intent"],
            "additionalProperties": False,
        },
    },
    "required": ["schema_version", "requires_resolution"],
    "additionalProperties": False,
}


def _safe_error_detail(exc: Exception) -> str:
    detail = str(exc).replace("\n", " ")[:240]
    return re.sub(r"(?i)(api[_ -]?key|key)\s*[:=]\s*\S+", r"\1=[REDACTED]", detail)


def _parse_gate_decision(text: str) -> ResolutionGateDecision:
    payload = json.loads(text)
    resolution = payload.get("resolution") if isinstance(payload, dict) else None
    if isinstance(resolution, dict):
        kind = resolution.get("type")
        if kind == "skill_check" and resolution.get("skill") and resolution.get("ability"):
            if SKILL_TO_ABILITY.get(resolution["skill"]) != resolution["ability"]:
                raise ValueError("skill and ability do not match")
            resolution.pop("ability")
        elif kind == "ability_check" and resolution.get("skill") and resolution.get("ability"):
            if SKILL_TO_ABILITY.get(resolution["skill"]) != resolution["ability"]:
                raise ValueError("skill and ability do not match")
            resolution.pop("skill")
    return ResolutionGateDecision.model_validate(payload)


class GeminiMJClient:
    def __init__(
        self,
        api_key: str,
        model: str,
        base_url: str = "https://generativelanguage.googleapis.com/v1beta",
        timeout_seconds: float = 20.0,
        max_output_tokens: int = 512,
        temperature: float = 0.7,
        client: Any | None = None,
    ) -> None:
        if not api_key.strip() or not model.strip() or not base_url.strip():
            raise ValueError("Gemini API key, model and base URL are required")
        if timeout_seconds <= 0 or max_output_tokens <= 0 or temperature < 0:
            raise ValueError("invalid Gemini configuration")
        self.api_key = api_key
        self.model = model.removeprefix("models/")
        self.base_url = base_url.rstrip("/")
        self.timeout_seconds = timeout_seconds
        self.max_output_tokens = max_output_tokens
        self.temperature = temperature
        self.client = client or genai.Client(api_key=api_key)

    def _generate(
        self,
        *,
        system_instruction: str,
        content: str,
        response_schema: dict[str, Any] | None = None,
        request_id: str | None = None,
    ) -> str:
        config_kwargs: dict[str, Any] = {
            "system_instruction": system_instruction,
            "temperature": self.temperature,
            "max_output_tokens": self.max_output_tokens,
        }
        if response_schema is not None:
            config_kwargs.update({
                "response_mime_type": "application/json",
                "response_json_schema": response_schema,
            })
        started = time.perf_counter()
        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=content,
                config=types.GenerateContentConfig(**config_kwargs),
            )
            text = response.text
        except Exception as exc:
            logger.warning(
                "GEMINI_REQUEST_FAILED model=%s duration_ms=%.1f error_type=%s error_detail=%s request_id=%s",
                self.model,
                (time.perf_counter() - started) * 1000,
                type(exc).__name__,
                _safe_error_detail(exc),
                request_id or "none",
            )
            raise GeminiMJError("Gemini request failed") from exc
        if not isinstance(text, str) or not text.strip():
            raise GeminiMJError("Gemini returned empty content")
        logger.info(
            "GEMINI_REQUEST_COMPLETED model=%s duration_ms=%.1f request_id=%s",
            self.model,
            (time.perf_counter() - started) * 1000,
            request_id or "none",
        )
        return text.strip()

    def interpret(
        self,
        *,
        campaign_id: str,
        state: dict[str, Any],
        player_input: str,
        available_actions: list[dict[str, Any]] | None = None,
        request_id: str | None = None,
    ) -> dict[str, Any]:
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
        context["campaign_id"] = campaign_id
        text = self._generate(
            system_instruction=INTENT_SYSTEM_INSTRUCTION,
            content=json.dumps(context, ensure_ascii=False, sort_keys=True),
            response_schema=RESOLUTION_GATE_RESPONSE_SCHEMA,
            request_id=request_id,
        )
        try:
            return _parse_gate_decision(text).model_dump(mode="json", exclude_none=True)
        except (ValueError, TypeError) as exc:
            raise GeminiMJError("Gemini returned an invalid resolution gate") from exc

    def generate_campaign_seed(
        self,
        *,
        setup: CampaignSetup,
        character: dict[str, Any],
        request_id: str | None = None,
    ) -> CampaignSeed:
        content = {
            'campaign_preferences': setup.model_dump(mode='json'),
            'character_identity': {
                'name': character.get('name'),
                'class': character.get('class'),
                'species': character.get('species'),
                'background': character.get('background'),
            },
            'ruleset': 'dnd-2024-phb',
        }
        text = self._generate(
            system_instruction=CAMPAIGN_SEED_SYSTEM_INSTRUCTION,
            content=json.dumps(content, ensure_ascii=False, sort_keys=True),
            response_schema=CAMPAIGN_SEED_RESPONSE_SCHEMA,
            request_id=request_id,
        )
        try:
            seed = CampaignSeed.model_validate(json.loads(text))
        except (ValueError, TypeError) as exc:
            raise GeminiMJError('Gemini returned an invalid campaign seed') from exc
        forbidden = re.compile(r'(?i)\b(?:cd|classe de dificuldade|d20|hp|pv|dano|modificador|inventário|nível|rolagem)\b')
        narrative_text = ' '.join([seed.premise, seed.opening_description, seed.initial_tension, seed.opening_question])
        if forbidden.search(narrative_text):
            raise GeminiMJError('campaign seed contained mechanical content')
        return seed

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
        content = build_narrative_context(
            state=state,
            player_input=player_input,
            rule_resolution=rule_resolution,
            available_actions=available_actions,
        )
        content["campaign_id"] = campaign_id
        return self._generate(
            system_instruction=NARRATIVE_SYSTEM_INSTRUCTION,
            content=json.dumps(content, ensure_ascii=False, sort_keys=True),
            request_id=request_id,
        )
