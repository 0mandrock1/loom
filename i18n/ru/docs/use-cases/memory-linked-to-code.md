<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=b9e9c3f0bf6d83928302d688a3f5c2bb304521db model=claude-sonnet-5-5 -->
> Эта страница — машинный перевод [английской страницы](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md); при расхождениях верна английская версия.
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# Память, связанная с кодом, для агентов программирования

Loom связывает воспоминания, которые агенты программирования сохраняют о репозитории, с функциями, классами
и файлами, которые в них названы, отслеживает эти символы при рефакторингах и помечает память, когда её
код меняется. По умолчанию память, весь код которой исчез, пропадает из результатов поиска, а любое
другое чтение, которое всё ещё показывает урок, сообщает, переместился ли его код, изменился или исчез.
Это работает через MCP в репозиториях, которые индексирует Loom на собственном сервере (
[индекс кода](code-intelligence.md), `LOOM_CODE_INGEST_ENABLED=true`), а воркер
`memory_code_links`, который поддерживает связи, включён по умолчанию.

<a id="what-you-get"></a>

## Что вы получаете

- **Уроки, привязанные к коду, о котором они.** Память с тегом `repo:<id>` (плагины
  Claude Code и Codex ставят этот тег на то, что фиксируют их хуки) или записанная в пространство имён кода
  репозитория связывается с каждым определением, которое названо в ней в обратных кавычках, как
  `A::b` или как вызов (`a.b()`), если оно разрешается ровно в одно место; с определениями в
  перечисленных в ней файлах, которые названы просто словом в camelCase или snake_case; и с каждым названным
  в ней файлом, в котором таких определений нет. Квалифицированное имя, которого ещё нет в основной ветке, связывается, если оно
  попадает в неё в течение `memory_code_links_pending_days` (14).
  [Память, связанная с кодом](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Связи, которые следуют за рефакторингами.** На каждом проиндексированном коммите каждая связь имеет состояние `fresh`,
  `moved`, `changed` (с указанием коммита, который её изменил), `unresolved` или `missing`. Связь
  хранит токены определения, поэтому пробелы, комментарии и большинство изменений форматтера
  оставляют её в состоянии `fresh`. [Состояния связей](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Устаревшая память обрабатывается так, как вы выберете.** Решает
  [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) пространства имён:
  `retire_removed` (по умолчанию) убирает из поиска память, весь код которой исчез,
  и помечает изменённый код; `flag` только помечает; `revise` тоже убирает память с исчезнувшим кодом
  и поручает модели сверить утверждение изменённой памяти со старым и новым кодом и предложить
  исправленное утверждение. При любой политике поиск ранжирует память с изменившимся кодом ниже
  следующих трёх результатов, а не скрывает её. Loom никогда не переписывает саму память.
- **Уроки везде, где агент читает код.** `code.symbol` возвращает воспоминания, связанные с
  определением, как `lessons`; `code.search`, `code.callers`, `code.callees`, `code.impact`,
  `code.outline` и `code.context_pack` прикладывают уроки для файлов, которые возвращают;
  каждый урок содержит состояние своей связи. Хуки Claude Code и Codex показывают память
  о файле (`memory.for_file`) до того, как агент его изменит, а хуки Cline — когда он читает
  или изменяет файл. [`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [плагин Claude Code](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **Общий доступ для команды.** Уроки читаются из пространства имён кода репозитория и
  пространств имён, названных в вызове (по MCP по умолчанию это собственное пространство вызывающего), поэтому урок, сохранённый в
  пространстве имён кода репозитория, доходит до каждого агента, который читает этот код.
  [Память, связанная с кодом](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Ветки читают собственный код.** В другой ветке урок сверяется с определением
  этой ветки, поэтому релизная ветка, в которой всё ещё старый текст, показывает `fresh`.
  [Память, связанная с кодом](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Документы рядом с кодом.** Документы из источника, зарегистрированного в пространстве имён кода репозитория
  (его основной ветки), получают тег репозитория, поэтому поиск по репозиторию
  читает их вместе с кодом (воркеру синхронизации нужен `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`).
  [Источники документов](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **Список для проверки.**
  [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) перечисляет
  воспоминания, код которых изменился или исчез, а панель управления показывает связи каждой памяти и
  их состояние.

![Подробности памяти в панели управления: связанный с ней код, одно определение актуально, другое изменено с некоторого коммита](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## Начало работы

[Краткое руководство](../getting-started/quickstart.md) запускает Loom, а
[добавление репозиториев](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md) включает индекс кода для
репозитория. Подключите своего агента программирования через плагин или по MCP
([подключение агента программирования](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)). Сохраните урок, который
называет в обратных кавычках определение из четырёх или более символов, или квалифицированное, например
`` `Reader.run` ``. Плагины Claude Code и Codex добавляют тег `repo:<id>` за вас
([как выводится id](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)); из любого
другого клиента записывайте урок в пространство имён кода репозитория. Каждый проход связывания обрабатывает
записи не младше 60 секунд, а проходы запускаются каждые 60 секунд, поэтому после индексации основной ветки
`code.symbol` для этого определения вернёт урок в `lessons` примерно через две
минуты.
