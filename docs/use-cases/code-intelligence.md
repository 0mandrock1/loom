# A code index for coding agents

Loom gives coding agents a shared index of your git repositories, served over MCP, so an
agent asks a structure question and gets the lines that answer it instead of reading whole
files. Needs a repository added and `LOOM_CODE_INGEST_ENABLED=true`. How the index is
built and kept fresh is in [the code index overview](../features/code-index/overview.md);
what it gives in each language is in [code index by language](code-intelligence-by-language.md).

## What you get

- **Definitions, callers and impact.** `code.symbol`, `code.callers` and `code.impact`
  locate code with file and line references. `code.index_status` reports the indexed
  commit. [Code operations](../features/code-index/operations.md)
- **Search by meaning and by exact string.** `code.search` finds where a mechanism lives;
  `code.grep` finds an exact message or identifier.
- **History and pull requests.** Merged-PR history with review comments, a pre-PR
  duplicate check and bisect candidates for a regression. These need PR history turned on
  (`LOOM_CODE_PR_HISTORY_ENABLED`); review comments and open PRs also need a GitHub token.
  [History, PRs and releases](../features/code-index/history-and-prs.md) says what each
  needs.
- **Lessons on the code.** `code.symbol` returns the memories linked to a definition, and
  `code.search`, `code.callers`, `code.callees`, `code.impact`, `code.outline` and a task's
  `code.context_pack` attach the lessons for the files they return, each with whether its
  code moved, changed or is gone. [Memory linked to code](memory-linked-to-code.md)
- **Freshness you can see.** The index trails the branch by merged commits, and a stale
  answer says so. [Freshness](../features/code-index/freshness.md)
- **Plugins and MCP clients.** The Claude Code plugin connects on the `coding` toolset and
  the Codex plugin on `core,coding,tracker,tasks`; both list the code tools. Cursor, GitHub
  Copilot, Gemini CLI, Cline, Kilo Code, OpenCode and other MCP clients list them with
  `?toolset=coding` on their URL (`loom install` adds it for Cursor; `--toolset coding` adds
  it for the others it writes).
  [Connect a coding agent](../getting-started/connect-a-coding-agent.md) ·
  [MCP clients](../features/integrations/mcp-clients.md)

## Measured

On the ClickHouse repository, Claude Code was asked to end each answer with up to three
files. With Loom it named the right file for 96.4% of 36 conceptual questions (10 paired
runs), against 91.4% without Loom, and the runs with Loom cost 21% less (the agent's model
bill; Loom's own model calls excluded).
On 200 SWE-bench Verified issues, `code.search` ranked a changed file in its first 10
results for 82% (file localization, not issue resolution), against 57% for BM25 over every
indexed file and 79% for BM25 over `.py` files only; the `.py`-only gap is within noise.
[With and without Loom](../benchmarks/results.md#with-and-without-loom) ·
[SWE-bench Verified file localization](../benchmarks/results.md#swe-bench-verified-file-localization)

## A coding agent that reads the definition it needs

Start with a repository you trust. On the released stack, follow the
[operator-configured index steps](../getting-started/quickstart.md#optional-index-a-repository-on-the-released-stack);
the dashboard's add-repository flow needs a sandbox that stack does not enable. Connect
Claude Code or Codex with its plugin and start a session inside that repository's checkout
([repository matching](../getting-started/connect-a-coding-agent.md#when-the-code-index-is-used)).

1. Ask the agent to call `loom_op` with `op: "code.index_status"` and
   `arguments: {"namespace": "<indexed namespace>", "brief": true}`. Record `git_head`,
   the indexed commit; an empty value means the first index has not finished.
2. Pick a function whose definition you know. Ask the agent to call `loom_code_symbol`
   in that namespace with the function's name and `include_body: true`. Check the
   returned `path`, `start_line`, `end_line`, `body` and `tier`.
3. Compare the definition with the source at `git_head` before relying on it. A missing
   or ambiguous definition needs a narrower name or a local read.
4. Start a fresh session in the same checkout and repeat both calls. The index supplies
   the definition again; the agent need not load the previous chat or the whole file.

This checks one indexed definition. Uncommitted edits still need local reads, and a
successful lookup is not a measurement of task accuracy or cost savings. See
[code operations](../features/code-index/operations.md) for search, callers and impact.

## Start

[Adding repositories](../features/code-index/repositories.md) turns the index on for a
repository; [the code index overview](../features/code-index/overview.md) explains the
tiers.

Some ops on this page are marked experimental on the [status page](../reference/status.md).
