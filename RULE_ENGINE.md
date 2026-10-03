# D&D 2024 Rule Engine API

Este serviço fornece busca de evidências nas fontes classificadas como 2024/2025, rolagem de dados bruta e um resolver explícito para `ability_check.mvp.v1`. O endpoint de resolução emite os fatos mecânicos desse único caso; a busca não promove candidatos a regras executáveis.

## Endpoints

- `GET /health` — estado do serviço e escopo de fontes pesquisáveis.
- `POST /v1/rules/search` — `{ "query": "concentration advantage", "limit": 8 }`.
- `GET /v1/rules/context?q=concentration%20advantage&limit=8`.
- `POST /v1/dice/roll` — rola dados, por exemplo `{ "expression": "1d20+5", "mode": "advantage" }`.
- `POST /v1/resolve` — aceita texto livre (fail-closed com `needs_rule_validation`) ou o `ability_check` estruturado suportado; respostas são serializadas como `rule-resolution-v1`.

Para um ability check explícito válido, a resposta usa `schema_version`, `resolution_id`, `status`, `action`, `check`, `rolls`, `outcome` e `rules_used`. Não duplica os fatos em um campo `facts_resolvidos`; os campos do envelope são a fonte única do resultado.

## Escopo estrito de fontes

A busca filtra por padrão as edições explicitamente classificadas como `2024` e `2025`. Fontes sem edição explícita e fontes de outras edições não são retornadas, mesmo quando uma chamada fornece um filtro de edição. Quando há mais de uma fonte com o mesmo título e edição, a busca prefere a marcada como `canonical_candidate`.

Cada resultado inclui `source_id` e `edition` para permitir rastrear a proveniência da evidência.

A rolagem de dados é independente da resolução de regras. O endpoint retorna expressão normalizada, faces sorteadas, modificador e total; não marca acerto/crítico, não determina sucesso e não calcula dano ou condições. Vantagem/desvantagem é aceita somente para um d20.

A chave opcional é enviada em `X-API-Key`. No Render, configure `RULE_ENGINE_API_KEY` como secret e use o mesmo valor no serviço que fará as chamadas.
