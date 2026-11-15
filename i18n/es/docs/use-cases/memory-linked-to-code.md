<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=ba0db8ea1cf39d2ef1aa0c782706183107d553a7 model=claude-sonnet-5-5 -->
> Esta página es una traducción automática de la [página en inglés](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), que prevalece.
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# Memoria vinculada al código para agentes de programación

Loom vincula las memorias que los agentes de programación guardan sobre un repositorio con las funciones, clases y archivos que nombran, sigue esos símbolos a través de las refactorizaciones y marca una memoria cuando su código cambia. De forma predeterminada, una memoria cuyo código ha desaparecido por completo deja de aparecer en la búsqueda, de modo que las lecciones obsoletas dejan de llegar a los agentes. Funciona sobre los repositorios que Loom indexa (el
[índice de código](code-intelligence.md), `LOOM_CODE_INGEST_ENABLED=true`), y el worker `memory_code_links`, que mantiene los vínculos, está activado de forma predeterminada.

<a id="what-you-get"></a>

## Qué obtienes

- **Lecciones asociadas al código del que tratan.** Una memoria etiquetada con `repo:<id>` (los plugins de Claude Code y Codex etiquetan lo que capturan sus hooks) o escrita en el espacio de nombres de código de un repositorio se vincula con cada definición que nombra entre acentos graves y que se resuelve en un único lugar, con las definiciones de los archivos que enumera y que nombra con una palabra simple en camelCase o snake_case, y con cada archivo que nombra y que no contiene ninguna de ellas. Un nombre calificado que aún no está en trunk se vincula si se fusiona dentro de `memory_code_links_pending_days` (14).
  [Memorias vinculadas al código](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Vínculos que siguen las refactorizaciones.** En cada commit indexado, cada vínculo figura como `fresh`, `moved`, `changed` (indicando el commit que lo cambió), `unresolved` o `missing`. Un vínculo registra los tokens de la definición, por lo que los espacios en blanco, los comentarios y la mayoría de los cambios de formateo lo dejan en `fresh`.
- **Memorias obsoletas tratadas como tú decidas.** Lo determina la
  [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md) del espacio de nombres: `retire_removed` (el valor predeterminado) deja fuera de la búsqueda una memoria cuyo código ha desaparecido por completo y marca el código modificado; `flag` solo marca; `revise` también deja fuera las memorias cuyo código ha desaparecido, y hace que el modelo contraste la afirmación de una memoria modificada con el código antiguo y el nuevo y sugiera una afirmación corregida. Con cualquier política, la búsqueda coloca una memoria cuyo código cambió por debajo de los tres resultados siguientes en lugar de ocultarla. Loom nunca reescribe la memoria en sí.
- **Lecciones allí donde el agente lee código.** `code.symbol` devuelve como `lessons` las memorias vinculadas a una definición; `code.search`, `code.callers`, `code.callees`, `code.impact`, `code.outline` y `code.context_pack` adjuntan las lecciones de los archivos que devuelven; cada lección incluye el estado de su vínculo. Los hooks de Claude Code y Codex muestran las memorias de un archivo (`memory.for_file`) antes de que un agente lo edite, y los hooks de Cline lo hacen cuando lo lee o lo edita.
  [`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Plugin de Claude Code](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **Compartidas con el equipo.** Las lecciones se leen del espacio de nombres de equipo del repositorio y, de forma predeterminada, del propio del solicitante, de modo que una lección que un agente guarda allí llega a todos los agentes que leen ese código.
- **Las ramas leen su propio código.** En otra rama, una lección se comprueba con la definición de esa misma rama, así que una rama de versión que aún conserva el texto antiguo figura como `fresh`.
- **Documentos junto al código.** Los documentos de una fuente registrada en el espacio de nombres de código de trunk de un repositorio llevan la etiqueta del repositorio, de modo que las búsquedas sobre el repositorio los leen junto con su código (el worker de sincronización necesita `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`).
  [Fuentes de documentos](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **Una lista de revisión.**
  [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) enumera las memorias cuyo código cambió o ha desaparecido, y el dashboard muestra los vínculos de cada memoria y su estado.

![Detalle de una memoria en el dashboard: su código vinculado, una definición actual y otra modificada desde un commit](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## Primeros pasos

La [guía de inicio rápido](../getting-started/quickstart.md) pone en marcha Loom, y
[añadir repositorios](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md) activa el índice de código para un repositorio. Conecta tu agente de programación con su plugin o mediante MCP
([conectar un agente de programación](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)). Guarda una lección que nombre entre acentos graves una definición de cuatro o más caracteres (o una calificada, como
`` `Reader.run` ``), etiquetada con `repo:<id>` o escrita en el espacio de nombres de código del repositorio. Una vez indexado trunk y ejecutada una pasada de vinculación un minuto después de guardar (las pasadas se ejecutan cada 60 segundos y van 60 segundos por detrás de la escritura más reciente, así que en unos dos minutos), `code.symbol` sobre esa definición devuelve la lección en `lessons`.
