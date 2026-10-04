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

## Orchestrator de turno

- `POST /v1/game/turn` — recebe `{ "campaign_id": "...", "state": {}, "player_input": "...", "action": null }`.

O campo `action` é opcional. Texto livre não é interpretado como regra. Quando uma ação estruturada é fornecida, ela é validada pelo mesmo `ResolveRequest` do Rule Engine e, se suportada, produz `rule-resolution-v1` no campo `rule_resolution`.

A integração com o provider de narração é separada do Rule Engine. O provider Groq recebe apenas contexto narrativo e fatos resolvidos; não rola, calcula ou adjudica mecânicas. O provider Groq permanece disponível para rollback explícito.

O Rule Engine também aceita ações estruturadas de `rest`, `define_resource`, `consume_resource`, `recover_resource`, `add_item`, `remove_item`, `equip_item` e `unequip_item`. Esses dados vivem no estado serializável do personagem (`resources`, `inventory` e `equipped`) e continuam sob a autoridade mecânica do resolver. Descanso não é permitido durante combate ativo; ataques de combate podem usar a arma equipada e a AC pode ser derivada da armadura equipada.

A progressão R5 usa a tabela oficial de XP do material D&D 2024 disponível no projeto, com níveis de 1 a 20 e bônus de proficiência derivado exclusivamente do nível total. As ações `add_experience` e `level_up` são resolvidas pelo engine; a primeira acumula XP e informa a disponibilidade de avanço, e a segunda aplica um nível por vez de forma atômica. O registry de classes mantém dados de Fighter, features por nível, proficiências, equipamento inicial e recursos dependentes de nível. Features ainda sem resolver mecânico próprio permanecem como metadados explícitos e não são simuladas pelo narrador.

O recurso `second_wind` do Fighter é uma exceção já resolvida mecanicamente: a ação `second_wind` consome uma unidade no combate, usa `1d10 + nível de Fighter`, limita a cura ao HP máximo e recupera **um uso em short rest** e **todos os usos em long rest**. A política é expressa genericamente por `recovery_amount`, não por uma exceção específica do Fighter.

Na resolução de ataques, modificadores de dano podem ser negativos, o dano final é limitado a zero, um acerto crítico rola os dados de dano duas vezes e o envelope preserva a expressão de dados real da arma. Proficiências de classe, definições de itens do catálogo e recursos de classe são validados contra registries server-owned; payloads não podem forjar essas definições mecânicas.

### Configuração

```text
GROQ_API_KEY=<secret do Google Groq>
GROQ_MODEL=llama-3.3-70b-versatile
GROQ_TIMEOUT_SECONDS=30
GROQ_MAX_OUTPUT_TOKENS=512
GROQ_TEMPERATURE=0.7
```


O endpoint retorna `401` para chave do backend inválida, `422` para ação estruturada inválida, `503` quando o provider selecionado não está configurado e `502` em falha do Rule Engine/narrador. Em falha do narrador, o corpo do erro preserva `rule_resolution` sem fabricar uma narrativa.
