<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=248230780bfc4a7af42bcb776c9574c89ec21839 model=claude-sonnet-5-5 -->
> Эта страница — машинный перевод [английской страницы](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md); при расхождениях верна английская версия.
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# Память, связанная с кодом, для агентов-программистов

Loom связывает воспоминания, которые агенты-программисты сохраняют о репозитории, с функциями, классами
и файлами, которые в них названы, прослеживает эти символы через рефакторинги и помечает память, когда её
код меняется. По умолчанию память, весь код которой исчез, пропадает из результатов поиска, а любое
другое чтение, где всё ещё показан такой урок, сообщает, был ли его код перемещён, изменён или удалён. Это
работает через MCP, HTTP и SDK на репозиториях, которые индексирует самостоятельно развёрнутый Loom
([индекс кода](code-intelligence.md), `LOOM_CODE_INGEST_ENABLED=true`), а воркер
`memory_code_links`, который поддерживает связи, включён по умолчанию.

<a id="what-you-get"></a>

## Что вы получаете

- **Уроки, привязанные к коду, о котором они.** Память с тегом `repo:<id>`
  (плагины Claude Code и Codex ставят этот тег на то, что фиксируют их хуки) или записанная в
  пространстве имён кода репозитория связывается с каждым определением, которое она называет в обратных кавычках, как
  `A::b` или как вызов (`a.b()`) и которое разрешается ровно в одно место; с определениями в
  перечисленных в ней файлах, которые она называет простым словом в camelCase или snake_case; и с каждым
  перечисленным в ней файлом, где нет ни одного из них. Уточнённое имя, которого ещё нет в trunk, связывается,
  если оно попадёт туда в течение `LOOM_MEMORY_CODE_LINKS_PENDING_DAYS` (14).
  [Память, связанная с кодом](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Связи, которые следуют за рефакторингами.** На каждом проиндексированном коммите каждая связь получает состояние `fresh`,
  `moved`, `changed` (с указанием коммита, который её изменил), `unresolved` или `missing`. Связь
  запоминает токены определения, поэтому пробелы, комментарии и большинство изменений форматтера
  оставляют её в состоянии `fresh`. [Состояния связей](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Устаревшая память обрабатывается так, как выберете вы.** Решает
  [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) пространства имён:
  `retire_removed` (по умолчанию) убирает из поиска память, весь код которой исчез, и
  помечает изменённый код; `flag` только помечает; `revise` тоже убирает память с исчезнувшим кодом
  и поручает модели сверить утверждение изменённой памяти со старым и новым кодом и предложить
  исправленное утверждение. При любой политике поиск ранжирует память с изменённым кодом ниже
  следующих трёх результатов, а не скрывает её. Сам Loom саму память никогда не переписывает.
- **Уроки везде, где агент читает код.** `code.symbol` возвращает память, связанную с
  определением, как `lessons`; `code.search`, `code.callers`, `code.callees`, `code.impact`,
  `code.outline` и `code.context_pack` задачи прикладывают уроки для файлов, которые
  возвращают; каждый урок несёт состояние своей связи. Хуки Claude Code и Codex показывают
  память файла (`memory.for_file`) до того, как агент его изменит, а хуки Cline — когда
  он читает или изменяет файл. [`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [плагин Claude Code](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **Общий доступ для команды.** Уроки читаются из пространства имён кода репозитория и
  пространств имён, которые назвал вызов (по MCP по умолчанию — собственного пространства вызывающего), поэтому урок, сохранённый в
  пространстве имён кода репозитория, доходит до каждого агента, который читает этот код.
  [Память, связанная с кодом](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Ветки читают собственный код.** В другой ветке урок проверяется по определению
  из этой ветки, поэтому релизная ветка, где остался старый текст, получает состояние `fresh`.
  [Память, связанная с кодом](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Документы рядом с кодом.** Документы из источника, зарегистрированного в пространстве имён кода
  репозитория (в его trunk), несут тег репозитория, поэтому поиск по репозиторию
  читает их вместе с кодом (воркеру синхронизации нужен `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`).
  [Источники документов](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **Список для проверки.**
  [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) перечисляет
  память, код которой изменился, не был найден снова или исчез, а панель управления показывает
  связи каждой памяти и их состояние.

![Подробности памяти в панели управления: связанный с ней код, одно определение актуально, другое изменено с некоторого коммита](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## Начало работы

[Быстрый старт](../getting-started/quickstart.md) запускает Loom, а
[добавление репозиториев](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md) включает индекс кода для
репозитория. Подключите своего агента-программиста через его плагин или по MCP
([подключение агента-программиста](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)). Сохраните урок,
который называет в обратных кавычках определение из четырёх и более символов или уточнённое, например
`` `Reader.run` ``. Плагины Claude Code и Codex ставят на то, что фиксируют их хуки, тег
`repo:<id>`, а вводная сессии называет этот id
([как он выводится](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)). Урок, который
агент сохраняет сам через `loom_memory_set`, связывается, если несёт этот тег
(`tags: ["repo:<id>"]`) или записан в пространстве имён кода репозитория; любой клиент может
сделать и то и другое. Каждый проход связывания обрабатывает записи возрастом не менее 60 секунд, а проходы запускаются каждые 60
секунд, поэтому после индексации trunk `code.symbol` для этого определения возвращает урок
в `lessons` примерно через две минуты.
