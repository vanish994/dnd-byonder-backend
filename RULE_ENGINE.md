# D&D 2024 Rule Engine API

Este serviço expõe busca na base SQLite sem incluir a edição de 2014.

## Endpoints

- `GET /health`
- `POST /v1/rules/search` — `{ "query": "concentration advantage", "limit": 8 }`
- `GET /v1/rules/context?q=concentration%20advantage&limit=8`
- `POST /v1/resolve` — atualmente falha de forma segura com `needs_rule_validation`; não inventa resolução mecânica.

A chave opcional é enviada em `X-API-Key`. No Render, configure `RULE_ENGINE_API_KEY` como secret e use o mesmo valor no serviço que fará as chamadas.
