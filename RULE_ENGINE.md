# D&D 2024 Rule Engine API

Este serviço fornece busca de evidências nas fontes classificadas como 2024/2025 e rolagem de dados bruta. Ele não decide sucesso, fracasso, dano ou efeitos de jogo.

## Endpoints

- `GET /health` — estado do serviço e escopo de fontes pesquisáveis.
- `POST /v1/rules/search` — `{ "query": "concentration advantage", "limit": 8 }`.
- `GET /v1/rules/context?q=concentration%20advantage&limit=8`.
- `POST /v1/dice/roll` — rola dados, por exemplo `{ "expression": "1d20+5", "mode": "advantage" }`.
- `POST /v1/resolve` — atualmente falha de forma segura com `needs_rule_validation`; não inventa resolução mecânica.

## Escopo estrito de fontes

A busca filtra por padrão as edições explicitamente classificadas como `2024` e `2025`. Fontes sem edição explícita e fontes de outras edições não são retornadas, mesmo quando uma chamada fornece um filtro de edição. Quando há mais de uma fonte com o mesmo título e edição, a busca prefere a marcada como `canonical_candidate`.

Cada resultado inclui `source_id` e `edition` para permitir rastrear a proveniência da evidência.

A rolagem de dados é independente da resolução de regras. O endpoint retorna expressão normalizada, faces sorteadas, modificador e total; não marca acerto/crítico, não determina sucesso e não calcula dano ou condições. Vantagem/desvantagem é aceita somente para um d20.

A chave opcional é enviada em `X-API-Key`. No Render, configure `RULE_ENGINE_API_KEY` como secret e use o mesmo valor no serviço que fará as chamadas.
