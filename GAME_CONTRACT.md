# Contrato do turno de jogo

## Responsabilidades

1. **Cliente do jogo** recebe a intenção do jogador.
2. **Motor de regras** consulta e valida regras, calcula a resolução e produz `FATOS_RESOLVIDOS`.
3. **Gemini** recebe o prompt do narrador, o histórico, a fala do jogador e os `FATOS_RESOLVIDOS`; apenas narra.

O narrador nunca deve receber a responsabilidade de calcular uma ação.

O endpoint `/v1/dice/roll` gera faces de dados e totais, mas não resolve uma ação. O cliente não deve transformar uma rolagem bruta em sucesso, fracasso, dano ou condição sem uma mecânica validada.

> **Nota de compatibilidade:** os exemplos desta seção foram preservados para referência histórica. Eles não são schemas mecânicos do Marco 5. O único contrato mecânico normativo é `rule-resolution-v1`, conforme implementado na branch do PR #3 e descrito na seção Marco 5 abaixo.

## Payload histórico recomendado para o Gemini

```json
{
  "model": "gemini-3.5-flash-lite",
  "user": "campaign_123",
  "messages": [
    {"role": "system", "content": "<prompt do narrador>"},
    {"role": "user", "content": "<estado resumido da cena>\n\n<FATOS_RESOLVIDOS>{}</FATOS_RESOLVIDOS>\n\n<fala_do_jogador>Eu avanço até a porta.</fala_do_jogador>"}
  ],
  "stream": false
}
```

Quando a ação já foi resolvida:

```xml
<FATOS_RESOLVIDOS>
{
  "resolution_id": "res_001",
  "status": "resolved",
  "action": "attack_roll",
  "outcome": "hit",
  "damage": {"total": 8, "type": "slashing"},
  "conditions_applied": []
}
</FATOS_RESOLVIDOS>
```

Quando ainda não foi resolvida:

```xml
<FATOS_RESOLVIDOS>{}</FATOS_RESOLVIDOS>
```

## Regra de falha segura

O endpoint `/v1/resolve` resolve somente o `ability_check.mvp.v1` quando a ação já chega explicitamente estruturada; a resposta segue `rule-resolution-v1`. Texto livre permanece `needs_rule_validation` e deve ser convertido para `FATOS_RESOLVIDOS: {}`; payloads estruturados fora do schema aceito continuam rejeitados pela validação. Nenhum desses caminhos vira sucesso/fracasso. Candidatos da base de conhecimento não são promovidos automaticamente a regras executáveis.

O endpoint `/v1/dice/roll` é limitado a rolagens independentes. A busca de regras usa somente fontes classificadas como 2024/2025 e, entre fontes duplicadas, prefere a candidata canônica.

## Fontes permitidas

A busca usa somente fontes identificadas como 2024 ou 2025: o *Player’s Handbook 2024 / 5.5*, a fonte canônica do *Dungeon Master’s Guide 2024 / 5.5* e o *Monster Manual 2025*. Suplementos sem edição explícita e a extração alternativa do DMG permanecem no arquivo para auditoria, mas não são retornados pela busca. A edição de 2014 foi excluída.

## Marco 5 — turno orquestrado

O endpoint `POST /v1/game/turn` recebe `state`, `player_input` e uma `action` opcional. Texto livre nunca é convertido automaticamente em mecânica. Somente uma ação estruturada validada pelo Rule Engine pode produzir uma resolução.

O resultado mecânico é sempre o objeto oficial `rule-resolution-v1`, exposto no campo `rule_resolution`. Não são usados `facts_resolved`, `facts_resolvidos` ou `rule_id` legado.

Quando não há ação estruturada válida, a resposta usa o envelope:

```json
{
  "schema_version": "rule-resolution-v1",
  "status": "needs_rule_validation",
  "reason": "No deterministic resolution was emitted because the requested rule has not been bound to a validated mechanic."
}
```

Esse envelope não é enviado como fato mecânico ao narrador: o transporte `<FATOS_RESOLVIDOS>` recebe `{}`. Quando a resolução tem `status: "resolved"`, o mesmo objeto `rule-resolution-v1` é transportado integralmente.

O backend é stateless: devolve o estado recebido e não persiste campanha, saves ou histórico.

## C1 — comandos de ataque e limite de autoridade

No `POST /v1/game/turn`, um comando público de ataque contém `type: "attack"`, `actor_id`, `target_id` e `weapon_id` opcional. Os campos de apresentação canônicos `label`, `description` e `player_input` também podem acompanhar a sugestão do Mestre; o Backend os remove antes da resolução mecânica. `attack_bonus`, `target_ac` e `damage` são valores mecânicos derivados e não podem ser fornecidos pelo cliente. O Rule Engine valida a arma equipada e resolve o ataque a partir do personagem e do alvo disponíveis no estado recebido; o contrato de saída continua sendo `rule-resolution-v1`.

**Limite pendente de C2:** o Backend ainda é stateless e recebe `state` do cliente. A validação do comando remove a injeção direta de bônus/dano, mas não torna atributos, equipamento, HP, combate ou revisão do estado confiáveis. Até C2 estabelecer snapshot canônico no servidor, vínculo de sessão/campanha e controle de revisão/idempotência, este fluxo não deve ser tratado como estado autoritativo de campanha compartilhada.
