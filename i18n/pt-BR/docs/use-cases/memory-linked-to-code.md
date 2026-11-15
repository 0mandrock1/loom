<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=e4bf1235c8a582b596af733bb2e5896ecc401751 model=claude-sonnet-5-5 -->
> Esta página é uma tradução automática da [página em inglês](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), que prevalece.
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# Memória vinculada ao código para agentes de programação

O Loom vincula as memórias dos agentes de programação às funções, classes e arquivos que elas citam, acompanha esses elementos ao longo de refatorações e sinaliza uma memória quando o código dela muda. Por padrão, a busca deixa de fora uma memória cujo código foi totalmente removido, e toda leitura que devolve uma lição informa quando o código dela mudou ou deixou de existir. Isso funciona por MCP, HTTP e pelos SDKs nos repositórios que um Loom auto-hospedado indexa
(o [índice de código](code-intelligence.md), `LOOM_CODE_INGEST_ENABLED=true`), e o worker `memory_code_links`, que mantém os vínculos, vem ativado por padrão.

<a id="what-you-get"></a>

## O que você recebe

- **Lições anexadas ao código a que se referem.** Uma memória marcada com `repo:<id>`
  (os plugins do Claude Code e do Codex marcam o que seus hooks capturam) ou escrita no namespace de código do repositório é vinculada a cada definição
  (função, classe ou outro símbolo) que ela cita entre crases, ou em qualquer ponto do texto como `A::b` ou como uma chamada (`a.b()`, `name()`), desde que resolva para exatamente um lugar; às definições dos arquivos que ela lista e que ela cita por uma palavra simples em camelCase ou snake_case; e a cada arquivo que ela lista e que não contém nenhuma delas. Um nome qualificado que ainda não está no trunk é vinculado se for integrado dentro de `LOOM_MEMORY_CODE_LINKS_PENDING_DAYS` (14 dias).
  [Memórias vinculadas ao código](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Vínculos que acompanham refatorações.** A cada commit indexado, cada vínculo passa a ser `fresh`, `moved`, `changed` (indicando o commit que o alterou), `unresolved` ou `missing`. Um vínculo registra os tokens da definição, de modo que espaços em branco, comentários e a maioria das mudanças de formatadores o mantêm como `fresh`. [Estados do vínculo](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Memórias desatualizadas tratadas como você escolher.** A
  [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) do namespace decide:
  `retire_removed` (o padrão) deixa fora da busca uma memória cujo código foi totalmente removido e sinaliza código alterado; `flag` apenas sinaliza; `revise` também deixa de fora as memórias cujo código sumiu e faz o modelo conferir a afirmação de uma memória alterada contra o código antigo e o novo e sugerir uma afirmação corrigida. Em qualquer política, a busca posiciona uma memória cujo código mudou abaixo dos três resultados seguintes, em vez de ocultá-la, a menos que uma pessoa a mantenha fixa.
  [Políticas](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code) O Loom nunca reescreve a própria memória.
- **Lições onde quer que o agente leia código.** `code.symbol` devolve as memórias vinculadas a uma definição como `lessons`; `code.search`, `code.callers`, `code.callees`, `code.impact`, `code.outline` e o `code.context_pack` de uma tarefa anexam as lições dos arquivos que devolvem; cada lição traz o estado do seu vínculo. Os hooks do Claude Code e do Codex mostram as memórias de um arquivo (`memory.for_file`) antes de um agente editá-lo, e os hooks do Cline fazem isso quando ele lê ou edita um arquivo. [`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [plugin do Claude Code](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md) ·
  [plugin do Codex](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/codex.md)
- **Compartilhado com a equipe.** As lições são lidas do namespace de código do repositório e dos namespaces que a chamada nomeia (por MCP, o do próprio chamador por padrão), de modo que uma lição salva no namespace de código do repositório chega a todo agente que pode ler esse namespace.
  [Memórias vinculadas ao código](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Branches leem o próprio código.** Em outro branch, uma lição é conferida contra a definição desse branch, de modo que um branch de release que ainda tem o texto antigo aparece como `fresh`.
  [Memórias vinculadas ao código](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Documentos ao lado do código.** Os documentos de uma fonte registrada no namespace de código do repositório (o do trunk) levam a tag do repositório, de modo que as buscas sobre o repositório os leem junto com o código (o worker de sincronização precisa de `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`).
  [Fontes de documentos](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **Uma lista de revisão.**
  [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) lista as memórias cujo código mudou, não foi encontrado novamente ou deixou de existir, e o painel mostra os vínculos de cada memória e o estado deles.

![Detalhe de uma memória no painel: o código vinculado, com uma definição atual e outra alterada desde um commit](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## Começar

O [Guia rápido](../getting-started/quickstart.md) inicia o Loom, e
[adicionar repositórios](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md) ativa o índice de código para um repositório. Conecte seu agente de programação com o plugin dele ou por MCP
([conectar um agente de programação](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)). Salve uma lição que cite entre crases uma definição de quatro ou mais caracteres, ou uma definição qualificada como `` `Reader.run` ``. Os plugins do Claude Code e do Codex marcam o que seus hooks capturam com `repo:<id>`, e o primer da sessão informa o id
([como ele é derivado](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)). Uma lição que o agente salva por conta própria com `loom_memory_set` é vinculada quando carrega essa tag
(`tags: ["repo:<id>"]`) ou é escrita no namespace de código do repositório; qualquer cliente pode fazer uma das duas coisas. Por HTTP (ou `agent.call` no SDK Python), uma lição salva no seu próprio namespace só é lida quando a chamada nomeia esse namespace em `lesson_namespaces`. Cada passada de vinculação vincula gravações com pelo menos 60 segundos de idade e as passadas rodam a cada 60 segundos, então, depois que o trunk é indexado, `code.symbol` nessa definição devolve a lição em `lessons` em cerca de dois minutos.

`code.callees` e `code.outline` são experimentais ([situação](https://github.com/ClickHouse/loom/blob/main/docs/reference/status.md)); o restante desta página é estável.
