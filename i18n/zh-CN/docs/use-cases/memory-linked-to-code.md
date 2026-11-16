<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=b1cc9eef83359902a76126407096411d372e2f5e model=claude-sonnet-5-5 -->
> 本页由机器从[英文原文](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)翻译，内容以英文原文为准。
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# 与代码关联的记忆，面向编码 agent

Loom 把编码 agent 的记忆与其中提到的函数、类和文件关联起来，在重构后继续跟踪这些关联，并在代码变更时标记对应的记忆。默认情况下，如果一条记忆所关联的代码已全部消失，搜索会将其排除，除非有人固定、锁定或验证过它；每次返回经验教训的读取操作，都会说明其代码何时变更或已消失。该功能通过 MCP、HTTP 和 SDK 运行，作用于自托管 Loom 已建立索引的仓库（即[代码索引](code-intelligence.md)，`LOOM_CODE_INGEST_ENABLED=true`），负责维护关联的 `memory_code_links` worker 默认开启。

<a id="what-you-get"></a>

## 你将获得什么

- **经验教训附着在它所涉及的代码上。** 带有 `repo:<id>` 标签的记忆（Claude Code 和 Codex 插件会给其钩子捕获的内容打上该标签），或写入仓库代码命名空间的记忆，会被关联到它所提到的每个定义（函数、类或其他符号）：用反引号括起的名称，或文本中任何位置以 `A::b` 或调用形式（`a.b()`、`name()`）出现且只能解析到唯一位置的名称；对于它所列出的文件，则关联其中以单独的 camelCase 或 snake_case 单词命名的定义；对于它所列出的、不包含上述任何定义的文件，则关联该文件本身。尚未合入主干的限定名，如果在 `LOOM_MEMORY_CODE_LINKS_PENDING_DAYS`（14 天）内合入，也会建立关联。
  [与代码关联的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **关联随重构而更新。** 每次已索引的提交，都会把每条关联读作 `fresh`、`moved`、`changed`（并指出造成变更的提交）、`unresolved` 或 `missing`。关联记录的是定义的 token，因此空白、注释和大多数格式化工具的改动都会让它保持 `fresh`。[关联状态](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **按你选择的方式处理过时的记忆。** 由命名空间的 [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) 决定：`retire_removed`（默认）会把代码已全部消失的记忆排除在搜索之外，并标记代码已变更的记忆；`flag` 只做标记；`revise` 同样排除代码已消失的记忆，并让模型对照新旧代码检查已变更记忆的论断，再给出修正后的论断建议。人工固定、锁定或验证过的记忆永远不会被排除。在所有策略下，搜索通常会把代码已变更的记忆排到后面三条结果之后，而不是将其隐藏；被保留的记忆和按名称找到的行则保持原位。Loom 从不改写记忆本身。
  [策略](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **agent 读代码的任何地方都能看到经验教训。** `code.symbol` 会把与某个定义关联的记忆作为 `lessons` 返回；`code.search`、`code.callers`、`code.callees`、`code.impact`、`code.outline` 以及任务的 `code.context_pack` 会为其返回的文件附上相关经验教训；每条经验教训都带有其关联状态。Claude Code 和 Codex 的钩子会在 agent 编辑文件之前显示该文件的记忆（`memory.for_file`），Cline 的钩子则在读取或编辑文件时显示。[`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) · [Claude Code 插件](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md) · [Codex 插件](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/codex.md)
- **与团队共享。** 经验教训从仓库的代码命名空间以及调用中指明的命名空间读取（通过 MCP 时，默认是调用者自己的命名空间），因此保存在仓库代码命名空间中的经验教训，会送达所有有权读取该命名空间的 agent（令牌若被限定为仅限其自身用户或会话，则读不到任何经验教训）。
  [与代码关联的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **各分支读取各自的代码。** 在其他分支上，经验教训会对照该分支自己的定义进行检查，因此仍保留旧文本的发布分支会读作 `fresh`。
  [与代码关联的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **文档与代码并列。** 来自仓库代码命名空间（其主干）上注册的来源的文档，会带有该仓库的标签，因此针对该仓库的搜索可以连同代码一起读取这些文档（同步 worker 需要 `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`）。
  [文档来源](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **审阅清单。** [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) 会列出代码已变更、无法再次找到或已消失的记忆，控制台则显示每条记忆的关联及其状态。

![控制台中记忆的详情：其关联的代码，一个定义是最新的，另一个自某次提交以来已变更](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## 开始

[快速入门](../getting-started/quickstart.md)会启动 Loom，[添加仓库](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)则为某个仓库开启代码索引。通过插件或 MCP 连接你的编码 agent（[连接编码 agent](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)）。保存一条经验教训，用反引号写出四个或更多字符的定义名，或像 `` `Reader.run` `` 这样的限定名。Claude Code 和 Codex 插件会给其钩子捕获的内容打上 `repo:<id>` 标签，会话引导信息中会写明该 id（[如何得出](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)）。agent 自己用 `loom_memory_set` 保存的经验教训，在带有该标签（`tags: ["repo:<id>"]`）或写入仓库代码命名空间时会建立关联；任何客户端都可以采用其中一种方式。通过 HTTP（或 Python SDK 中的 `agent.call`），保存在你自己命名空间中的经验教训，只有当调用在 `lesson_namespaces` 中指明该命名空间时才会被读取。每轮关联处理会关联至少已写入 60 秒的内容，并且每 60 秒运行一轮，因此主干完成索引后，对该定义调用 `code.symbol`，大约两分钟内就会在 `lessons` 下返回这条经验教训。

`code.callees` 和 `code.outline` 处于实验阶段（[状态](https://github.com/ClickHouse/loom/blob/main/docs/reference/status.md)）；本页其余部分是稳定的。
