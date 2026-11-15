<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=ba0db8ea1cf39d2ef1aa0c782706183107d553a7 model=claude-sonnet-5-5 -->
> 本页由机器从[英文原文](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)翻译，内容以英文原文为准。
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# 为编码 agent 提供与代码关联的记忆

Loom 会把编码 agent 保存的关于某个仓库的记忆，关联到记忆中提到的函数、类和文件，在重构过程中跟踪这些符号，并在记忆所对应的代码发生变化时为该记忆打上标记。默认情况下，如果一条记忆对应的代码已全部消失，它就会退出搜索结果，这样过时的经验不会再传到 agent 那里。该功能运行在 Loom 已建立索引的仓库上（即[代码索引](code-intelligence.md)，`LOOM_CODE_INGEST_ENABLED=true`），而负责维护这些关联的 `memory_code_links` worker 默认开启。

<a id="what-you-get"></a>

## 你能得到什么

- **经验与其相关的代码绑定。** 带有 `repo:<id>` 标签的记忆（Claude Code 和 Codex 插件会给其 hook 捕获的内容打上该标签），或写入某个仓库代码命名空间的记忆，会关联到以下对象：用反引号写出且只能解析到唯一位置的每个定义；记忆中列出的文件里、以单独的 camelCase 或 snake_case 单词提到的定义；以及记忆中提到、但文件内不含上述任何定义的每个文件。尚未进入主干的限定名，如果在 `memory_code_links_pending_days`（14）天内合并，也会建立关联。
  [关联到代码的记忆](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **关联会跟随重构。** 每次提交被索引时，每个关联的状态为 `fresh`、`moved`、`changed`（会指明改动它的提交）、`unresolved` 或 `missing` 之一。关联记录的是定义的 token，因此空白、注释和大多数格式化工具的改动都会让它保持 `fresh`。
- **按你的选择处理过时的记忆。** 由命名空间的 [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md) 决定：`retire_removed`（默认）会让代码已全部消失的记忆退出搜索，并标记代码已变化的记忆；`flag` 只做标记；`revise` 同样会让代码已消失的记忆退出搜索，并让模型对照新旧代码检查已变化记忆的论断，给出修正后的论断建议。在所有策略下，搜索都会把代码已变化的记忆排到后面三条结果之后，而不是将其隐藏。Loom 绝不会改写记忆本身。
- **agent 读代码的任何地方都能看到经验。** `code.symbol` 以 `lessons` 的形式返回关联到某个定义的记忆；`code.search`、`code.callers`、`code.callees`、`code.impact`、`code.outline` 和 `code.context_pack` 会为其返回的文件附上相关经验；每条经验都带有其关联状态。Claude Code 和 Codex 的 hook 会在 agent 编辑文件之前显示该文件的记忆（`memory.for_file`），Cline 的 hook 则在 agent 读取或编辑文件时显示。
  [`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Claude Code 插件](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **与团队共享。** 经验会从仓库的团队命名空间读取，默认还会读取调用者自己的命名空间，因此某个 agent 保存在那里的经验，会传到所有读取该代码的 agent。
- **分支读取各自的代码。** 在其他分支上，经验会对照该分支自己的定义进行检查，因此仍保留旧文本的发布分支会显示为 `fresh`。
- **文档与代码放在一起。** 在仓库主干代码命名空间上注册的来源所产生的文档，会带上该仓库的标签，因此针对该仓库的搜索会把这些文档与其代码一并读取（同步 worker 需要 `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`）。
  [文档来源](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **一份审查清单。**
  [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) 会列出代码已变化或已消失的记忆，控制台则显示每条记忆的关联及其状态。

![控制台中一条记忆的详情：其关联的代码，一个定义是最新的，另一个自某次提交以来已变化](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## 开始使用

[快速开始](../getting-started/quickstart.md)会启动 Loom，[添加仓库](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)则为某个仓库开启代码索引。通过插件或 MCP 连接你的编码 agent（[连接编码 agent](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)）。保存一条经验，其中用反引号写出一个长度至少为四个字符的定义名（或限定名，例如 `` `Reader.run` ``），并打上 `repo:<id>` 标签，或写入该仓库的代码命名空间。待主干完成索引，并且在保存一分钟之后运行过一次关联处理（每 60 秒运行一次，并落后最新写入 60 秒，因此大约两分钟内即可完成），对该定义调用 `code.symbol` 就会在 `lessons` 下返回这条经验。
