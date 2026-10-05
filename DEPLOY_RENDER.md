# Deploy do Rule Engine no Render

## Serviço

- **Runtime:** Python/FastAPI em Docker.
- **Comando:** `uvicorn rule_engine.app:app --host 0.0.0.0 --port $PORT`.
- **Blueprint:** `render.yaml`.
- **Health check:** `GET /health`.
- **Contrato mecânico:** `rule-resolution-v1`.
- **Regras:** exclusivamente D&D 2024 / PHB 2024 (`dnd-2024-phb`).

## Variáveis de ambiente

Configure no Web Service do Rule Engine:

```text
RULE_ENGINE_API_KEY=<secret para chamadas ao backend>
DATABASE_URL=<URL privada do PostgreSQL associado ao mesmo workspace/região>
GROQ_API_KEY=<secret do Groq>
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_BASE_URL=https://api.groq.com/openai/v1
GROQ_TIMEOUT_SECONDS=20
GROQ_MAX_OUTPUT_TOKENS=512
GROQ_TEMPERATURE=0.7
```

`DATABASE_URL` é obrigatória para criar campanhas, retomar sessões e executar turnos. Sem ela, esses endpoints falham fechados com HTTP 503; não há fallback stateless que aceite estado do cliente. O repositório não provisiona automaticamente um banco e o `render.yaml` não cria recurso Postgres.

Depois de o usuário provisionar ou selecionar um banco e configurar `DATABASE_URL` no Backend, aplique as migrações **antes** de habilitar o novo fluxo:

```bash
python scripts/migrate_campaigns.py
```

O comando é explícito, versionado e não roda no startup. Faça backup e confira o alvo de `DATABASE_URL` antes de executá-lo. Não copie a URL para logs, frontend, Git ou mensagens.

`RULE_ENGINE_API_KEY` protege a API do Rule Engine. `GROQ_API_KEY` é usada somente no servidor e nunca é enviada ao navegador ou incluída em respostas.

## Contrato de campanha/sessão C2

- `POST /v2/character/create`: recebe escolhas PHB 2024, um `X-Session-Token` aleatório e `Idempotency-Key`; grava campanha, sessão e snapshot inicial revision 0 em uma transação.
- `GET /v1/sessions/{session_id}`: exige `X-Session-Token` e devolve o snapshot canônico corrente, revisão, dados de exibição e histórico narrativo limitado.
- `POST /v1/game/turn`: exige `session_id`, `expected_revision`, `player_input` e `action`, além de `X-Session-Token` e `Idempotency-Key`. Não aceita `state`, `campaign_id` nem `available_actions` do cliente.
- O Backend carrega o snapshot do PostgreSQL, valida o token de sessão, deriva mecânicas sobre esse estado e confirma o novo snapshot imutável, evento de turno, revisão e resposta idempotente em uma única transação.
- Um replay com mesma chave e mesmo payload retorna a resposta gravada. Reutilizar a chave com outro payload ou enviar revisão antiga resulta em HTTP 409.
- O lock de linha é mantido durante resolução e narração para serializar turnos da mesma sessão. O timeout do narrador deve permanecer abaixo do timeout do Gateway.
- O token é uma credencial bearer de sessão sem identidade de conta; somente seu hash SHA-256 é persistido. O Frontend mantém o token em `sessionStorage` e nunca o envia ao narrador nem registra em logs.

## Smoke test

Crie primeiro uma sessão pelo Frontend PHB 2024 ou use um payload de criação validado, conservando o token e a chave de idempotência gerados pelo cliente. Para testar um turno, use o `session_id`, a revisão recebida e o token daquela sessão:

```bash
curl -i https://SEU-GATEWAY.onrender.com/v1/game/turn \
  -H 'Content-Type: application/json' \
  -H 'X-Session-Token: TOKEN_DA_SESSAO' \
  -H 'Idempotency-Key: CHAVE_UNICA_DO_TURNO' \
  -d '{
    "session_id": "UUID_DA_SESSAO",
    "expected_revision": 0,
    "player_input": "Observo cuidadosamente a clareira.",
    "action": null
  }'
```

A resposta inclui `campaign_id`, `session_id`, `revision`, `state` vindo do snapshot e `rule_resolution.schema_version = "rule-resolution-v1"`. Não envie nem aceite `state` ou bônus mecânicos controlados pelo cliente.

## Falha do narrador

A resolução mecânica acontece antes da chamada ao Groq. Se o Groq responder com erro, exceder o timeout ou retornar conteúdo inválido, o endpoint ainda retorna HTTP 200 com a resolução mecânica preservada e `narration_status: "unavailable"`. O Rule Engine continua sendo a autoridade para dados, modificadores, CD/DC, CA, dano, HP, estado e regras usadas.

## Segurança operacional

- Não faça commit de `.env`, chaves ou tokens.
- Não registre headers `X-Session-Token`, `Authorization` ou valores de secrets.
- Não configure secrets no frontend, Gateway público ou arquivos versionados.
- A configuração de Postgres, execução de migration em produção, merge e deploy não fazem parte desta branch C2.
