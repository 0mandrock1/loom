<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=248230780bfc4a7af42bcb776c9574c89ec21839 model=claude-sonnet-5-5 -->
> 本页由机器从[英文原文](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)翻译，内容以英文原文为准。
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# 为编码 agent 关联到代码的记忆

Loom 会把编码 agent 保存的、关于某个仓库的记忆，关联到这些记忆所提到的函数、类和文件，在重构过程中跟踪这些符号，并在记忆对应的代码发生变化时为其打上标记。默认情况下，对应代码已全部消失的记忆不再出现在搜索结果中，而其他仍会展示这条经验的读取操作，都会说明它的代码是已移动、已变更还是已消失。该功能通过 MCP、HTTP 和 SDK 运行，作用于自托管 Loom 所索引的仓库（即[代码索引](code-intelligence.md)，需设置 `LOOM_CODE_INGEST_ENABLED=true`），负责维护这些关联的 `memory_code_links` worker 默认开启。

<a id="what-you-get"></a>

## 你能得到什么

- **经验与其所涉及的代码绑定。** 带有 `repo:<id>` 标签的记忆（Claude Code 和 Codex 插件会为其钩子捕获的内容打上该标签），或写入仓库代码命名空间的记忆，会关联到：它用反引号写出的、以 `A::b` 形式或调用形式（`a.b()`）出现且恰好解析到一处的每个定义；它列出的文件中、以单独的 camelCase 或 snake_case 单词提到的定义；以及它列出的、不包含上述任何定义的每个文件。主干上尚不存在的限定名称，如果在 `LOOM_MEMORY_CODE_LINKS_PENDING_DAYS`（14）天内合并，也会建立关联。
  [关联到代码的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **关联随重构而更新。** 每次索引提交时，每条关联的状态为 `fresh`、`moved`、`changed`（并给出导致变更的提交）、`unresolved` 或 `missing`。关联记录的是定义的 token，因此空白、注释以及大多数格式化工具造成的改动都不会改变 `fresh` 状态。[关联状态](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **按你的选择处理过时的记忆。** 命名空间的 [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) 决定处理方式：`retire_removed`（默认）会让代码已全部消失的记忆不再出现在搜索中，并标记代码已变更的记忆；`flag` 只做标记；`revise` 同样让代码已消失的记忆退出搜索，并让模型对照新旧代码检查已变更记忆的论断，给出修正后的论断建议。无论采用哪种策略，搜索都会把代码已变更的记忆排在后面三条结果之后，而不是将其隐藏。Loom 绝不会改写记忆本身。
- **agent 读代码的每个地方都有经验。** `code.symbol` 以 `lessons` 返回关联到某个定义的记忆；`code.search`、`code.callers`、`code.callees`、`code.impact`、`code.outline` 以及任务的 `code.context_pack` 会为它们返回的文件附上相关经验；每条经验都带有其关联状态。Claude Code 和 Codex 的钩子会在 agent 编辑文件之前展示该文件的记忆（`memory.for_file`），Cline 的钩子则在读取或编辑文件时展示。[`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Claude Code 插件](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **与团队共享。** 经验会从仓库的代码命名空间以及调用中指定的命名空间读取（通过 MCP 时，默认读取调用者自己的命名空间），因此保存在仓库代码命名空间中的经验，会触达每一个读取该代码的 agent。
  [关联到代码的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **分支读取各自的代码。** 在其他分支上，经验会对照该分支自己的定义进行检查，因此仍保留旧代码的发布分支会显示为 `fresh`。
  [关联到代码的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **文档与代码并列。** 在仓库代码命名空间（即其主干的命名空间）上注册的来源所产生的文档，会带上该仓库的标签，因此针对该仓库的搜索会把这些文档与代码一并读取（同步 worker 需要 `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`）。
  [文档来源](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **审阅清单。**
  [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) 会列出代码已变更、再也找不到或已消失的记忆，控制台则展示每条记忆的关联及其状态。

![控制台中一条记忆的详情：它所关联的代码，其中一个定义是当前的，另一个自某次提交以来已变更](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## 开始使用

[快速入门](../getting-started/quickstart.md)会启动 Loom，而[添加仓库](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)会为某个仓库开启代码索引。通过插件或 MCP 连接你的编码 agent（[连接编码 agent](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)）。保存一条经验，在其中用反引号写出某个四个字符及以上的定义名，或形如 `` `Reader.run` `` 的限定名。Claude Code 和 Codex 插件会为其钩子捕获的内容打上 `repo:<id>` 标签，会话引导信息会给出该 id（[它是如何得出的](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)）。agent 自己通过 `loom_memory_set` 保存的经验，在带有该标签（`tags: ["repo:<id>"]`）或写入仓库代码命名空间时会建立关联；任何客户端都可以采用其中任意一种方式。每轮关联处理会处理至少写入 60 秒的内容，且每 60 秒运行一轮，因此一旦主干完成索引，对该定义调用 `code.symbol` 大约两分钟内就会在 `lessons` 下返回这条经验。
