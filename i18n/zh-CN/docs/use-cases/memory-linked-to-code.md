<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=bc08f5ccede7493df24bf88e34857dcfc36ff446 model=claude-sonnet-5-5 -->
> 本页由机器从[英文原文](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)翻译，内容以英文原文为准。
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# 面向编码 agent 的与代码关联的记忆

Loom 会把编码 agent 的记忆关联到记忆中提到的函数、类和文件，在重构后继续跟踪这些关联，并在对应代码发生变化时标记该记忆。默认情况下，搜索会排除代码已全部消失的记忆，除非有人固定、锁定或验证过它；每次返回经验教训的读取操作，都会说明其代码何时发生变化或已不存在。该功能通过 MCP、HTTP 和各 SDK 运行，作用于自托管 Loom 所索引的代码仓库（即[代码索引](code-intelligence.md)，需设置 `LOOM_CODE_INGEST_ENABLED=true`），而维护这些关联的 `memory_code_links` worker 默认开启。

<a id="what-you-get"></a>

## 功能概览

- **经验教训关联到相关代码。** 带有 `repo:<id>` 标签的记忆（Claude Code 和 Codex 插件会给其 hook 捕获的内容打上该标签），或写入仓库代码命名空间的记忆，会关联到它在反引号中提到的每个定义（函数、类或其他符号），或文本中任意位置以 `A::b` 或调用形式（`a.b()`、`name()`）出现且只解析到唯一位置的定义；也会关联到它所列出的文件中、以裸 camelCase 或 snake_case 单词提到的定义；以及它所列出的、不包含上述任何定义的每个文件。尚未进入主干的限定名称，如果在 `LOOM_MEMORY_CODE_LINKS_PENDING_DAYS`（14 天）内合并，也会建立关联。
  [与代码关联的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **关联随重构而更新。** 每次索引提交时，每条关联的状态为 `fresh`、`moved`、`changed`（会指明导致变化的提交）、`unresolved` 或 `missing`。关联记录的是定义的 token，因此空白、注释和大多数格式化工具造成的改动仍保持 `fresh`。[关联状态](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **按你的选择处理过时的记忆。** 由命名空间的 [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) 决定：`retire_removed`（默认）会把代码已全部消失的记忆排除在搜索之外，并标记代码已变化的记忆；`flag` 只做标记；`revise` 同样排除代码已消失的记忆，并让模型对照新旧代码检查已变化记忆的论断，给出修正后的论断建议。由人固定、锁定或验证过的记忆永远不会被排除。无论采用哪种策略，搜索通常会把代码已变化的记忆排到后面三条结果之后，而不是将其隐藏；被保留的记忆和按名称找到的条目位置不变。Loom 不会改写记忆本身。
  [策略](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **agent 读代码的任何地方都能看到经验教训。** `code.symbol` 会把关联到某个定义的记忆作为 `lessons` 返回；`code.search`、`code.callers`、`code.callees`、`code.impact`、`code.outline` 以及任务的 `code.context_pack` 会为其返回的文件附上相关经验教训；每条经验教训都带有其关联状态。Claude Code 和 Codex 的 hook 会在 agent 编辑文件之前展示该文件的记忆（`memory.for_file`），Cline 的 hook 则在读取或编辑文件时展示。[`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Claude Code 插件](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md) ·
  [Codex 插件](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/codex.md) ·
  [Cline hook](https://github.com/ClickHouse/loom/blob/main/integrations/cline/README.md)（实验性）
- **与团队共享。** 经验教训会从仓库的代码命名空间以及调用中指明的命名空间读取（通过 MCP 时，默认读取调用者自己的命名空间），因此保存在仓库代码命名空间中的经验教训，可以送达每个有权读取该命名空间的 agent（范围收窄到其所属用户或会话的令牌读不到任何经验教训）。
  [与代码关联的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **各分支读取各自的代码。** 在其他分支上，经验教训会对照该分支自己的定义进行检查，因此仍保留旧文本的发布分支会显示为 `fresh`。
  [与代码关联的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **文档与代码并列。** 来自注册在仓库代码命名空间（即其主干）上的来源的文档，会带有该仓库的标签，因此针对该仓库的搜索可以把这些文档与代码一并读取（同步 worker 需要 `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`）。
  [文档来源](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **审查列表。**
  [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) 会列出代码已变化、无法再次找到或已消失的记忆，控制台则显示每条记忆的关联及其状态。

![控制台中的记忆详情：其关联的代码，一个定义是最新的，另一个自某次提交以来已变化](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## 开始使用

[快速入门](../getting-started/quickstart.md)会启动 Loom，而[添加仓库](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)会为某个仓库开启代码索引。通过插件或 MCP 连接你的编码 agent（[连接编码 agent](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)）。保存一条经验教训，在反引号中写出一个四个字符及以上的定义名，或形如 `` `Reader.run` `` 的限定名。Claude Code 和 Codex 插件会给其 hook 捕获的内容打上 `repo:<id>` 标签，会话引导信息中会写明该 id（[如何得出](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)）。agent 自己通过 `loom_memory_set` 保存的经验教训，在带有该标签（`tags: ["repo:<id>"]`）或写入仓库代码命名空间时会建立关联；任何客户端都可以采用其中一种方式。通过 HTTP（或 Python SDK 中的 `agent.call`）时，保存在你自己命名空间中的经验教训，只有在调用的 `lesson_namespaces` 中指明该命名空间时才会被读取。每轮关联处理只会关联至少 60 秒之前的写入，且每 60 秒运行一轮，因此主干索引完成后，对该定义调用 `code.symbol`，大约两分钟内就会在 `lessons` 下返回这条经验教训。

本页中的部分操作在[状态页](https://github.com/ClickHouse/loom/blob/main/docs/reference/status.md)上标记为实验性。
