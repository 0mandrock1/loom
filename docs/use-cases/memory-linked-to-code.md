# Memory linked to code for coding agents

Loom links each memory a coding agent saves about your code to the definitions and files
it names, and keeps those links current as the code changes: a lesson about a function
follows it through moves and renames, is flagged when the function's text changes, and
leaves search results once the code is gone. It runs on the repositories Loom indexes
(the [code index](code-intelligence.md)), and the `memory_code_links` worker is on by
default.

## What you get

- **Lessons attached to the code they are about.** A memory tagged with a repository (the
  Claude Code and Codex plugins tag their sessions) or written in a repository's code
  namespace is linked to each definition it names in backticks that resolves to exactly
  one place, and to each file it names. A name that is not on trunk yet links once it
  merges. [Memories linked to code](../concepts/memory.md#memories-linked-to-code)
- **Links that follow the code.** On every indexed commit, each link reads `fresh`,
  `moved`, `changed` (naming the commit that changed it), `unresolved` or `missing`. A
  link records the definition's tokens, so a formatter or a comment edit leaves it
  `fresh`.
- **Stale memories handled the way you choose.** The namespace's `code_change_policy`
  leaves a memory whose code is all gone out of search (`retire_removed`, the default),
  only flags it (`flag`), or has the model check the claim against the old and new code
  and suggest a corrected one (`revise`). Loom never rewrites the memory itself.
- **The lessons appear where the agent reads code.** `code.symbol` returns the memories
  linked to a definition as `lessons`, each with its link state; `memory.for_file` serves
  the memories linked to code in a file; the Claude Code plugin shows them before an edit
  to that file. [Code operations](../features/code-index/operations.md) ·
  [Claude Code plugin](../features/integrations/claude-code.md)
- **Branches read their own code.** On a release branch that still has the old text, a
  lesson reads `fresh` after trunk changed it.
- **Documents beside the code.** Documents from a source registered on a repository's
  trunk code namespace carry the repository's tag, so searches over the repository read
  them with its code (the sync worker needs `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`).
  [Document sources](../features/knowledge.md#document-sources)
- **A review list.** `namespace.validate` lists the memories whose code changed or is
  gone, and the dashboard shows each memory's links and their state.

## Start

[Quickstart](../getting-started/quickstart.md) boots Loom, and
[adding repositories](../features/code-index/repositories.md) turns the code index on for a
repository. Connect your coding agent with its plugin or over MCP
([connect a coding agent](../getting-started/connect-a-coding-agent.md)), save a lesson
that names a definition in backticks, then ask `code.symbol` about that definition.
