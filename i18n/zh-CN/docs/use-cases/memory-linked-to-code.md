<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=398a66da6f0537777da91f8146bf14723fcb2c87 model=claude-sonnet-5-5 -->
> 本页由机器从[英文原文](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)翻译，内容以英文原文为准。
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# 与代码关联的记忆，面向编码 agent

Loom 把编码 agent 的记忆关联到记忆中提到的函数、类和文件，在重构后继续跟踪它们，并在对应代码发生变化时标记该记忆。默认情况下，除非有人固定、锁定或验证过，否则搜索会排除其代码已全部消失的记忆；每次返回经验教训的读取，都会说明其代码何时发生变化或已消失。它通过 MCP、HTTP 和 SDK 运行，作用于自托管 Loom 所索引的仓库（即[代码索引](code-intelligence.md)，`LOOM_CODE_INGEST_ENABLED=true`），而维护这些关联的 `memory_code_links` worker 默认开启。

<a id="what-you-get"></a>

## 你将获得什么

- **经验教训附着在它所涉及的代码上。** 带有 `repo:<id>` 标签的记忆（Claude Code 和 Codex 插件会给其 hook 捕获的内容打上该标签），或写入仓库代码命名空间的记忆，会被关联到：它用反引号提到的每个定义（函数、类或其他符号），或在文本任意位置以 `A::b` 或调用形式（`a.b()`、`name()`）提到、且恰好解析到唯一位置的定义（不带限定的名称需要四个或更多字符）；它列出的文件中、以单独的 camelCase 或 snake_case 词提到的定义；以及它列出的、不包含上述任何定义的每个文件。尚未进入主干的限定名称，如果在 `LOOM_MEMORY_CODE_LINKS_PENDING_DAYS`（14 天）内合并，也会被关联。
  [与代码关联的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **关联随重构而更新。** 在每次被索引的提交上，每条关联的状态为 `fresh`、`moved`、`changed`（并指明改动它的提交）、`unresolved` 或 `missing`。关联会记录定义的 token，因此空白、注释和大多数格式化工具的改动都会让它保持 `fresh`。[关联状态](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **按你的选择处理过时的记忆。** 由命名空间的 [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) 决定：`retire_removed`（默认）会让其代码已全部消失的记忆不再出现在搜索中，并标记代码已变化的记忆；`flag` 只做标记；`revise` 同样会排除代码已消失的记忆，并让模型对照旧代码和新代码检查已变化记忆的论断：仍然成立的会重新锚定，被推翻的会得到一条建议的更正。被人固定、锁定或验证过的记忆永远不会被排除。无论采用哪种策略，搜索通常会把代码已变化的记忆排在后续三条结果之后，而不是将其隐藏；被保留的记忆和按名称找到的行则保持原位。Loom 从不改写记忆本身。
  [策略](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **agent 读代码的任何地方都能看到经验教训。** `code.symbol` 以 `lessons` 返回与某个定义关联的记忆；`code.search`、`code.callers`、`code.callees`、`code.impact`、`code.outline` 以及任务的 `code.context_pack` 会为它们返回的文件附上经验教训；每条经验教训都带有其关联状态。Claude Code 和 Codex 的 hook 会在 agent 编辑文件之前显示该文件的记忆（`memory.for_file`），Cline 的 hook 则在读取或编辑文件时显示。[`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Claude Code 插件](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md) ·
  [Codex 插件](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/codex.md) ·
  [Cline hook](https://github.com/ClickHouse/loom/blob/main/integrations/cline/README.md)（实验性）
- **与团队共享。** 经验教训从仓库的代码命名空间以及调用中指明的命名空间读取（通过 MCP 时，默认是调用者自己的命名空间），因此保存在仓库代码命名空间中的经验教训，会触达每个有权读取该命名空间的 agent（令牌若被限定为只能访问自己的用户或会话，则读不到任何经验教训）。
  [与代码关联的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **各分支读取各自的代码。** 在其他分支上，经验教训会对照该分支自己的定义进行检查，因此仍保留旧文本的发布分支会显示为 `fresh`。
  [与代码关联的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **文档与代码并列。** 来自注册在仓库代码命名空间（即其主干）上的来源的文档，会带有该仓库的标签，因此针对该仓库的搜索会把它们与代码一起读取（sync worker 需要 `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`）。
  [文档来源](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **一份待审查列表。**
  [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) 会列出代码已变化、再也找不到或已消失的记忆，控制台则显示每条记忆的关联及其状态。

![控制台中某条记忆的详情：其关联的代码，一个定义为最新，另一个自某次提交后已变化](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## 开始

[快速开始](../getting-started/quickstart.md)会启动 Loom，而[添加仓库](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)会为某个仓库开启代码索引。使用插件或通过 MCP 连接你的编码 agent（[连接编码 agent](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)）。保存一条经验教训，用反引号或调用形式（`` `name()` ``）提到一个四个或更多字符的定义，或者提到一个限定名称，例如 `` `Reader.run` ``。Claude Code 和 Codex 插件会给其 hook 捕获的内容打上 `repo:<id>` 标签，会话引导信息中会指明该 id（[其生成方式](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)）。agent 自己通过 `loom_memory_set` 保存的经验教训，只要带有该标签（`tags: ["repo:<id>"]`）或写入仓库的代码命名空间，就会被关联；任何客户端都可以采用其中一种方式。通过 HTTP（或 Python SDK 中的 `agent.call`），保存在你自己命名空间中的经验教训，只有当调用在 `lesson_namespaces` 中指明该命名空间时才会被读取。每次关联轮次会关联至少已写入 60 秒的内容，且每 60 秒运行一次，因此一旦主干被索引，对该定义调用 `code.symbol` 就会在大约两分钟内于 `lessons` 下返回这条经验教训。

本页中的部分 op 在[状态页](https://github.com/ClickHouse/loom/blob/main/docs/reference/status.md)上标记为实验性。
