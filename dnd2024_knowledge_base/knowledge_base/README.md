# D&D 2024 — base de conhecimento estruturada

Gerada em 2026-10-03 a partir das fontes permitidas do corpus fornecido.

## Entregáveis

- `INVENTORY.json`: inventário, hashes, edição, formato, avisos e duplicatas.
- `dnd_rules.db`: SQLite com FTS5 (`chunks_fts`) para consultas rápidas.
- `knowledge_base.json`: envelope com INVENTORY/RULES/ENTITIES/MECHANICS/CONDITIONS/REFERENCES/RULE_CONFLICTS/TEST_CASES/GLOSSARY/DATA_SCHEMA.
- `TEST_CASES.json`: casos de teste estruturais pendentes de amarração a regras validadas.
- `DATA_SCHEMA.json`: contrato dos dados.
- `AUDIT.json`: auditoria de rastreabilidade e limitações.
- `doc_*.txt`: textos normalizados, preservando o conteúdo recuperado.

## Decisões de desempenho e qualidade

1. Os quatro MIME/HTML foram convertidos pelo contêiner semântico `<pre>`, evitando indexar CSS, navegação e metadados da página.
2. O texto integral foi particionado em chunks de 80 linhas e indexado com SQLite FTS5; isso evita reler 1,25 milhão de palavras para cada consulta.
3. A fonte original é preservada por SHA-256; nenhum arquivo foi sobrescrito.
4. O DMG e PHB TXT foram marcados como candidatos canônicos; as cópias HTML/OCR permanecem para comparação e rastreabilidade.
5. Frases normativas são **candidatos**, não regras validadas: o pipeline não inventa página, valor ou interpretação.

## Consulta rápida

```sql
SELECT c.chunk_id, c.title, c.section, c.page, snippet(chunks_fts, 3, '[', ']', ' … ', 24)
FROM chunks_fts JOIN chunks c ON c.rowid = chunks_fts.rowid
WHERE chunks_fts MATCH 'concentration AND advantage'
LIMIT 20;
```

## Próxima etapa recomendada

Validar candidatos contra o documento canônico, preencher `category`, transformar candidatos em regras executáveis e criar casos de teste somente depois da validação.


## Escopo de edição

A entrega contém somente as fontes permitidas do corpus; versões fora do escopo não são indexadas.
