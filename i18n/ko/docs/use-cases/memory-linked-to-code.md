<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=b1cc9eef83359902a76126407096411d372e2f5e model=claude-sonnet-5-5 -->
> 이 페이지는 [영어 원문](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md)을 기계 번역한 것이며, 영어 원문이 기준입니다.
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# 코딩 에이전트를 위한 코드 연결 메모리

Loom은 코딩 에이전트의 메모리를 에이전트가 언급한 함수, 클래스, 파일에 연결하고, 리팩터링 후에도 연결을 따라가며, 메모리가 가리키는 코드가 바뀌면 해당 메모리에 표시를 남깁니다. 기본적으로 검색은 연결된 코드가 모두 사라진 메모리를 결과에서 제외합니다. 단, 사람이 고정(pin), 잠금(lock) 또는 검증(verify)한 메모리는 제외하지 않습니다. 교훈을 제공하는 모든 읽기 응답에는 해당 코드가 언제 바뀌었는지, 또는 사라졌는지가 함께 표시됩니다. 이 기능은 자체 호스팅 Loom이 인덱싱한 저장소에서 MCP, HTTP, SDK를 통해 동작하며
([코드 인덱스](code-intelligence.md), `LOOM_CODE_INGEST_ENABLED=true`), 연결을 유지하는 `memory_code_links` 워커는 기본적으로 켜져 있습니다.

<a id="what-you-get"></a>

## 제공 기능

- **교훈을 해당 코드에 연결.** `repo:<id>` 태그가 붙은 메모리(Claude Code와 Codex 플러그인은 훅이 기록하는 내용에 이 태그를 붙입니다)나 저장소의 코드 네임스페이스에 작성된 메모리는 다음과 같이 연결됩니다. 백틱으로 언급했거나, 본문 어디에서든 `A::b` 또는 호출(`a.b()`, `name()`) 형태로 언급해 정확히 한 곳으로 확정되는 각 정의(함수, 클래스 또는 기타 심볼)에 연결됩니다. 메모리에 나열된 파일 안의 정의 중 camelCase 또는 snake_case 단어 하나만으로 언급된 것에도 연결됩니다. 나열된 파일 중 위 정의를 하나도 포함하지 않은 파일에는 파일 단위로 연결됩니다. 아직 trunk에 없는 정규화된 이름은 `LOOM_MEMORY_CODE_LINKS_PENDING_DAYS`(14일) 이내에 병합되면 연결됩니다.
  [코드에 연결된 메모리](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **리팩터링을 따라가는 연결.** 인덱싱된 커밋마다 각 연결은 `fresh`, `moved`, `changed`(변경한 커밋을 명시), `unresolved`, `missing` 중 하나로 표시됩니다. 연결은 정의의 토큰을 기록하므로 공백, 주석, 대부분의 포매터 변경에는 `fresh` 상태가 유지됩니다. [연결 상태](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **오래된 메모리를 원하는 방식으로 처리.** 네임스페이스의 [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister)가 결정합니다. `retire_removed`(기본값)는 코드가 모두 사라진 메모리를 검색에서 제외하고 변경된 코드에는 표시를 남깁니다. `flag`는 표시만 남깁니다. `revise`는 코드가 사라진 메모리를 마찬가지로 제외하고, 변경된 메모리에 대해서는 모델이 이전 코드와 새 코드를 기준으로 메모리의 주장을 확인한 뒤 수정된 주장을 제안하게 합니다. 사람이 고정, 잠금 또는 검증한 메모리는 절대 제외되지 않습니다. 어떤 정책에서든 검색은 코드가 변경된 메모리를 숨기지 않고 보통 다음 세 개의 결과 아래로 순위를 낮춥니다. 보류된 메모리와 이름으로 찾은 행은 순위가 유지됩니다. Loom은 메모리 자체를 다시 쓰지 않습니다.
  [정책](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **에이전트가 코드를 읽는 모든 곳에서 교훈 제공.** `code.symbol`은 정의에 연결된 메모리를 `lessons`로 반환합니다. `code.search`, `code.callers`, `code.callees`, `code.impact`, `code.outline`, 그리고 작업의 `code.context_pack`은 반환하는 파일에 대한 교훈을 첨부하며, 각 교훈에는 연결 상태가 포함됩니다. Claude Code와 Codex 훅은 에이전트가 파일을 편집하기 전에 그 파일의 메모리(`memory.for_file`)를 보여 주고, Cline 훅은 파일을 읽거나 편집할 때 보여 줍니다. [`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Claude Code 플러그인](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md) ·
  [Codex 플러그인](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/codex.md)
- **팀과 공유.** 교훈은 저장소의 코드 네임스페이스와 호출에서 지정한 네임스페이스에서 읽습니다(MCP에서는 기본적으로 호출자 본인의 네임스페이스). 따라서 저장소의 코드 네임스페이스에 저장한 교훈은 해당 네임스페이스를 읽을 수 있는 모든 에이전트에 전달됩니다(자신의 사용자 또는 세션으로 범위가 좁혀진 토큰은 교훈을 읽지 못합니다).
  [코드에 연결된 메모리](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **브랜치별로 해당 브랜치의 코드를 읽음.** 다른 브랜치에서는 교훈이 그 브랜치 자체의 정의를 기준으로 확인되므로, 이전 코드가 그대로 남아 있는 릴리스 브랜치에서는 `fresh`로 표시됩니다.
  [코드에 연결된 메모리](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **코드 옆의 문서.** 저장소의 코드 네임스페이스(trunk의 네임스페이스)에 등록된 소스의 문서에는 저장소 태그가 붙으므로, 저장소에 대한 검색에서 코드와 함께 읽힙니다(동기화 워커에는 `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`가 필요합니다).
  [문서 소스](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **검토 목록.**
  [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate)는 코드가 변경되었거나, 다시 찾지 못했거나, 사라진 메모리를 나열하며, 대시보드는 각 메모리의 연결과 그 상태를 보여 줍니다.

![대시보드의 메모리 상세 화면: 연결된 코드 중 하나는 최신 상태이고 하나는 특정 커밋 이후 변경됨](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## 시작하기

[빠른 시작](../getting-started/quickstart.md)에서 Loom을 부팅하고, [저장소 추가](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md)에서 저장소의 코드 인덱스를 켭니다. 플러그인 또는 MCP로 코딩 에이전트를 연결합니다([코딩 에이전트 연결](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)). 네 글자 이상의 정의 이름을 백틱으로 감싸거나 `` `Reader.run` ``처럼 정규화된 이름으로 언급한 교훈을 저장합니다. Claude Code와 Codex 플러그인은 훅이 기록하는 내용에 `repo:<id>` 태그를 붙이며, 세션 프라이머에 해당 id가 표시됩니다([id 도출 방식](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)). 에이전트가 `loom_memory_set`으로 직접 저장한 교훈은 이 태그(`tags: ["repo:<id>"]`)를 갖고 있거나 저장소의 코드 네임스페이스에 작성되면 연결되며, 어떤 클라이언트든 둘 중 하나를 할 수 있습니다. HTTP(또는 Python SDK의 `agent.call`)에서는 본인 네임스페이스에 저장한 교훈이 호출에서 `lesson_namespaces`로 그 네임스페이스를 지정한 경우에만 읽힙니다. 연결 패스는 기록된 지 60초 이상 지난 쓰기를 연결하고 60초마다 실행되므로, trunk가 인덱싱되면 해당 정의에 대한 `code.symbol`이 약 2분 안에 `lessons`로 그 교훈을 반환합니다.

`code.callees`와 `code.outline`은 실험적 기능이며([상태](https://github.com/ClickHouse/loom/blob/main/docs/reference/status.md)), 이 페이지의 나머지는 안정 기능입니다.
