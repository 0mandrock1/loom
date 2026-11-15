<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=e4bf1235c8a582b596af733bb2e5896ecc401751 model=claude-sonnet-5-5 -->
> Эта страница — машинный перевод [английской страницы](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md); при расхождениях верна английская версия.
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# Память, связанная с кодом, для кодинг-агентов

Loom связывает память кодинг-агентов с функциями, классами и файлами, которые в ней названы, отслеживает их при рефакторингах и помечает память, когда её код меняется. По умолчанию поиск не возвращает память, весь код которой исчез, а любое чтение, отдающее урок, сообщает, что его код изменился или исчез. Это работает через MCP, HTTP и SDK на репозиториях, которые индексирует self-hosted Loom
([индекс кода](code-intelligence.md), `LOOM_CODE_INGEST_ENABLED=true`), а воркер `memory_code_links`, который поддерживает связи, включён по умолчанию.

<a id="what-you-get"></a>

## Что вы получаете

- **Уроки, привязанные к коду, о котором они.** Память с тегом `repo:<id>`
  (плагины Claude Code и Codex ставят этот тег на то, что фиксируют их хуки) или записанная в пространстве имён кода репозитория связывается с каждым определением
  (функцией, классом или другим символом), которое она называет в обратных кавычках, или в любом месте текста в виде `A::b` либо вызова (`a.b()`, `name()`) и которое разрешается ровно в одно место; с определениями в перечисленных в ней файлах, которые она называет простым словом в camelCase или snake_case; и с каждым перечисленным в ней файлом, где нет ни одного из них. Квалифицированное имя, которого ещё нет в trunk, связывается, если оно попадёт в trunk в течение `LOOM_MEMORY_CODE_LINKS_PENDING_DAYS` (14 дней).
  [Память, связанная с кодом](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Связи, которые переживают рефакторинги.** На каждом проиндексированном коммите каждая связь получает состояние `fresh`, `moved`, `changed` (с указанием коммита, который её изменил), `unresolved` или `missing`. Связь запоминает токены определения, поэтому пробелы, комментарии и большинство изменений форматтера оставляют её в состоянии `fresh`. [Состояния связей](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Устаревшая память обрабатывается так, как вы выберете.** Это определяет
  [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) пространства имён:
  `retire_removed` (по умолчанию) убирает из поиска память, весь код которой исчез, и помечает изменившийся код; `flag` только помечает; `revise` тоже убирает память с исчезнувшим кодом, а для изменившейся просит модель сверить утверждение памяти со старым и новым кодом и предложить исправленное утверждение. При любой политике поиск ранжирует память с изменившимся кодом ниже следующих трёх результатов, а не скрывает её, если только её не удерживает человек.
  [Политики](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code) Loom никогда не переписывает саму память.
- **Уроки везде, где агент читает код.** `code.symbol` возвращает память, связанную с определением, как `lessons`; `code.search`, `code.callers`, `code.callees`, `code.impact`, `code.outline` и `code.context_pack` задачи прикладывают уроки для файлов, которые возвращают; каждый урок несёт состояние своей связи. Хуки Claude Code и Codex показывают память о файле (`memory.for_file`) до того, как агент его изменит, а хуки Cline — когда он читает или изменяет файл. [`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [плагин Claude Code](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md) ·
  [плагин Codex](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/codex.md)
- **Общий доступ для команды.** Уроки читаются из пространства имён кода репозитория и пространств имён, названных в вызове (через MCP по умолчанию это собственное пространство вызывающего), поэтому урок, сохранённый в пространстве имён кода репозитория, доходит до каждого агента, которому разрешено читать это пространство имён.
  [Память, связанная с кодом](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Ветки читают свой код.** В другой ветке урок проверяется по определению из этой ветки, поэтому релизная ветка, где остался старый текст, показывает `fresh`.
  [Память, связанная с кодом](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Документы рядом с кодом.** Документы из источника, зарегистрированного в пространстве имён кода репозитория (его trunk), получают тег репозитория, поэтому поиск по репозиторию читает их вместе с его кодом (воркеру синхронизации нужен `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`).
  [Источники документов](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **Список для проверки.**
  [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) перечисляет память, код которой изменился, не был найден снова или исчез, а панель управления показывает связи каждой памяти и их состояние.

![Подробности памяти в панели управления: связанный код, одно определение актуально, другое изменилось с некоторого коммита](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## Начало работы

[Быстрый старт](../getting-started/quickstart.md) запускает Loom, а
[добавление репозиториев](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md) включает индекс кода для репозитория. Подключите кодинг-агента через его плагин или по MCP
([подключение кодинг-агента](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)). Сохраните урок, который называет в обратных кавычках определение из четырёх или более символов либо квалифицированное, например `` `Reader.run` ``. Плагины Claude Code и Codex ставят на то, что фиксируют их хуки, тег `repo:<id>`, а id называется в праймере сессии
([как он выводится](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)). Урок, который агент сохраняет сам через `loom_memory_set`, связывается, если у него есть этот тег
(`tags: ["repo:<id>"]`) или он записан в пространстве имён кода репозитория; любой клиент может сделать и то и другое. Через HTTP (или `agent.call` в Python SDK) урок, сохранённый в вашем собственном пространстве имён, читается, только если вызов называет это пространство имён в `lesson_namespaces`. Каждый проход связывания обрабатывает записи старше 60 секунд, а проходы выполняются каждые 60 секунд, поэтому после индексации trunk `code.symbol` для этого определения вернёт урок в `lessons` примерно через две минуты.

`code.callees` и `code.outline` экспериментальные ([статус](https://github.com/ClickHouse/loom/blob/main/docs/reference/status.md)); остальное на этой странице стабильно.
