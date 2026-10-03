# Contrato do turno de jogo

## Responsabilidades

1. **Cliente do jogo** recebe a intenção do jogador.
2. **Motor de regras** consulta e valida regras, calcula a resolução e produz `FATOS_RESOLVIDOS`.
3. **Proxy Mimo** recebe o prompt do narrador, o histórico, a fala do jogador e os `FATOS_RESOLVIDOS`; apenas narra.

O narrador nunca deve receber a responsabilidade de calcular uma ação.

## Payload recomendado para o Mimo

```json
{
  "model": "mimo-v2.5-no-thinking",
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

O endpoint atual `/v1/resolve` retorna `needs_rule_validation` porque a base possui candidatos de regra, não um conjunto integral de mecânicas validadas. Esse estado deve ser convertido para `FATOS_RESOLVIDOS: {}` e nunca para sucesso/fracasso.

## Fontes permitidas

Esta instalação contém o *Player’s Handbook 2024 / 5.5*, o *Dungeon Master’s Guide 2024 / 5.5*, o *Monster Manual 2025* e os suplementos presentes no inventário. A edição de 2014 foi excluída.
