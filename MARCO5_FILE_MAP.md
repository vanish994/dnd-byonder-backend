# Marco 5 — Mapa de impacto por arquivo

**Base de implementação:** `origin/feat/ability-check-rule-resolution-v1` (PR #3)

**Escopo desta fase:** inspeção e planejamento. Nenhum arquivo do GitHub foi alterado.

## 1. Arquivos a criar

| Arquivo | Responsabilidade | Observação |
|---|---|---|
| `services/__init__.py` | Marcar o pacote de serviços | Pode ser vazio. |
| `services/mimo_narrator.py` | Cliente HTTP do proxy MiMo | Bearer auth, timeout, `stream=false`, normalização de erros e extração de `choices[0].message.content`. |
| `game/__init__.py` | Marcar o pacote de domínio do jogo | Pode ser vazio. |
| `game/contracts.py` | Contratos Pydantic do turno | `GameTurnRequest`, `GameTurnResponse`, `CampaignState` e referência ao `AbilityCheckAction` do Rule Engine; `rule_resolution` sempre usa envelope v1. |
| `game/narrator.py` | Construção determinística da mensagem | Serializa estado, `rule-resolution-v1` ou `{}` e fala do jogador. Não interpreta regras. |
| `game/orchestrator.py` | Coordenação do turno | Chama o resolver local somente para ação estruturada e depois o cliente MiMo. |
| `tests/test_mimo_narrator.py` | Testes unitários do cliente MiMo | Usa transporte/mock; não chama o proxy real. |
| `tests/test_game_orchestrator.py` | Testes do fluxo de domínio | Verifica turno narrativo, ability check, estado e fail-closed. |
| `tests/test_game_api.py` | Testes do endpoint HTTP | Verifica 200, 401, 422, 502 e 503. |

## 2. Arquivos a alterar

| Arquivo | Alteração prevista | Limite de compatibilidade |
|---|---|---|
| `rule_engine/app.py` | Importar contratos/orchestrator, criar configuração do MiMo e expor `POST /v1/game/turn`. | Não alterar a lógica existente de `/v1/resolve`, `/v1/dice/roll`, busca ou health. Reutilizar `authorize` e `resolve_request`. |
| `requirements.txt` | Adicionar cliente HTTP fixado, preferencialmente `httpx==0.28.1`. | Não remover FastAPI ou Uvicorn. |
| `GAME_CONTRACT.md` | Documentar `GameTurnRequest`, `GameTurnResponse`, `rule_resolution` e o envelope `<FATOS_RESOLVIDOS>`. | Remover exemplos legados que sugiram `facts_resolved` paralelo. |
| `RULE_ENGINE.md` | Documentar `POST /v1/game/turn`, autenticação, limites do v1 e códigos de erro. | Manter a documentação dos endpoints existentes. |
| `DEPLOY_RENDER.md` | Adicionar `MIMO_BASE_URL`, `MIMO_MODEL`, `MIMO_API_KEY` e `MIMO_TIMEOUT_SECONDS`. | Não confundir `MIMO_API_KEY` com `RULE_ENGINE_API_KEY`. |
| `render.yaml` | Declarar as variáveis não secretas e placeholders/secrets necessários, conforme o padrão já usado pelo projeto. | Não colocar chaves reais no repositório. |

## 3. Arquivos preservados

Os arquivos abaixo não precisam de alteração para o Marco 5:

| Arquivo | Motivo |
|---|---|
| `rule_engine/dice.py` | Já fornece rolagem segura e injetável; não decide regras. |
| `rule_engine/source_policy.py` | Política de fontes do Rule Engine permanece independente do narrador. |
| `tests/test_dice.py` | Testa o comportamento existente da rolagem. |
| `tests/test_source_policy.py` | Testa a política estrita 2024/2025. |
| `dnd2024_knowledge_base/**` | Base de conhecimento não deve ser alterada pelo orquestrador. |
| `Dockerfile.rule-engine` | O comando atual continua servindo o mesmo app FastAPI. Só revisar se a dependência adicionada exigir mudança de build. |
| `render.rule-engine.yaml` | Preservar até verificar se é um blueprint alternativo ainda utilizado. |
| `README.md` | Pode permanecer mínimo; só alterar se o projeto decidir centralizar nele o quickstart do Marco 5. |

## 4. Dependências internas

```text
rule_engine/app.py
    ├── game.contracts
    ├── game.orchestrator
    │     ├── game.narrator
    │     ├── rule_engine.app.resolve_request
    │     └── services.mimo_narrator
    └── authorize
```

### Regra contra dependência circular

O `GameOrchestrator` não deve importar o módulo inteiro `rule_engine.app` se isso criar ciclo de importação. A implementação deve escolher uma destas formas, em ordem de preferência:

1. extrair o domínio de resolução do PR #3 para um módulo neutro, por exemplo `rule_engine/resolver.py`, preservando a saída byte a byte e os testes; ou
2. injetar uma função `resolve_action` no construtor do Orchestrator, com `rule_engine.app.resolve_request` fornecido pelo endpoint; ou
3. importar a função somente após a inicialização, apenas se os testes confirmarem que não há ciclo.

A opção escolhida não pode criar um segundo resolver nem alterar o contrato do PR #3.

## 5. Contratos que não podem reaparecer

Não criar, retornar ou encaminhar:

- `facts_resolved`;
- `facts_resolvidos`;
- `rule_id` legado;
- um objeto de resolução alternativo ao `rule-resolution-v1`;
- sucesso/falha inferido de `player_input`;
- chamada `player_input → MiMo → decisão mecânica`;
- chamada HTTP interna para `/v1/resolve` quando o endpoint e o resolver estão no mesmo processo.

## 6. Sequência de implementação recomendada

1. Criar `game/narrator.py` e seus testes puros.
2. Criar `game/contracts.py` e validar payloads.
3. Criar `services/mimo_narrator.py` e seus testes com mock.
4. Resolver a dependência de importação por injeção ou extração neutra.
5. Criar `game/orchestrator.py` e testar todos os caminhos.
6. Alterar `rule_engine/app.py` para expor o endpoint.
7. Atualizar dependências e documentação/Render.
8. Rodar a suíte existente do PR #3 e a nova suíte.
9. Só depois executar o smoke test contra o MiMo real.

## 7. Estado da inspeção

- [x] Branch do PR #3 identificada.
- [x] Arquivos existentes listados.
- [x] Contrato `rule-resolution-v1` confirmado.
- [x] `facts_resolvidos` legado ausente na saída do PR #3.
- [x] `rule_engine/dice.py` confirmado como rolagem sem adjudicação.
- [x] Proxy MiMo real responde em `/health` e `/v1/models`.
- [x] Nenhuma alteração enviada ao GitHub.
- [ ] Implementação do Marco 5 — fase posterior.
