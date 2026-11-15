<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=9cbe1bbc3b8361789f5d82dc35b67597e77e8dbf model=claude-sonnet-5-5 -->
> 本页由机器从[英文原文](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)翻译，内容以英文原文为准。
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# 与代码关联的记忆，供 coding agent 使用

Loom 会把 coding agent 保存的每条记忆，关联到记忆中提到的函数、类和文件，在重构后继续追踪这些符号，并在相关代码变更时标记该记忆。默认情况下，所关联的代码已全部消失的记忆会退出搜索，因此过时的经验不会再传给 agent。该功能运行在 Loom 已建立索引的仓库上（即[代码索引](code-intelligence.md)，`LOOM_CODE_INGEST_ENABLED=true`），负责维护关联的 `memory_code_links` worker 默认开启。

<a id="what-you-get"></a>

## 功能概览

- **经验与其所涉及的代码相关联。** 带有仓库标签的记忆（Claude Code 和 Codex 插件会为其会话打上标签），或写入仓库代码命名空间的记忆，会关联到它用反引号写出且恰好解析到唯一位置的每个定义（在它所列出的文件内，还包括以裸词形式提到的定义），以及它提到的、不含任何这些定义的每个文件。尚未进入主干的限定名，如果在 `memory_code_links_pending_days`（14）天内合并，也会建立关联。
  [与代码关联的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **关联随重构而更新。** 每次对提交建立索引时，每条关联的状态为 `fresh`、`moved`、`changed`（并给出导致变更的提交）、`unresolved` 或 `missing`。关联会记录定义的 token，因此空白、注释和大多数格式化工具的改动都不会改变 `fresh` 状态。
- **按你选择的方式处理过时的记忆。** 命名空间的 `code_change_policy` 可以让所关联代码已全部消失的记忆退出搜索（`retire_removed`，默认值），也可以只做标记（`flag`），或者让模型对照新旧代码核对该记忆的说法，并给出修正建议（`revise`）。Loom 从不自行改写记忆。
- **经验出现在 agent 阅读代码的地方。** `code.symbol` 会把关联到某个定义的记忆作为 `lessons` 返回，并附带各自的关联状态；`memory.for_file` 返回关联到某文件中代码的记忆；Claude Code 插件会在编辑该文件之前显示这些记忆。[`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Claude Code 插件](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **分支读取各自的代码。** 在仍保留旧文本的发布分支上，即使主干已修改了相关代码，经验仍显示为 `fresh`。
- **文档与代码并存。** 注册在仓库主干代码命名空间下的来源所产生的文档，会带上该仓库的标签，因此针对该仓库的搜索会把这些文档与代码一并读取（同步 worker 需要设置 `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`）。
  [文档来源](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **审阅清单。** `namespace.validate` 会列出所关联代码已变更或已消失的记忆，控制台会显示每条记忆的关联及其状态。

<a id="start"></a>

## 开始使用

[快速开始](../getting-started/quickstart.md)会启动 Loom，[添加仓库](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)会为某个仓库开启代码索引。通过插件或 MCP 连接你的 coding agent（[连接 coding agent](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)），然后保存一条用反引号写出某个定义的经验。主干建立索引、且下一轮关联处理（默认每 60 秒一次）运行之后，对该定义调用 `code.symbol` 就会在 `lessons` 下返回这条经验。
