<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=020733a9f887152df7c768465027827db57128ba model=claude-sonnet-5-5 -->
> 이 페이지는 [영어 원문](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)을 기계 번역한 것이며, 영어 원문이 기준입니다.
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# 코딩 에이전트를 위한 코드 연결 메모리

Loom은 코딩 에이전트의 메모리를 그 메모리가 언급하는 함수, 클래스, 파일에 연결하고, 리팩터링 이후에도 연결을 따라가며, 해당 코드가 바뀌면 메모리에 표시를 남깁니다. 기본적으로 검색은 코드가 모두 사라진 메모리를 결과에서 제외하며, 교훈(lesson)을 반환하는 모든 읽기는 그 코드가 이동했는지, 변경됐는지, 사라졌는지를 함께 알려 줍니다. 이 기능은 셀프 호스팅 Loom이 인덱싱한 저장소([코드 인덱스](code-intelligence.md), `LOOM_CODE_INGEST_ENABLED=true`)에 대해 MCP, HTTP, SDK를 통해 동작하며, 연결을 유지하는 `memory_code_links` 워커는 기본으로 켜져 있습니다.

<a id="what-you-get"></a>

## 제공하는 기능

- **교훈을 해당 코드에 연결.** `repo:<id>` 태그가 붙은 메모리(Claude Code와 Codex 플러그인은 훅이 기록한 내용에 이 태그를 붙입니다)나 저장소의 코드 네임스페이스에 작성된 메모리는 다음에 연결됩니다. 백틱으로 언급했거나 본문 어디에서든 `A::b` 또는 호출(`a.b()`, `name()`) 형태로 언급해 정확히 한 곳으로 확정되는 각 정의, 메모리가 나열한 파일 안에서 camelCase나 snake_case 단어 그대로 언급한 정의, 그리고 나열된 파일 중 이런 정의가 하나도 없는 각 파일입니다. 아직 trunk에 없는 정규화된 이름은 `LOOM_MEMORY_CODE_LINKS_PENDING_DAYS`(14)일 안에 병합되면 연결됩니다.
  [코드에 연결된 메모리](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **리팩터링을 따라가는 연결.** 인덱싱된 모든 커밋마다 각 연결은 `fresh`, `moved`, `changed`(변경한 커밋을 명시), `unresolved`, `missing` 중 하나로 표시됩니다. 연결은 정의의 토큰을 기록하므로 공백, 주석, 대부분의 포매터 변경은 `fresh` 상태를 유지합니다. [연결 상태](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **오래된 메모리를 원하는 방식으로 처리.** 네임스페이스의 [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister)가 결정합니다. `retire_removed`(기본값)는 코드가 모두 사라진 메모리를 검색에서 제외하고 변경된 코드에는 표시를 남깁니다. `flag`는 표시만 합니다. `revise`는 코드가 사라진 메모리도 제외하며, 모델이 변경된 메모리의 주장을 이전 코드와 새 코드에 대조해 확인하고 수정된 주장을 제안하게 합니다. 어떤 정책에서든 검색은 코드가 변경된 메모리를 숨기지 않고 다음 세 개의 결과보다 아래에 순위를 매깁니다. Loom은 메모리 자체를 다시 쓰지 않습니다.
- **에이전트가 코드를 읽는 모든 곳에서 교훈 제공.** `code.symbol`은 정의에 연결된 메모리를 `lessons`로 반환합니다. `code.search`, `code.callers`, `code.callees`, `code.impact`, `code.outline`, 그리고 작업의 `code.context_pack`은 반환하는 파일의 교훈을 첨부하며, 각 교훈에는 연결 상태가 포함됩니다. Claude Code와 Codex 훅은 에이전트가 파일을 편집하기 전에 해당 파일의 메모리(`memory.for_file`)를 보여 주고, Cline 훅은 파일을 읽거나 편집할 때 보여 줍니다. [`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Claude Code 플러그인](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **팀과 공유.** 교훈은 저장소의 코드 네임스페이스와 호출이 지정한 네임스페이스(MCP에서는 기본적으로 호출자 본인의 네임스페이스)에서 읽습니다. 따라서 저장소의 코드 네임스페이스에 저장된 교훈은 해당 코드를 읽는 모든 에이전트에게 전달됩니다.
  [코드에 연결된 메모리](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **브랜치별로 자기 코드를 읽음.** 다른 브랜치에서는 교훈을 그 브랜치의 정의와 대조해 확인하므로, 이전 텍스트를 그대로 가진 릴리스 브랜치에서는 `fresh`로 표시됩니다.
  [코드에 연결된 메모리](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **코드 옆의 문서.** 저장소의 코드 네임스페이스(trunk의 네임스페이스)에 등록된 소스의 문서에는 저장소 태그가 붙으므로, 저장소를 대상으로 한 검색이 코드와 함께 문서도 읽습니다(동기화 워커에는 `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`가 필요합니다).
  [문서 소스](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **검토 목록.** [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate)는 코드가 변경됐거나, 다시 찾지 못했거나, 사라진 메모리를 나열하며, 대시보드는 각 메모리의 연결과 그 상태를 보여 줍니다.

![대시보드의 메모리 상세 화면: 연결된 코드, 최신 상태인 정의 하나와 특정 커밋 이후 변경된 정의 하나](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## 시작하기

[빠른 시작](../getting-started/quickstart.md)에서 Loom을 부팅하고, [저장소 추가](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)로 저장소에 대한 코드 인덱스를 켭니다. 플러그인이나 MCP로 코딩 에이전트를 연결합니다([코딩 에이전트 연결](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)). 네 글자 이상의 정의를 백틱으로 언급하거나 `` `Reader.run` ``처럼 정규화된 이름으로 언급한 교훈을 저장합니다. Claude Code와 Codex 플러그인은 훅이 기록한 내용에 `repo:<id>` 태그를 붙이며, 세션 프라이머에 해당 id가 표시됩니다([id 도출 방식](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)). 에이전트가 `loom_memory_set`으로 직접 저장한 교훈은 그 태그(`tags: ["repo:<id>"]`)를 갖고 있거나 저장소의 코드 네임스페이스에 작성되면 연결되며, 어떤 클라이언트든 둘 중 어느 쪽이든 할 수 있습니다. HTTP나 SDK에서는 본인 네임스페이스에 저장한 교훈을 호출이 `lesson_namespaces`에서 해당 네임스페이스를 지정할 때만 읽습니다. 각 연결 패스는 작성된 지 60초 이상 지난 쓰기를 연결하고 패스는 60초마다 실행되므로, trunk가 인덱싱되고 나면 해당 정의에 대한 `code.symbol`은 약 2분 안에 `lessons`에서 교훈을 반환합니다.

이 페이지의 일부 op는 [상태 페이지](https://github.com/ClickHouse/loom/blob/main/docs/reference/status.md)에서 실험적(experimental)으로 표시되어 있습니다.
