<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=b9e9c3f0bf6d83928302d688a3f5c2bb304521db model=claude-sonnet-5-5 -->
> Esta página é uma tradução automática da [página em inglês](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), que prevalece.
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# Memória vinculada ao código para agentes de programação

O Loom vincula as memórias que agentes de programação salvam sobre um repositório às funções, classes
e arquivos que elas citam, acompanha esses símbolos ao longo de refatorações e sinaliza uma memória quando o código
dela muda. Por padrão, uma memória cujo código foi totalmente removido deixa de aparecer nos resultados de busca, e toda
outra leitura que ainda mostre uma lição informa se o código dela foi movido, alterado ou removido. O recurso
funciona via MCP nos repositórios que um Loom auto-hospedado indexa (o
[índice de código](code-intelligence.md), `LOOM_CODE_INGEST_ENABLED=true`), e o worker
`memory_code_links`, que mantém os vínculos, vem ativado por padrão.

<a id="what-you-get"></a>

## O que você obtém

- **Lições anexadas ao código a que se referem.** Uma memória marcada com `repo:<id>` (os
  plugins do Claude Code e do Codex marcam o que seus hooks capturam) ou escrita no namespace de código do
  repositório é vinculada a cada definição que ela cita entre crases, como
  `A::b` ou como uma chamada (`a.b()`), que resolva para exatamente um lugar; às definições nos
  arquivos que ela lista e que cita por uma palavra simples em camelCase ou snake_case; e a cada arquivo que
  ela cita e que não contém nenhuma delas. Um nome qualificado que ainda não está no trunk é vinculado se for integrado
  dentro de `memory_code_links_pending_days` (14).
  [Memórias vinculadas ao código](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Vínculos que acompanham refatorações.** A cada commit indexado, cada vínculo fica como `fresh`,
  `moved`, `changed` (indicando o commit que o alterou), `unresolved` ou `missing`. Um
  vínculo registra os tokens da definição, então espaços em branco, comentários e a maioria das mudanças de formatadores
  o mantêm como `fresh`. [Estados do vínculo](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Memórias desatualizadas tratadas como você escolher.** O
  [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) do namespace decide:
  `retire_removed` (o padrão) tira da busca uma memória cujo código foi totalmente removido
  e sinaliza o código alterado; `flag` apenas sinaliza; `revise` também tira da busca as memórias de código removido
  e faz o modelo verificar a afirmação de uma memória alterada contra o código antigo e o novo e sugerir uma
  afirmação corrigida. Em qualquer política, a busca classifica uma memória cujo código mudou abaixo dos
  três resultados seguintes, em vez de escondê-la. O Loom nunca reescreve a memória em si.
- **Lições onde quer que o agente leia código.** `code.symbol` retorna as memórias vinculadas a
  uma definição como `lessons`; `code.search`, `code.callers`, `code.callees`, `code.impact`,
  `code.outline` e `code.context_pack` anexam as lições dos arquivos que retornam;
  cada lição traz seu estado de vínculo. Os hooks do Claude Code e do Codex mostram as memórias de um arquivo
  (`memory.for_file`) antes de um agente editá-lo, e os hooks do Cline fazem isso quando ele lê
  ou edita um. [`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Plugin do Claude Code](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **Compartilhadas com a equipe.** As lições são lidas do namespace de código do repositório e dos
  namespaces que a chamada indica (via MCP, o do próprio chamador por padrão), então uma lição salva no
  namespace de código do repositório chega a todo agente que lê esse código.
  [Memórias vinculadas ao código](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Branches leem o próprio código.** Em outra branch, uma lição é verificada contra a definição
  dessa branch, então uma branch de release que ainda tem o texto antigo fica como `fresh`.
  [Memórias vinculadas ao código](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Documentos ao lado do código.** Os documentos de uma fonte registrada no namespace de código do repositório
  (o do trunk) levam a tag do repositório, então as buscas sobre o repositório
  os leem junto com o código (o worker de sincronização precisa de `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`).
  [Fontes de documentos](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **Uma lista de revisão.**
  [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) lista as
  memórias cujo código mudou ou foi removido, e o painel mostra os vínculos de cada memória e
  seu estado.

![Detalhe de uma memória no painel: seu código vinculado, uma definição atual e outra alterada desde um commit](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## Primeiros passos

O [Início rápido](../getting-started/quickstart.md) inicializa o Loom, e
[adicionar repositórios](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md) ativa o índice de código para um
repositório. Conecte seu agente de programação com o plugin dele ou via MCP
([conectar um agente de programação](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)). Salve uma lição que
cite entre crases uma definição de quatro ou mais caracteres, ou uma qualificada como
`` `Reader.run` ``. Os plugins do Claude Code e do Codex adicionam a tag `repo:<id>` para você
([como o id é derivado](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)); em qualquer
outro cliente, escreva a lição no namespace de código do repositório. Cada passada de vinculação vincula
escritas com pelo menos 60 segundos de idade, e as passadas rodam a cada 60 segundos, então, assim que o trunk for indexado,
`code.symbol` nessa definição retorna a lição em `lessons` em cerca de dois
minutos.
