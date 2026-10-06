# D&D Byonder — Guia de continuidade PHB 2024

## Estado publicado nesta etapa

Este commit consolida o estado do Backend / Rule Engine na branch `feat/c2-dragon-delves-redwood-grove`.

A arquitetura vigente é:

```text
Frontend / Phaser
    -> intenção, escolhas e apresentação
Gateway
    -> contrato e autenticação de transporte
Backend / Rule Engine
    -> catálogo, validação, dados, combate, magia e estado mecânico
PostgreSQL
    -> snapshot canônico persistido
Gemini
    -> interpretação segura e narrativa; nunca autoridade mecânica
```

A regra de projeto é absoluta: somente D&D 2024 / Livro do Jogador 2024 e fontes 2024 autorizadas podem entrar em código, dados, testes ou documentação.

## O que está implementado

### Catálogo e personagem

- Catálogo canônico versionado com proveniência, status e `evidence_hash`.
- 12 classes, 48 subclasses, 10 espécies e 16 origens no manifest atual.
- Rejeição explícita de registros não canônicos.
- `UniversalCharacter` com seleção canônica, atributos, proficiências, recursos, magia, condições e efeitos ativos.
- Adaptação aditiva de snapshots antigos, sem inventar escolhas ausentes.
- Seleção de subclasse server-owned a partir do nível 3.

### Progressão

- Progressão das 12 classes implementada na faixa atual até o nível 6.
- Níveis 1–3 e 4–6 cobertos por testes.
- Slots de magia e recursos de classe sincronizados no level-up.
- Magias de subclasse sempre preparadas materializadas quando o vínculo estiver confirmado no catálogo do runtime.
- Criação direta continua restrita ao fluxo previsto; level-up é resolvido pelo Rule Engine.

### Subclasses

- Dispatcher de características server-owned em `rule_engine/subclass_mechanics.py`.
- Validação de classe, subclasse, nível e recurso antes de qualquer efeito.
- Características acionáveis já conectadas incluem efeitos de condições, dados, cura, teleporte/benefício e consumo de recursos onde o contrato foi implementado.
- Características sem infraestrutura completa não devem receber fallback narrativo nem efeito inventado.

### Combate e magia

- Núcleo em `rule_engine/combat_magic.py`.
- Concentração:
  - CD igual ao maior valor entre 10 e metade do dano, arredondada para baixo;
  - limite máximo de CD 30;
  - salvaguarda de Constituição server-owned;
  - encerramento da magia concentrada e limpeza de efeitos ativos em falha;
  - checagem automática após dano de ataque quando o alvo tem concentração ativa.
- Salvaguardas complexas:
  - vantagem/desvantagem;
  - dano rolado pelo servidor;
  - dano pela metade em sucesso;
  - condição e duração em falha.
- Janelas de reação:
  - abertura por gatilho;
  - estado `pending`/`consumed`;
  - alvo e ator validados;
  - consumo fora do turno do personagem, conforme a natureza das reações;
  - restauração da reação no início do próximo turno.
- Ataques contra personagens com característica de reação aplicável já podem abrir uma janela server-owned; a aplicação completa de cada reação deve ser implementada em sua própria fatia.

## Como validar antes de continuar

A partir de `/tmp/dnd-live/backend`:

```bash
PYTHONPATH=. pytest -q
python3 -m compileall -q game rule_engine services tests
 git diff --check
```

A suíte publicada nesta etapa deve permanecer sem falhas. Avisos de depreciação do Pydantic não são falhas funcionais, mas devem ser tratados em uma tarefa separada de modernização.

Auditoria obrigatória:

```bash
! rg -n -i '2014|legacy rules|legacy content' rule_engine game services tests
```

## Próxima ordem recomendada

### 1. Fechar o contrato de magia canônica

Criar registros canônicos para truques e magias confirmados no material 2024, cada um com:

- nível;
- escola;
- lista de classe;
- tempo de conjuração;
- alcance;
- componentes;
- duração;
- concentração;
- alvo/área;
- salvaguarda ou ataque mágico;
- dano/efeito mecânico;
- escalonamento;
- proveniência e hash.

Nenhuma magia deve ser resolvida somente por nome livre enviado pelo cliente ou pelo Gemini.

### 2. Criar o pipeline de conjuração

Implementar, nesta ordem:

1. validar que a magia é conhecida ou preparada;
2. validar nível e espaço disponível;
3. validar componentes e alcance quando modelados;
4. validar ação, ação bônus ou reação;
5. consumir o espaço somente após todas as validações;
6. rolar ataque ou salvaguarda no servidor;
7. aplicar dano, cura, condições e efeitos ativos;
8. iniciar concentração de forma atômica quando necessário;
9. devolver `rules_used`, rolagens, alvos e alterações de estado.

O pipeline deve impedir duas concentrações simultâneas e limpar a concentração ao aplicar Incapacitated ou outro término confirmado pela fonte.

### 3. Completar salvaguardas multi-alvo

Estender `complex_saving_throw` para uma ação explícita com:

- lista de alvos resolvida pelo servidor;
- modificador individual derivado de cada personagem/monstro;
- resistência/imunidade quando confirmadas e modeladas;
- dano separado por alvo;
- condição aplicada somente a alvos que falharam;
- rollback completo em caso de erro.

Não aceitar modificadores, resultados ou seleção de alvos mecânicos fornecidos pelo cliente.

### 4. Fechar reações específicas

Implementar uma reação por vez, com testes positivos e negativos:

- Opportunity Attack, depois de existir um contrato de alcance/ameaça;
- Misty Escape completo;
- Shield, quando o registro e o cálculo de CA estiverem canônicos;
- reações de subclasses que modificam dano ou rolagens;
- Ready e gatilhos, somente após existir um estado de ação preparada.

Cada reação deve declarar:

```text
trigger -> janela -> validação -> custo -> rolagem/efeito -> consumo -> projeção
```

### 5. Persistência e compatibilidade

- Persistir `spellcasting.concentration_spell_id`, `active_effects` e `reaction_windows` no snapshot canônico.
- Adicionar migração somente quando o contrato de persistência estiver estável.
- Testar retomada de sessão e idempotência.
- Não alterar silenciosamente snapshots antigos: migrar de forma explícita ou rejeitar com motivo.

### 6. Integração Frontend / Phaser

Depois que os contratos backend estiverem estáveis:

- exibir janela de reação como decisão urgente e tocável;
- mostrar concentração ativa e interrupção da magia;
- animar salvaguardas e dano sem determinar seus resultados;
- manter alvos de toque com pelo menos 44 px;
- validar primeiro em viewport móvel, sem overflow horizontal.

## Regras de segurança mecânica

- Gemini pode interpretar intenção e produzir narrativa contextual.
- Gemini não pode escolher CD, resultado de dado, dano, cura, condição, slot, recurso, alvo mecânico ou transição.
- O frontend pode solicitar uma ação, mas não pode fornecer os valores derivados.
- O Rule Engine deve falhar fechado para magia, subclasse, condição ou reação sem registro canônico e resolver determinístico.
- Dados, condições, efeitos e recursos devem ser alterados atomicamente e restaurados no rollback de `resolve_request`.

## Checklist de uma próxima implementação

- [ ] Buscar a regra no material 2024 local.
- [ ] Registrar a proveniência no catálogo.
- [ ] Definir contrato Pydantic estrito.
- [ ] Implementar resolver server-owned.
- [ ] Impedir valores mecânicos do cliente.
- [ ] Adicionar teste de sucesso.
- [ ] Adicionar teste de falha e rollback.
- [ ] Adicionar teste de opção desconhecida/não canônica.
- [ ] Validar retomada e persistência.
- [ ] Rodar suíte completa, compilação, diff check e auditoria de edição.
- [ ] Só depois integrar narrativa, React e Phaser.

## Limitações conhecidas

O backend não deve ser descrito como uma implementação completa de todas as magias e efeitos do PHB 2024 enquanto o catálogo de magias e o pipeline de conjuração não estiverem fechados. A infraestrutura atual é uma base incremental e fail-closed para continuar o desenvolvimento sem conceder autoridade mecânica ao narrador.
