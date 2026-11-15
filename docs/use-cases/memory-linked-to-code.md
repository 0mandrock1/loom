# Memory linked to code for coding agents

Loom links the memories coding agents save about a repository to the functions, classes
and files they name, follows those symbols through refactors, and flags a memory when its
code changes. By default, a memory whose code is all gone leaves search results, and every
other read that still shows a lesson says whether its code moved, changed or is gone. It
runs over MCP, HTTP and the SDKs on the repositories a self-hosted Loom indexes
(the [code index](code-intelligence.md), `LOOM_CODE_INGEST_ENABLED=true`), and the
`memory_code_links` worker that keeps the links is on by default.

## What you get

- **Lessons attached to the code they are about.** A memory tagged `repo:<id>`
  (the Claude Code and Codex plugins tag what their hooks capture) or written in the
  repository's code namespace is linked to each definition it names in backticks, as
  `A::b` or as a call (`a.b()`), that resolves to exactly one place; to definitions in the
  files it lists that it names by a bare camelCase or snake_case word; and to each file it
  lists that holds none of them. A qualified name not on trunk yet links if it merges
  within `LOOM_MEMORY_CODE_LINKS_PENDING_DAYS` (14).
  [Memories linked to code](../concepts/memory.md#memories-linked-to-code)
- **Links that follow refactors.** On every indexed commit, each link reads `fresh`,
  `moved`, `changed` (naming the commit that changed it), `unresolved` or `missing`. A
  link records the definition's tokens, so whitespace, comments and most formatter changes
  leave it `fresh`. [Link states](../concepts/memory.md#memories-linked-to-code)
- **Stale memories handled the way you choose.** The namespace's
  [`code_change_policy`](../reference/operations/namespace.md#namespaceregister) decides:
  `retire_removed` (the default) leaves a memory whose code is all gone out of search and
  flags changed code; `flag` only flags; `revise` also leaves gone-code memories out, and
  has the model check a changed one's claim against the old and new code and suggest a
  corrected claim. Under every policy, search ranks a memory whose code changed below the
  next three results instead of hiding it. Loom never rewrites the memory itself.
- **Lessons wherever the agent reads code.** `code.symbol` returns the memories linked to
  a definition as `lessons`; `code.search`, `code.callers`, `code.callees`, `code.impact`,
  `code.outline` and a task's `code.context_pack` attach the lessons for the files they
  return; each lesson carries its link state. The Claude Code and Codex hooks show a
  file's memories (`memory.for_file`) before an agent edits it, and the Cline hooks when
  it reads or edits one. [`code.symbol`](../reference/operations/code.md#codesymbol) ·
  [Claude Code plugin](../features/integrations/claude-code.md)
- **Shared with the team.** Lessons are read from the repository's code namespace and the
  namespaces the call names (over MCP, the caller's own by default), so a lesson saved in
  the repository's code namespace reaches every agent that reads that code.
  [Memories linked to code](../concepts/memory.md#memories-linked-to-code)
- **Branches read their own code.** On another branch, a lesson is checked against that
  branch's own definition, so a release branch that still has the old text reads `fresh`.
  [Memories linked to code](../concepts/memory.md#memories-linked-to-code)
- **Documents beside the code.** Documents from a source registered on the repository's
  code namespace (its trunk's) carry the repository's tag, so searches over the repository
  read them with its code (the sync worker needs `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`).
  [Document sources](../features/knowledge.md#document-sources)
- **A review list.**
  [`namespace.validate`](../reference/operations/namespace.md#namespacevalidate) lists the
  memories whose code changed, was not found again or is gone, and the dashboard shows
  each memory's links and their state.

![A memory's detail in the dashboard: its linked code, one definition current and one changed since a commit](../assets/screenshots/memory-linked-code.webp)

## Start

[Quickstart](../getting-started/quickstart.md) boots Loom, and
[adding repositories](../features/code-index/repositories.md) turns the code index on for
a repository. Connect your coding agent with its plugin or over MCP
([connect a coding agent](../getting-started/connect-a-coding-agent.md)). Save a lesson
that names a definition of four or more characters in backticks, or a qualified one such
as `` `Reader.run` ``. The Claude Code and Codex plugins tag what their hooks capture with
`repo:<id>`, and the session primer names the id
([how it is derived](../features/integrations/claude-code.md#configuration)). A lesson the
agent saves itself with `loom_memory_set` links when it carries that tag
(`tags: ["repo:<id>"]`) or is written in the repository's code namespace; any client can
do either. Each link pass links writes at least 60 seconds old and passes run every 60
seconds, so once trunk is indexed, `code.symbol` on that definition returns the lesson
under `lessons` within about two minutes.
