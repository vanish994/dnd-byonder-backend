# Deploy do Rule Engine no Render

## Serviço

- **Runtime:** Python/FastAPI em Docker.
- **Comando:** `uvicorn rule_engine.app:app --host 0.0.0.0 --port $PORT`.
- **Blueprint:** `render.yaml`.
- **Health check:** `GET /health`.
- **Contrato mecânico:** `rule-resolution-v1`.

## Variáveis de ambiente

Configure no Web Service do Rule Engine:

```text
RULE_ENGINE_API_KEY=<secret para chamadas ao backend>
GROQ_API_KEY=<secret do Groq>
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_BASE_URL=https://api.groq.com/openai/v1
GROQ_TIMEOUT_SECONDS=30
GROQ_MAX_OUTPUT_TOKENS=512
GROQ_TEMPERATURE=0.7
```

`RULE_ENGINE_API_KEY` protege a API do Rule Engine. `GROQ_API_KEY` é usada
somente no servidor e nunca é enviada ao navegador ou incluída em respostas.
Não há seleção de provider: o único narrador suportado é Groq.

## Smoke test

```bash
curl -i https://SEU-RULE-ENGINE.onrender.com/health

curl -X POST https://SEU-RULE-ENGINE.onrender.com/v1/game/turn \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: SUA_RULE_ENGINE_API_KEY' \
  -d '{
    "campaign_id": "campaign_123",
    "state": {"scene": "Uma porta bloqueia o corredor."},
    "player_input": "Eu observo a porta lentamente.",
    "action": null
  }'
```

O turno deve preservar `rule_resolution.schema_version` como
`rule-resolution-v1`. Com `action: null`, o status esperado é
`needs_rule_validation`; o narrador não pode criar uma resolução mecânica.

## Falha do narrador

A resolução mecânica acontece antes da chamada ao Groq. Se o Groq responder
com erro, exceder o timeout ou retornar conteúdo inválido, o endpoint ainda
retorna HTTP 200 com a resolução mecânica preservada e
`narration_status: "unavailable"`. O Rule Engine continua sendo a autoridade
para d20, modificadores, CD/DC, AC, dano, HP, estado e regras usadas.

## Segurança operacional

- Não faça commit de `.env`, chaves ou tokens.
- Não registre headers `Authorization` nem valores de secrets.
- Não configure secrets no frontend, gateway público ou arquivos versionados.
- Para mudanças de modelo/timeout, altere somente variáveis de ambiente e faça
  o smoke test acima; não adicione lógica mecânica ao narrador.
