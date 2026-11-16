<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=28b15a8a4fd05c45dfe8244e3dd6a3e41528f1d2 model=claude-sonnet-5-5 -->
> 本页由机器从[英文原文](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)翻译，内容以英文原文为准。
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# 与代码关联的记忆，面向编码 agent

Loom 将编码 agent 的记忆与其中提到的函数、类和文件关联起来，在重构后继续跟踪这些关联，并在代码变更时标记相应的记忆。默认情况下，如果一条记忆所关联的代码已全部消失，搜索会将其排除，除非有人固定、锁定或验证过它；每次返回经验教训的读取，都会说明其代码何时发生了变更或已消失。该功能通过 MCP、HTTP 和 SDK 运行，作用于自托管 Loom 所索引的仓库（即[代码索引](code-intelligence.md)，`LOOM_CODE_INGEST_ENABLED=true`），而维护这些关联的 `memory_code_links` worker 默认开启。

<a id="what-you-get"></a>

## 你能得到什么

- **经验教训附着在它所涉及的代码上。** 带有 `repo:<id>` 标签的记忆（Claude Code 和 Codex 插件会为其钩子捕获的内容打上该标签），或写入仓库代码命名空间的记忆，会关联到它用反引号写出的每个定义（函数、类或其他符号），或在文本任意位置以 `A::b` 或调用形式（`a.b()`、`name()`）写出且恰好能解析到唯一位置的定义；关联到它所列文件中以单独的 camelCase 或 snake_case 词语提到的定义；以及它所列出的、不包含上述任何定义的每个文件。尚未进入主干的限定名称，如果在 `LOOM_MEMORY_CODE_LINKS_PENDING_DAYS`（14 天）内合并，也会建立关联。
  [与代码关联的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **关联随重构而移动。** 在每次被索引的提交上，每条关联的状态为 `fresh`、`moved`、`changed`（会指明导致变更的提交）、`unresolved` 或 `missing`。关联记录的是定义的 token，因此空白、注释和大多数格式化工具的改动都不会改变 `fresh` 状态。[关联状态](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **按你选择的方式处理过时的记忆。** 由命名空间的 [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) 决定：`retire_removed`（默认）会让代码已全部消失的记忆不出现在搜索中，并标记已变更的代码；`flag` 只做标记；`revise` 同样排除代码已消失的记忆，并让模型对照新旧代码检查已变更记忆的论断，再建议修正后的论断。由人固定、锁定或验证过的记忆永远不会被排除。无论采用哪种策略，搜索通常会把代码已变更的记忆排在后面三条结果之后，而不是将其隐藏；被保留的记忆和按名称找到的行则保持原位。Loom 从不改写记忆本身。
  [策略](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **agent 读代码的任何地方都能看到经验教训。** `code.symbol` 以 `lessons` 返回与某个定义关联的记忆；`code.search`、`code.callers`、`code.callees`、`code.impact`、`code.outline` 以及任务的 `code.context_pack` 会为其返回的文件附带相应的经验教训；每条经验教训都带有其关联状态。Claude Code 和 Codex 的钩子会在 agent 编辑文件之前展示该文件的记忆（`memory.for_file`），Cline 的钩子则在读取或编辑文件时展示。[`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Claude Code 插件](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md) ·
  [Codex 插件](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/codex.md)
- **与团队共享。** 经验教训从仓库的代码命名空间以及调用中指明的命名空间读取（通过 MCP 时，默认读取调用者自己的命名空间），因此保存在仓库代码命名空间中的经验教训，会送达每个有权读取该命名空间的 agent（限定于自身用户或会话的令牌读不到任何经验教训）。
  [与代码关联的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **各分支读取各自的代码。** 在其他分支上，经验教训会对照该分支自己的定义进行检查，因此仍保留旧文本的发布分支会显示为 `fresh`。
  [与代码关联的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **文档与代码并列。** 来自注册在仓库代码命名空间（即其主干）上的来源的文档，会带有该仓库的标签，因此针对该仓库的搜索会将这些文档与其代码一并读取（同步 worker 需要 `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`）。
  [文档来源](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **复查清单。**
  [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) 会列出代码已变更、未能重新找到或已消失的记忆，控制台则显示每条记忆的关联及其状态。

![控制台中一条记忆的详情：其关联的代码，一个定义为当前状态，另一个自某次提交以来已变更](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## 开始

[快速入门](../getting-started/quickstart.md)会启动 Loom，而[添加仓库](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)会为某个仓库开启代码索引。通过插件或 MCP 连接你的编码 agent（[连接编码 agent](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)）。保存一条经验教训，用反引号写出一个至少四个字符的定义，或写出如 `` `Reader.run` `` 这样的限定名称。Claude Code 和 Codex 插件会为其钩子捕获的内容打上 `repo:<id>` 标签，会话引导信息中会写明该 id（[如何得出](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)）。agent 自己用 `loom_memory_set` 保存的经验教训，在带有该标签（`tags: ["repo:<id>"]`）或写入仓库代码命名空间时会建立关联；任何客户端都可以采用其中一种方式。通过 HTTP（或 Python SDK 中的 `agent.call`）时，保存在你自己命名空间中的经验教训，只有在调用的 `lesson_namespaces` 中指明该命名空间时才会被读取。每次关联处理会关联至少 60 秒前的写入，处理每 60 秒运行一次，因此一旦主干完成索引，对该定义调用 `code.symbol` 大约两分钟内就会在 `lessons` 下返回这条经验教训。

`code.callees` 和 `code.outline` 为实验性功能（[状态](https://github.com/ClickHouse/loom/blob/main/docs/reference/status.md)）；本页其余部分均为稳定功能。
