<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=b9e9c3f0bf6d83928302d688a3f5c2bb304521db model=claude-sonnet-5-5 -->
> 本页由机器从[英文原文](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)翻译，内容以英文原文为准。
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# 与代码关联的记忆，面向编码 agent

Loom 会把编码 agent 保存的关于某个仓库的记忆，关联到这些记忆所提到的函数、类和文件，在重构过程中持续跟踪这些符号，并在记忆所对应的代码发生变化时标记该记忆。默认情况下，对应代码已全部消失的记忆会从搜索结果中移除，而其他仍会展示该经验的读取操作，都会说明其代码是已移动、已变更还是已消失。该功能通过 MCP 运行，作用于自托管 Loom 已索引的仓库（即[代码索引](code-intelligence.md)，`LOOM_CODE_INGEST_ENABLED=true`），而负责维护这些关联的 `memory_code_links` worker 默认开启。

<a id="what-you-get"></a>

## 你能得到什么

- **经验附着在其所涉及的代码上。** 带有 `repo:<id>` 标签的记忆（Claude Code 和 Codex 插件会为其 hook 捕获的内容打上该标签），或写入仓库代码命名空间的记忆，会关联到它在反引号中提到的每个定义，写法为 `A::b` 或调用形式（`a.b()`），且只能解析到唯一位置；也会关联到它所列出的文件中、以单独的 camelCase 或 snake_case 单词提到的定义；以及它提到的、不包含上述任何定义的每个文件。尚未进入主干的限定名称，如果在 `memory_code_links_pending_days`（14）天内合并，也会建立关联。
  [与代码关联的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **关联随重构而更新。** 每次索引提交时，每个关联的状态为 `fresh`、`moved`、`changed`（并指明造成变更的提交）、`unresolved` 或 `missing`。关联会记录定义的 token，因此空白、注释和大多数格式化工具造成的改动都不会改变 `fresh` 状态。[关联状态](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **按你的选择处理过时的记忆。** 由命名空间的 [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) 决定：`retire_removed`（默认）会让代码已全部消失的记忆退出搜索，并标记已变更的代码；`flag` 只做标记；`revise` 同样让代码已消失的记忆退出搜索，并让模型对照新旧代码检查已变更记忆的论断，再给出修正后的论断建议。在所有策略下，搜索都会把代码已变更的记忆排在后面三条结果之后，而不是将其隐藏。Loom 从不改写记忆本身。
- **agent 读代码的任何地方都有经验可查。** `code.symbol` 会把关联到某个定义的记忆作为 `lessons` 返回；`code.search`、`code.callers`、`code.callees`、`code.impact`、`code.outline` 和 `code.context_pack` 会为其返回的文件附上相关经验；每条经验都带有其关联状态。Claude Code 和 Codex 的 hook 会在 agent 编辑文件之前展示该文件的记忆（`memory.for_file`），Cline 的 hook 则在读取或编辑文件时展示。[`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) · [Claude Code 插件](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **与团队共享。** 经验会从仓库的代码命名空间以及调用中指明的命名空间读取（通过 MCP 时，默认读取调用者自己的命名空间），因此保存在仓库代码命名空间中的经验可以被所有读取该代码的 agent 获取。
  [与代码关联的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **分支读取各自的代码。** 在其他分支上，经验会对照该分支自己的定义进行检查，因此仍保留旧文本的发布分支会显示为 `fresh`。
  [与代码关联的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **文档与代码并列。** 来自注册在仓库代码命名空间（即其主干命名空间）上的来源的文档，会带有该仓库的标签，因此针对该仓库的搜索会把这些文档与代码一并读取（同步 worker 需要设置 `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`）。
  [文档来源](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **审阅清单。** [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) 会列出代码已变更或已消失的记忆，控制台则会展示每条记忆的关联及其状态。

![控制台中一条记忆的详情：其关联的代码，一个定义为最新，另一个自某次提交后已变更](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## 开始使用

[快速开始](../getting-started/quickstart.md)会启动 Loom，而[添加仓库](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)会为某个仓库开启代码索引。通过插件或 MCP 连接你的编码 agent（[连接编码 agent](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)）。保存一条经验，在反引号中提到一个至少四个字符的定义，或像 `` `Reader.run` `` 这样的限定名称。Claude Code 和 Codex 插件会为你添加 `repo:<id>` 标签（[id 的推导方式](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)）；如果使用其他客户端，请把经验写入仓库的代码命名空间。每一轮关联处理会关联写入时间至少已达 60 秒的记忆，且每 60 秒运行一轮，因此主干索引完成后，对该定义调用 `code.symbol`，大约两分钟内就会在 `lessons` 下返回这条经验。
