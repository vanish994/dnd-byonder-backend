> **Nota histórica (C2.2+):** este documento descreve a implementação legada baseada em Groq. A implementação ativa usa o SDK oficial Google Gen AI/Gemini; não use este documento para reintroduzir Groq, Llama ou autoridade mecânica no narrador.

# Marco 5 — Game Orchestrator + Groq Narrator

**Status:** especificação para implementação

**Base obrigatória:** branch `feat/ability-check-rule-resolution-v1` (PR #3), não `main`.

**Objetivo:** adicionar o primeiro endpoint de turno sem transformar o Groq em motor de regras e sem duplicar o contrato `rule-resolution-v1`.

## 1. Decisões de arquitetura

1. **Rule Engine decide fatos mecânicos.** Ele continua sendo a única autoridade para rolagem, modificadores, CD, sucesso/falha e futuras mecânicas validadas.
2. **Orchestrator coordena.** Ele recebe o turno, preserva o estado, encaminha ações estruturadas ao resolver local e monta a mensagem do narrador.
3. **Groq narra.** O Groq não interpreta intenção mecânica, não escolhe CD, não rola dados e não pode declarar sucesso/falha sem receber uma resolução válida.
4. **O v1 não interpreta texto livre como regra.** `player_input` é enviado ao narrador. Uma regra só é acionada quando o cliente envia `action` em formato estruturado e validado. Isso evita que heurística, regex ou o próprio Groq virem um segundo Rule Engine.
5. **O contrato mecânico único é `rule-resolution-v1`.** O objeto retornado pelo Rule Engine é chamado de `rule_resolution` na resposta do turno. O bloco XML `<FATOS_RESOLVIDOS>` é somente o envelope de transporte desse mesmo objeto para o proxy narrativo; não é um segundo schema.
6. **O backend é estateless nesta fase.** O estado recebido é devolvido sem mutação persistente. Persistência, autenticação por campanha e concorrência ficam fora do Marco 5.

## 2. Árvore de arquivos

```text
services/
  __init__.py
  groq_narrator.py              # cliente HTTP síncrono do proxy

game/
  __init__.py
  contracts.py                  # modelos do turno e estado
  narrator.py                   # construção do prompt de transporte
  orchestrator.py               # fluxo do turno

rule_engine/
  app.py                        # expõe POST /v1/game/turn e injeta dependências

tests/
  test_groq_narrator.py
  test_game_orchestrator.py
  test_game_api.py

GAME_CONTRACT.md                # contrato público atualizado
RULE_ENGINE.md                  # endpoint novo e limites
DEPLOY_RENDER.md                # variáveis e smoke test
requirements.txt                # cliente HTTP, se escolhido
```

### Dependência recomendada

Usar `httpx` em modo síncrono para o cliente, com timeout explícito e testes via mock de transporte. Adicionar uma versão fixada compatível com o ambiente atual, por exemplo `httpx==0.28.1`. Não usar o SDK do Groq nem copiar código do proxy.

## 3. Contratos canônicos

### 3.1 `GameTurnRequest`

```json
{
  "campaign_id": "campaign_123",
  "state": {
    "scene": "Uma porta de madeira bloqueia o corredor.",
    "character": {"name": "Arin", "hp": 12},
    "history": []
  },
  "player_input": "Eu tento abrir a porta.",
  "action": null,
  "available_actions": []
}
```

Campos:

| Campo | Tipo | Regra |
|---|---|---|
| `campaign_id` | `str` | obrigatório, não vazio, máximo 128 caracteres |
| `state` | `dict[str, Any]` | default `{}`; tratado como entrada imutável |
| `player_input` | `str` | obrigatório, 1–4000 caracteres |
| `action` | `AbilityCheckAction \| null` | opcional; único tipo aceito no Marco 5 |
| `available_actions` | `list[dict]` | default `[]`; apenas repassado ao narrador/resposta, não executado automaticamente |

`action` reutiliza exatamente o modelo validado do PR #3:

```json
{
  "type": "ability_check",
  "ability": "strength",
  "dc": 15,
  "modifier": 3
}
```

Não aceitar `dc`, `modifier` ou `ability` extraídos de `player_input`. Não aceitar `advantage` neste MVP, pois o resolver do PR #3 não o modela.

### 3.2 Resolução mecânica (`rule-resolution-v1`)

Quando `action` está presente, o Orchestrator chama a função interna `resolve_request` do Rule Engine. A saída válida é exatamente a saída do PR #3, sem renomear, duplicar ou adicionar `facts_resolvidos`:

```json
{
  "schema_version": "rule-resolution-v1",
  "resolution_id": "uuid",
  "status": "resolved",
  "action": {"type": "ability_check", "ability": "strength"},
  "check": {"ability": "strength", "dc": 15, "modifier": 3},
  "rolls": [{"type": "d20", "result": 14}],
  "outcome": {"total": 17, "success": true},
  "rules_used": ["ability_check.mvp.v1"]
}
```

Nome canônico no código do turno: `rule_resolution`.

Quando não existe ação estruturada válida, `rule_resolution` na resposta continua sendo um envelope canônico `rule-resolution-v1`:

```json
{
  "schema_version": "rule-resolution-v1",
  "status": "needs_rule_validation",
  "reason": "No deterministic resolution was emitted because the requested rule has not been bound to a validated mechanic."
}
```

Para o transporte ao narrador, uma resolução que não esteja em `status == "resolved"` é serializada como `{}` dentro de `<FATOS_RESOLVIDOS>`. Esse `{}` significa **nenhum fato mecânico resolvido**, não sucesso, falha ou autorização para inventar um resultado.

### 3.3 `GameTurnResponse`

```json
{
  "campaign_id": "campaign_123",
  "narration": "A maçaneta resiste...",
  "rule_resolution": {
    "schema_version": "rule-resolution-v1",
    "status": "needs_rule_validation",
    "reason": "No deterministic resolution was emitted because the requested rule has not been bound to a validated mechanic."
  },
  "state": {
    "scene": "Uma porta de madeira bloqueia o corredor.",
    "character": {"name": "Arin", "hp": 12},
    "history": []
  },
  "available_actions": []
}
```

Regras:

- `narration` é sempre texto retornado pelo proxy, nunca fabricado pelo backend.
- `rule_resolution` contém sempre um envelope `rule-resolution-v1`: o objeto integral de resolução quando `status == "resolved"` ou o envelope `needs_rule_validation` quando não houve resolução determinística.
- `state` deve ser estruturalmente igual ao estado recebido no v1. Não escrever `state` em banco nem inserir automaticamente texto narrativo nele.
- `available_actions` é devolvido sem que o Orchestrator interprete ou execute ações futuras.
- Não expor API key, prompt interno, headers ou corpo bruto do proxy.

## 4. Mensagem enviada ao Groq

O cliente deve chamar:

```text
{GROQ_BASE_URL}/v1/chat/completions
```

O valor de `GROQ_BASE_URL` é a origem/base do serviço, sem o sufixo `/v1/chat/completions`; o cliente deve normalizar uma barra final para evitar `//`.

Payload v1:

```json
{
  "model": "llama-3.3-70b-versatile",
  "user": "campaign_123",
  "stream": false,
  "messages": [
    {
      "role": "user",
      "content": "<estado_da_campanha>{\"scene\":\"Uma porta...\"}</estado_da_campanha>\n\n<FATOS_RESOLVIDOS>{}</FATOS_RESOLVIDOS>\n\n<fala_do_jogador>Eu tento abrir a porta.</fala_do_jogador>"
    }
  ]
}
```

O backend **não** envia uma mensagem `system`: o proxy já injeta `prompts/dnd_narrator.md` quando `DND_NARRATOR_MODE=true`. O cliente deve extrair apenas `choices[0].message.content` da resposta compatível com Chat Completions.

Para uma ação resolvida, `<FATOS_RESOLVIDOS>` contém o JSON canônico integral de `rule_resolution`. Para `needs_rule_validation`, contém `{}`.

### Streaming

O Marco 5 implementa somente `stream: false`. A saída do endpoint do jogo é uma resposta JSON completa; streaming exige um contrato SSE no frontend e fica para uma fase posterior. O cliente deve rejeitar `stream=true` como configuração não suportada, não fingir que processa streaming.

## 5. Interfaces internas

### `services/groq_narrator.py`

```python
class GroqNarratorError(RuntimeError): ...

class GroqNarratorClient:
    def __init__(self, base_url: str, model: str, api_key: str,
                 timeout_seconds: float = 30.0): ...

    def narrate(self, *, campaign_id: str, state: dict[str, Any],
                player_input: str,
                rule_resolution: dict[str, Any]) -> str: ...
```

Requisitos:

- `Authorization: Bearer <GROQ_API_KEY>`;
- `Content-Type: application/json`;
- `stream: false` sempre;
- timeout de conexão e leitura configurável, default 30 segundos;
- não repetir automaticamente uma chamada que possa narrar um turno já resolvido;
- converter timeout, conexão, HTTP não-2xx, JSON inválido, ausência de `choices` ou conteúdo vazio em `GroqNarratorError`;
- nunca registrar a API key nem o prompt completo em logs de erro;
- tratar `GROQ_API_KEY` como obrigatório para ativar `/v1/game/turn`.

### `game/narrator.py`

```python
def build_narrator_content(
    *, state: dict[str, Any], player_input: str,
    rule_resolution: dict[str, Any],
) -> str: ...
```

Essa função é pura, determinística e testável. Deve usar `json.dumps(..., ensure_ascii=False, sort_keys=True)` para os blocos JSON. Não deve inferir regras.

### `game/orchestrator.py`

```python
class GameOrchestrator:
    def __init__(self, narrator: GroqNarratorClient): ...

    def turn(self, request: GameTurnRequest) -> GameTurnResponse: ...
```

Fluxo exato:

1. validar `GameTurnRequest` pelo Pydantic; ação estruturada malformada é erro de entrada, enquanto ausência de ação ou texto livre resulta em `needs_rule_validation`;
2. copiar o estado de entrada para impedir mutação acidental;
3. se `action` for `None`, não chamar Rule Engine;
4. se `action` existir, construir `ResolveRequest(action=action, state=state)` e chamar `resolve_request` localmente;
5. aceitar como fato somente resposta com `schema_version == "rule-resolution-v1"` e `status == "resolved"`;
6. montar a mensagem do narrador com o fato validado ou `{}` quando o envelope estiver em `needs_rule_validation`;
7. chamar `GroqNarratorClient.narrate`;
8. devolver a mesma campanha, narrativa, resolução, estado e ações disponíveis.

O Orchestrator não deve chamar `/v1/resolve` por HTTP dentro do mesmo processo. A função de domínio local evita loop de rede e mantém uma única implementação do resolver.

## 6. Endpoint HTTP

Adicionar em `rule_engine/app.py`:

```text
POST /v1/game/turn
```

- autenticar com `X-API-Key` usando a mesma função `authorize` do Rule Engine;
- validar o corpo com `GameTurnRequest`;
- construir o cliente/orchestrator por dependência de aplicação;
- retornar `GameTurnResponse` com HTTP 200;
- retornar 401 para chave inválida;
- retornar 422 para corpo inválido;
- retornar 502 para falha do proxy Groq, sem criar uma narrativa substituta;
- retornar 503 se a integração estiver desabilitada por configuração ausente;
- não alterar o comportamento de `/health`, `/v1/rules/*`, `/v1/dice/roll` ou `/v1/resolve`.

A configuração do cliente deve ser lida no processo (`GROQ_BASE_URL`, `GROQ_MODEL`, `GROQ_API_KEY`). Não hardcodar URL, modelo ou segredo. O default permitido para `GROQ_MODEL` é `llama-3.3-70b-versatile`; a URL não deve ter default de produção para evitar chamadas acidentais.

## 7. Variáveis Render

No serviço do backend, configurar como secrets/env vars:

```text
RULE_ENGINE_API_KEY=<chave que o frontend usa no backend>
GROQ_BASE_URL=https://api.groq.com/openai
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_API_KEY=<API_KEY do proxy Groq>
GROQ_TIMEOUT_SECONDS=30
```

`GROQ_API_KEY` é diferente de `RULE_ENGINE_API_KEY`. O primeiro autentica backend → proxy; o segundo autentica cliente → backend.

O `DND_NARRATOR_MODE=true` e `DND_NARRATOR_PROMPT_FILE=prompts/dnd_narrator.md` permanecem configuração do serviço Groq, não deste repositório.

## 8. Testes obrigatórios

### Cliente Groq (`tests/test_groq_narrator.py`)

1. payload correto para turno sem resolução, incluindo `stream: false`;
2. payload correto com `rule-resolution-v1` integral dentro de `<FATOS_RESOLVIDOS>`;
3. header Bearer e `Content-Type`;
4. extração de `choices[0].message.content`;
5. timeout/conexão/HTTP 4xx ou 5xx viram `GroqNarratorError`;
6. JSON sem `choices` ou conteúdo vazio falha;
7. API key não aparece na exceção/log.

### Orchestrator (`tests/test_game_orchestrator.py`)

1. turno puramente narrativo não chama o resolver e envia `{}` ao narrador;
2. ability check estruturado chama o resolver exatamente uma vez;
3. a resolução enviada é a do PR #3, sem `facts_resolvidos` duplicado;
4. texto livre nunca é convertido em `ability_check`;
5. estado de entrada permanece inalterado e é devolvido preservado;
6. resolução `needs_rule_validation` não chega como fato ao narrador;
7. falha do Groq é propagada como erro de integração, sem narrativa inventada;
8. `available_actions` é preservado sem execução.

### API (`tests/test_game_api.py`)

1. `POST /v1/game/turn` retorna 200 no turno narrativo;
2. action inválida retorna 422;
3. chave inválida retorna 401;
4. Groq indisponível retorna 502;
5. configuração ausente retorna 503;
6. endpoints atuais continuam passando.

## 9. Critérios de aceite

- [ ] PR #3 está disponível como base e seus testes continuam passando.
- [ ] `rule-resolution-v1` é o único contrato mecânico versionado.
- [ ] Não existe campo simultâneo `facts_resolved`/`facts_resolvidos` na resposta.
- [ ] Groq nunca recebe responsabilidade de rolar, escolher CD ou decidir sucesso/falha.
- [ ] Texto livre não aciona resolver automaticamente.
- [ ] `/v1/game/turn` usa autenticação e variáveis de ambiente separadas.
- [ ] Falha do Groq é fail-closed e não produz narrativa de fallback.
- [ ] Nenhum estado é persistido ou mutado implicitamente no Marco 5.
- [ ] Testes unitários e de API passam; `compileall`, lint e `git diff --check` passam.
- [ ] Smoke test contra o Groq real usa chave do Render sem expor segredo e confirma resposta não vazia.
- [ ] Só depois da validação a branch `feat/groq-narrator-orchestrator` pode abrir PR.

## 10. Procedimento de implementação posterior

1. Criar a branch a partir de `origin/feat/ability-check-rule-resolution-v1`:

   ```bash
   git switch -c feat/groq-narrator-orchestrator \
     origin/feat/ability-check-rule-resolution-v1
   ```

2. Implementar primeiro contratos e montagem da mensagem, com testes puros.
3. Implementar o cliente Groq mockável.
4. Implementar o Orchestrator usando o resolver local.
5. Expor o endpoint e adicionar testes HTTP.
6. Atualizar documentação e Render.
7. Rodar a suíte completa e o smoke test real.
8. Inspecionar diff e só então publicar branch/PR.

Nenhuma alteração no GitHub faz parte desta especificação ou desta fase.
