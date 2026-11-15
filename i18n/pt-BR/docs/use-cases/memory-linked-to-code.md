<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=ba0db8ea1cf39d2ef1aa0c782706183107d553a7 model=claude-sonnet-5-5 -->
> Esta página é uma tradução automática da [página em inglês](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), que prevalece.
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# Memória vinculada ao código para agentes de programação

O Loom vincula as memórias que os agentes de programação salvam sobre um repositório às funções, classes
e arquivos que elas citam, acompanha esses símbolos ao longo de refatorações e sinaliza uma memória quando o código
dela muda. Por padrão, uma memória cujo código desapareceu por completo sai da busca, de modo que lições
obsoletas deixam de chegar aos agentes. O recurso funciona nos repositórios que o Loom indexa (o
[índice de código](code-intelligence.md), `LOOM_CODE_INGEST_ENABLED=true`), e o worker
`memory_code_links`, que mantém os vínculos, vem ativado por padrão.

<a id="what-you-get"></a>

## O que você recebe

- **Lições anexadas ao código de que tratam.** Uma memória marcada com `repo:<id>` (os plugins do
  Claude Code e do Codex marcam o que seus hooks capturam) ou escrita no namespace de código de um repositório é vinculada a cada definição que ela cita entre crases e que resolve para
  exatamente um lugar, a definições nos arquivos que ela lista e que cita por uma palavra simples em
  camelCase ou snake_case, e a cada arquivo que ela cita e que não contém nenhuma delas. Um
  nome qualificado que ainda não está no trunk é vinculado se for incorporado dentro de
  `memory_code_links_pending_days` (14).
  [Memórias vinculadas ao código](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Vínculos que acompanham refatorações.** A cada commit indexado, cada vínculo assume o estado `fresh`,
  `moved`, `changed` (indicando o commit que o alterou), `unresolved` ou `missing`. Um
  vínculo registra os tokens da definição, então espaços em branco, comentários e a maioria das mudanças de formatador
  o mantêm como `fresh`.
- **Memórias obsoletas tratadas como você escolher.** A
  [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md) do namespace decide: `retire_removed`
  (o padrão) tira da busca uma memória cujo código desapareceu por completo e sinaliza código
  alterado; `flag` apenas sinaliza; `revise` também tira da busca as memórias de código removido e faz o modelo
  conferir a afirmação de uma memória alterada contra o código antigo e o novo e sugerir uma afirmação corrigida.
  Em qualquer política, a busca classifica uma memória cujo código mudou abaixo dos três
  resultados seguintes, em vez de escondê-la. O Loom nunca reescreve a memória em si.
- **Lições onde quer que o agente leia código.** `code.symbol` retorna as memórias vinculadas a
  uma definição como `lessons`; `code.search`, `code.callers`, `code.callees`, `code.impact`,
  `code.outline` e `code.context_pack` anexam as lições dos arquivos que retornam;
  cada lição traz o estado do seu vínculo. Os hooks do Claude Code e do Codex mostram as memórias de um arquivo
  (`memory.for_file`) antes de um agente editá-lo, e os hooks do Cline fazem isso quando ele lê ou edita
  um arquivo.
  [`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Plugin do Claude Code](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **Compartilhado com a equipe.** As lições são lidas do namespace da equipe do repositório e, por
  padrão, do namespace do próprio chamador, então uma lição que um agente salva ali chega a todos os agentes que leem esse
  código.
- **Branches leem o próprio código.** Em outro branch, uma lição é verificada contra a definição
  desse branch, de modo que um branch de release que ainda tem o texto antigo aparece como `fresh`.
- **Documentos ao lado do código.** Os documentos de uma fonte registrada no namespace de código do trunk de um repositório
  recebem a tag do repositório, então as buscas sobre o repositório os leem
  junto com o código (o worker de sincronização precisa de `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`).
  [Fontes de documentos](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **Uma lista de revisão.**
  [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) lista as
  memórias cujo código mudou ou desapareceu, e o painel mostra os vínculos de cada memória e
  o estado deles.

![Detalhe de uma memória no painel: seu código vinculado, uma definição atual e outra alterada desde um commit](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## Primeiros passos

O [Guia de início rápido](../getting-started/quickstart.md) inicia o Loom, e
[adicionar repositórios](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md) ativa o índice de código para um
repositório. Conecte seu agente de programação com o plugin dele ou por MCP
([conectar um agente de programação](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)). Salve uma lição que
cite entre crases uma definição de quatro ou mais caracteres (ou uma qualificada, como
`` `Reader.run` ``), marcada com `repo:<id>` ou escrita no namespace de código do repositório. Quando
o trunk estiver indexado e uma passagem de vínculos tiver rodado um minuto após o salvamento (as passagens rodam a cada 60
segundos e ficam 60 segundos atrás da gravação mais recente, ou seja, em cerca de dois minutos),
`code.symbol` nessa definição retorna a lição em `lessons`.
