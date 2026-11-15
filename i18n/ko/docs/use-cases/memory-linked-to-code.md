<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=9cbe1bbc3b8361789f5d82dc35b67597e77e8dbf model=claude-sonnet-5-5 -->
> 이 페이지는 [영어 원문](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)을 기계 번역한 것이며, 영어 원문이 기준입니다.
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# 코딩 에이전트를 위한 코드에 연결된 메모리

Loom은 코딩 에이전트가 저장한 각 메모리를 그 메모리가 언급하는 함수, 클래스, 파일에 연결하고, 리팩터링을 거치는 동안 해당 심볼을 추적하며, 그 코드가 바뀌면 메모리에 표시합니다.
기본적으로 연결된 코드가 모두 사라진 메모리는 검색에서 제외되므로, 오래된 교훈이 에이전트에 전달되지 않습니다. 이 기능은 Loom이 인덱싱하는 리포지토리([코드 인덱스](code-intelligence.md), `LOOM_CODE_INGEST_ENABLED=true`)에서 동작하며, 링크를 유지하는 `memory_code_links` 워커는 기본으로 켜져 있습니다.

<a id="what-you-get"></a>

## 제공 기능

- **교훈을 해당 코드에 연결.** 리포지토리 태그가 붙은 메모리(Claude Code와 Codex 플러그인은 세션에 태그를 붙입니다)나 리포지토리의 코드 네임스페이스에 작성된 메모리는, 백틱으로 언급한 정의 중 정확히 한 곳으로 해석되는 각 정의에 연결됩니다. 메모리가 나열한 파일 안에서는 단어만으로 언급한 정의에도 연결되고, 그런 정의가 하나도 없는 파일은 파일 자체에 연결됩니다. 아직 trunk에 없는 정규화된 이름은 `memory_code_links_pending_days`(14) 안에 병합되면 연결됩니다.
  [코드에 연결된 메모리](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **리팩터링을 따라가는 링크.** 인덱싱된 모든 커밋마다 각 링크는 `fresh`, `moved`, `changed`(변경한 커밋을 함께 표시), `unresolved`, `missing` 중 하나로 표시됩니다. 링크는 정의의 토큰을 기록하므로, 공백, 주석, 대부분의 포매터 변경은 `fresh` 상태를 유지합니다.
- **오래된 메모리를 원하는 방식으로 처리.** 네임스페이스의 `code_change_policy`에 따라, 연결된 코드가 모두 사라진 메모리를 검색에서 제외하거나(`retire_removed`, 기본값), 표시만 하거나(`flag`), 모델이 이전 코드와 새 코드를 기준으로 주장을 확인해 수정안을 제안하게 할 수 있습니다(`revise`). Loom이 메모리 자체를 다시 쓰는 일은 없습니다.
- **에이전트가 코드를 읽는 곳에 교훈 표시.** `code.symbol`은 정의에 연결된 메모리를 링크 상태와 함께 `lessons`로 반환하고, `memory.for_file`은 파일의 코드에 연결된 메모리를 제공하며, Claude Code 플러그인은 해당 파일을 편집하기 전에 이를 보여 줍니다. [`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Claude Code 플러그인](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **브랜치는 자신의 코드를 읽음.** 이전 텍스트가 그대로 남아 있는 릴리스 브랜치에서는, trunk가 해당 코드를 바꾼 뒤에도 교훈이 `fresh`로 표시됩니다.
- **코드 옆의 문서.** 리포지토리의 trunk 코드 네임스페이스에 등록된 소스의 문서에는 리포지토리 태그가 붙으므로, 리포지토리를 대상으로 한 검색이 코드와 함께 문서도 읽습니다(동기화 워커에는 `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`가 필요합니다).
  [문서 소스](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **검토 목록.** `namespace.validate`는 코드가 바뀌었거나 사라진 메모리를 나열하고, 대시보드는 각 메모리의 링크와 상태를 보여 줍니다.

<a id="start"></a>

## 시작하기

[빠른 시작](../getting-started/quickstart.md)에서 Loom을 부팅하고,
[리포지토리 추가](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)에서 리포지토리의 코드 인덱스를 켭니다.
플러그인이나 MCP로 코딩 에이전트를 연결하고([코딩 에이전트 연결](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)), 백틱으로 정의를 언급하는 교훈을 저장하세요. trunk가 인덱싱되고 다음 링크 처리(기본값은 60초마다)가 실행되면, 해당 정의에 대한 `code.symbol`이 그 교훈을 `lessons`로 반환합니다.
