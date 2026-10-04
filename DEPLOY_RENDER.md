# Deploy no Render

## 1. Serviço de regras

1. No Render, crie um **Web Service** apontando para este repositório.
2. Use o blueprint `render.yaml` ou Dockerfile `Dockerfile.rule-engine`.
3. O serviço inicia com `uvicorn rule_engine.app:app --host 0.0.0.0 --port $PORT`.
4. Copie a URL pública do serviço, por exemplo `https://dnd-2024-rule-engine.onrender.com`.
5. Guarde o valor gerado de `RULE_ENGINE_API_KEY` como secret.

Teste:

```bash
curl https://SEU-RULE-ENGINE.onrender.com/health
curl -X POST https://SEU-RULE-ENGINE.onrender.com/v1/rules/search \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: SUA_RULE_ENGINE_API_KEY' \
  -d '{"query":"concentration advantage","limit":3}'
curl -X POST https://SEU-RULE-ENGINE.onrender.com/v1/dice/roll \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: SUA_RULE_ENGINE_API_KEY' \
  -d '{"expression":"1d20+5","mode":"advantage"}'
```

## 2. Proxy Mimo / narrador

1. Use o serviço existente do projeto `vanish994/Mimo-ai`, exposto em `https://dnd-mimo-narrator.onrender.com`.
2. Use o blueprint `render.yaml`.
3. Preencha os secrets Xiaomi: `SERVICE_TOKEN`, `USER_ID`, `XIAOMI_CHATBOT_PH` — ou as variantes plurais para rotação.
4. O Render gera `API_KEY`; use esse valor nas chamadas ao proxy.
5. `DND_NARRATOR_MODE=true` injeta automaticamente `prompts/dnd_narrator.md` como mensagem `system`.

Teste:

```bash
curl https://SEU-MIMO.onrender.com/health
curl https://SEU-MIMO.onrender.com/v1/models \
  -H 'Authorization: Bearer SUA_API_KEY'
```

## 3. Primeiro turno narrativo

```bash
curl -X POST https://SEU-MIMO.onrender.com/v1/chat/completions \
  -H 'Authorization: Bearer SUA_API_KEY' \
  -H 'Content-Type: application/json' \
  -d @- <<'JSON'
{
  "model": "mimo-v2.6-flash",
  "user": "campaign_123",
  "stream": false,
  "messages": [
    {
      "role": "user",
      "content": "<FATOS_RESOLVIDOS>{}</FATOS_RESOLVIDOS>\n\n<fala_do_jogador>Eu abro a porta lentamente.</fala_do_jogador>"
    }
  ]
}
JSON
```

O modo narrador é fail-closed: quando não há fatos resolvidos, o Mimo não deve afirmar que a ação foi bem-sucedida.

## Observação sobre o estado atual

O serviço de regras já pesquisa as fontes 2024/5.5 e 2025, mas `/v1/resolve` ainda retorna `needs_rule_validation`. Antes de habilitar resolução automática de ataques, dano, condições ou magia, é necessário validar as regras candidatas e implementar testes determinísticos para cada mecânica.

## 4. Marco 5 — configuração do backend

No serviço `dnd-2024-rule-engine`, adicione:

```text
MIMO_BASE_URL=https://dnd-mimo-narrator.onrender.com
MIMO_MODEL=mimo-v2.6-flash
MIMO_API_KEY=<API_KEY do serviço MiMo>
MIMO_TIMEOUT_SECONDS=30
```

`MIMO_API_KEY` deve ser configurada como secret. Ela é diferente de `RULE_ENGINE_API_KEY`, que continua protegendo os endpoints do backend.

O proxy correto deste projeto é o serviço `vanish994/Mimo-ai`, exposto em `https://dnd-mimo-narrator.onrender.com`. Não é necessário criar serviço, fazer deploy ou alterar o repositório do proxy para o Marco 5.

O modo narrador permanece configurado no proxy por `DND_NARRATOR_MODE=true` e `DND_NARRATOR_PROMPT_FILE=prompts/dnd_narrator.md`.

Smoke test do endpoint depois de configurar os secrets:

```bash
curl -X POST https://SEU-RULE-ENGINE.onrender.com/v1/game/turn \
  -H 'Content-Type: application/json' \
  -H 'X-API-Key: SUA_RULE_ENGINE_API_KEY' \
  -d '{
    "campaign_id": "campaign_123",
    "state": {"scene": "Uma porta bloqueia o corredor."},
    "player_input": "Eu abro a porta lentamente.",
    "action": null
  }'
```
