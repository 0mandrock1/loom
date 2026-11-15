<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=020733a9f887152df7c768465027827db57128ba model=claude-sonnet-5-5 -->
> 本页由机器从[英文原文](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)翻译，内容以英文原文为准。
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# 与代码关联的记忆，面向编码 agent

Loom 把编码 agent 的记忆关联到它们提到的函数、类和文件，在重构后继续跟踪这些关联，并在代码变更时标记对应的记忆。默认情况下，搜索会排除其代码已全部消失的记忆，每一次返回经验教训的读取都会说明其代码是已移动、已变更还是已消失。该功能通过 MCP、HTTP 和 SDK 运行，作用于自托管 Loom 所索引的仓库（即[代码索引](code-intelligence.md)，`LOOM_CODE_INGEST_ENABLED=true`），而负责维护这些关联的 `memory_code_links` worker 默认开启。

<a id="what-you-get"></a>

## 你能获得什么

- **经验教训附着在它所涉及的代码上。** 带有 `repo:<id>` 标签的记忆（Claude Code 和 Codex 插件会给其 hook 捕获的内容打上该标签），或写入仓库代码命名空间的记忆，会关联到：它用反引号提到的每个定义，或在文本任意位置以 `A::b` 或调用形式（`a.b()`、`name()`）提到、且恰好解析到一处的定义；它列出的文件中、以纯 camelCase 或 snake_case 单词提到的定义；以及它列出的、不包含上述任何定义的每个文件。尚未进入主干的限定名，如果在 `LOOM_MEMORY_CODE_LINKS_PENDING_DAYS`（14）天内合并，也会建立关联。[与代码关联的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **关联随重构而更新。** 每次索引提交时，每个关联的状态为 `fresh`、`moved`、`changed`（并指明导致变更的提交）、`unresolved` 或 `missing`。关联记录的是定义的 token，因此空白、注释和大多数格式化工具的改动都会让它保持 `fresh`。[关联状态](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **按你的选择处理过时的记忆。** 由命名空间的 [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) 决定：`retire_removed`（默认）会让代码已全部消失的记忆不再出现在搜索中，并标记已变更的代码；`flag` 只做标记；`revise` 同样排除代码已消失的记忆，并让模型对照新旧代码检查已变更记忆的论断，给出修正后的论断建议。无论采用哪种策略，搜索都会把代码已变更的记忆排在后面三个结果之后，而不是将其隐藏。Loom 绝不会改写记忆本身。
- **agent 读代码的任何地方都有经验教训。** `code.symbol` 以 `lessons` 返回关联到某个定义的记忆；`code.search`、`code.callers`、`code.callees`、`code.impact`、`code.outline` 以及任务的 `code.context_pack` 会附上它们所返回文件的经验教训；每条经验教训都带有其关联状态。Claude Code 和 Codex 的 hook 会在 agent 编辑文件前显示该文件的记忆（`memory.for_file`），Cline 的 hook 则在读取或编辑文件时显示。[`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) · [Claude Code 插件](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **与团队共享。** 经验教训从仓库的代码命名空间以及调用中指明的命名空间读取（通过 MCP 时，默认为调用者自己的命名空间），因此保存在仓库代码命名空间中的经验教训会送达每个读取该代码的 agent。[与代码关联的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **分支读取各自的代码。** 在其他分支上，经验教训会对照该分支自己的定义进行检查，因此仍保留旧文本的发布分支会显示 `fresh`。[与代码关联的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **文档与代码并存。** 来自注册在仓库代码命名空间（即其主干）上的数据源的文档，会带上该仓库的标签，因此针对该仓库的搜索会把这些文档与其代码一并读取（同步 worker 需要 `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`）。[文档来源](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **审查列表。** [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) 会列出代码已变更、再次查找不到或已消失的记忆，控制台则显示每条记忆的关联及其状态。

![控制台中一条记忆的详情：其关联的代码，一个定义为当前状态，另一个自某次提交后已变更](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## 开始使用

[快速开始](../getting-started/quickstart.md)会启动 Loom，[添加仓库](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)则会为某个仓库开启代码索引。通过插件或 MCP 连接你的编码 agent（[连接编码 agent](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)）。保存一条经验教训，用反引号写出一个至少四个字符的定义名，或像 `` `Reader.run` `` 这样的限定名。Claude Code 和 Codex 插件会给其 hook 捕获的内容打上 `repo:<id>` 标签，会话引导信息会写明该 id（[如何得出](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)）。agent 自己用 `loom_memory_set` 保存的经验教训，在带有该标签（`tags: ["repo:<id>"]`）或写入仓库代码命名空间时会建立关联；任何客户端都可以采用其中一种做法。通过 HTTP 或 SDK 时，保存在你自己命名空间中的经验教训，只有在调用的 `lesson_namespaces` 中指明该命名空间时才会被读取。每轮关联处理只会关联至少已写入 60 秒的内容，且每 60 秒运行一轮，因此一旦主干完成索引，对该定义调用 `code.symbol` 大约在两分钟内就会在 `lessons` 下返回这条经验教训。

本页中的部分操作在[状态页](https://github.com/ClickHouse/loom/blob/main/docs/reference/status.md)上被标记为实验性。
