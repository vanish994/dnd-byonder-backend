# Capability Map: D&D Byonder — Versão Jogável 2024

## Escopo

Expandir o fluxo atual de Dragon Delves para uma fatia jogável de exploração, social, investigação e combate, usando exclusivamente o Livro do Jogador 2024 e fontes 2024 autorizadas do projeto.

## Restrições permanentes

- Toda regra mecânica deve ser confirmada no material 2024 disponível.
- O Rule Engine é a autoridade de resolução; o narrador Gemini interpreta intenção e produz narrativa.
- O PostgreSQL mantém o estado canônico da sessão.
- O Frontend envia intenção e apresenta estado; nunca determina CD, rolagem, modificador, resultado ou transição.
- Toda interface nova ou alterada será mobile-first, sem overflow horizontal e com alvos de toque de pelo menos 44 px.
- Referências não confirmadas no material 2024 não entram em código, dados, testes ou documentação.

## Módulos

| ID | Responsabilidade | Depende de |
|---|---|---|
| `session-foundation` | Criação, retomada, revisão, idempotência e estado canônico da sessão | — |
| `character-2024` | Criação e exibição de personagem conforme escolhas confirmadas do PHB 2024 | `session-foundation` |
| `exploration-redwood` | Cenas, ações server-owned, investigação e descobertas de Redwood Grove | `session-foundation`, `character-2024` |
| `social-kaynen` | Respeito, Persuasão, atitude e desbloqueios da sequência de Kaynen | `exploration-redwood`, `character-2024` |
| `combat-2024` | Encontro jogável, iniciativa, ataques, dano, condições e encerramento somente com regras confirmadas | `session-foundation`, `character-2024` |
| `frontend-playable` | Fluxo mobile-first para criação, cena, ações, rolagens, combate e retomada, com a narração como área visual principal | `session-foundation`, `exploration-redwood`, `social-kaynen`, `combat-2024` |
| `narrator-gemini` | Interpretação de intenção e narrativa limitada ao snapshot e aos fatos autorizados | `session-foundation`, `exploration-redwood`, `social-kaynen`, `combat-2024` |
| `quality-performance` | Testes E2E, auditoria de edição 2024, validação mobile e medições de desempenho | todos os módulos funcionais |

## Ordem de construção

1. `session-foundation`
2. `character-2024`
3. `exploration-redwood`
4. `social-kaynen`
5. `combat-2024`
6. `narrator-gemini`
7. `frontend-playable`
8. `quality-performance`

## Diretriz de layout da interface

- Priorizar a área de narração no primeiro viewport e durante a leitura da cena.
- Reduzir cabeçalhos, painéis auxiliares, espaçamentos e elementos repetitivos que ocupem altura sem ajudar a decisão do jogador.
- Manter ações disponíveis próximas da narração, mas visualmente secundárias até serem necessárias.
- Em smartphones, evitar que status, inventário e controles empurrem a narrativa para fora da tela; usar seções compactas e expansíveis.
- Validar a proporção de espaço e a legibilidade em viewport móvel representativa antes de adaptar para tablet e desktop.
- Não sacrificar alvos de toque de pelo menos 44 px nem criar overflow horizontal.

## Gate de aprovação

Este mapa precisa ser aprovado antes da criação das especificações individuais dos módulos e antes de qualquer implementação da próxima versão jogável.

## Questões abertas

1. A primeira versão jogável deve cobrir apenas o encontro inicial de Dragon Delves ou também a progressão após Redwood Grove?
2. O combate inicial deve ser iniciado por uma ação explícita do jogador após uma investigação bem-sucedida, mantendo a escolha fora do combate automático?
3. A versão jogável precisa de autenticação de usuário ou a sessão anônima persistente atual permanece como escopo?
4. O alvo de desempenho deve ser medido no Render atual ou apenas localmente nesta etapa?
