<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=9cbe1bbc3b8361789f5d82dc35b67597e77e8dbf model=claude-sonnet-5-5 -->
> Esta página é uma tradução automática da [página em inglês](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), que prevalece.
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# Memória vinculada ao código para agentes de programação

O Loom vincula cada memória que um agente de programação salva às funções, classes e arquivos que ela cita, acompanha esses símbolos ao longo de refatorações e sinaliza a memória quando esse código muda.
Por padrão, uma memória cujo código foi todo removido deixa de aparecer na busca, de modo que lições obsoletas param de chegar aos agentes. O recurso funciona sobre os repositórios que o Loom indexa (o [índice de código](code-intelligence.md),
`LOOM_CODE_INGEST_ENABLED=true`), e o worker `memory_code_links`, que mantém os vínculos, vem ativado por padrão.

<a id="what-you-get"></a>

## O que você obtém

- **Lições anexadas ao código a que se referem.** Uma memória marcada com um repositório (os plugins do Claude Code e do Codex marcam suas sessões) ou escrita no namespace de código de um repositório é vinculada a cada definição citada entre crases que resolva para exatamente um lugar (e, dentro dos arquivos que ela lista, às definições citadas como palavra simples), e a cada arquivo citado que não contenha nenhuma delas. Um nome qualificado que ainda não está no trunk é vinculado se for mesclado dentro de `memory_code_links_pending_days` (14).
  [Memórias vinculadas ao código](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Vínculos que acompanham refatorações.** A cada commit indexado, cada vínculo assume o estado `fresh`, `moved`, `changed` (indicando o commit que o alterou), `unresolved` ou `missing`. Um vínculo registra os tokens da definição, de modo que espaços em branco, comentários e a maioria das mudanças de formatador o mantêm como `fresh`.
- **Memórias obsoletas tratadas como você escolher.** O `code_change_policy` do namespace remove da busca uma memória cujo código foi todo removido (`retire_removed`, o padrão), apenas a sinaliza (`flag`), ou faz o modelo conferir a afirmação contra o código antigo e o novo e sugerir uma versão corrigida (`revise`). O Loom nunca reescreve a memória por conta própria.
- **As lições aparecem onde o agente lê código.** `code.symbol` retorna as memórias vinculadas a uma definição como `lessons`, cada uma com o estado do seu vínculo; `memory.for_file` fornece as memórias vinculadas ao código de um arquivo; o plugin do Claude Code as mostra antes de uma edição nesse arquivo. [`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Plugin do Claude Code](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **Branches leem o próprio código.** Em um branch de release que ainda tem o texto antigo, uma lição aparece como `fresh` depois que o trunk o alterou.
- **Documentos ao lado do código.** Documentos de uma fonte registrada no namespace de código do trunk de um repositório recebem a tag do repositório, de modo que as buscas sobre o repositório os leem junto com o código (o worker de sincronização precisa de `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`).
  [Fontes de documentos](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **Uma lista de revisão.** `namespace.validate` lista as memórias cujo código mudou ou desapareceu, e o painel mostra os vínculos de cada memória e seu estado.

<a id="start"></a>

## Primeiros passos

O [Guia rápido](../getting-started/quickstart.md) inicia o Loom, e
[adicionar repositórios](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md) ativa o índice de código para um
repositório. Conecte seu agente de programação com o plugin dele ou via MCP
([conectar um agente de programação](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)) e salve uma lição
que cite uma definição entre crases. Quando o trunk estiver indexado e a próxima passagem de vinculação tiver sido executada
(a cada 60 segundos por padrão), `code.symbol` nessa definição retorna a lição em `lessons`.
