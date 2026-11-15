<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=b9e9c3f0bf6d83928302d688a3f5c2bb304521db model=claude-sonnet-5-5 -->
> Esta página es una traducción automática de la [página en inglés](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), que prevalece.
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# Memoria vinculada al código para agentes de programación

Loom vincula las memorias que los agentes de programación guardan sobre un repositorio con las funciones, clases y archivos que nombran, sigue esos símbolos a través de las refactorizaciones y marca una memoria cuando su código cambia. Por defecto, una memoria cuyo código ha desaparecido por completo deja de aparecer en los resultados de búsqueda, y cualquier otra lectura que aún muestre una lección indica si su código se movió, cambió o desapareció. Funciona sobre MCP en los repositorios que indexa un Loom autoalojado (el
[índice de código](code-intelligence.md), `LOOM_CODE_INGEST_ENABLED=true`), y el worker `memory_code_links`, que mantiene los vínculos, está activado por defecto.

<a id="what-you-get"></a>

## Qué obtienes

- **Lecciones adjuntas al código del que tratan.** Una memoria etiquetada con `repo:<id>` (los plugins de Claude Code y Codex etiquetan lo que capturan sus hooks) o escrita en el espacio de nombres de código del repositorio se vincula con cada definición que nombra entre acentos graves, como `A::b` o como una llamada (`a.b()`), que se resuelve en un único lugar; con las definiciones de los archivos que lista y que nombra con una palabra suelta en camelCase o snake_case; y con cada archivo que nombra y que no contiene ninguna de ellas. Un nombre calificado que aún no está en la rama principal se vincula si se fusiona dentro de `memory_code_links_pending_days` (14).
  [Memorias vinculadas al código](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Vínculos que siguen las refactorizaciones.** En cada commit indexado, cada vínculo figura como `fresh`, `moved`, `changed` (con el commit que lo cambió), `unresolved` o `missing`. Un vínculo registra los tokens de la definición, de modo que los espacios en blanco, los comentarios y la mayoría de los cambios de formateadores lo dejan en `fresh`. [Estados de los vínculos](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Memorias obsoletas tratadas como tú elijas.** La
  [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) del espacio de nombres lo decide: `retire_removed` (el valor por defecto) deja fuera de la búsqueda una memoria cuyo código ha desaparecido por completo y marca el código modificado; `flag` solo marca; `revise` también deja fuera las memorias con código desaparecido y hace que el modelo compare la afirmación de una memoria modificada con el código antiguo y el nuevo y sugiera una afirmación corregida. Con cualquier política, la búsqueda coloca una memoria cuyo código cambió por debajo de los tres resultados siguientes en lugar de ocultarla. Loom nunca reescribe la memoria en sí.
- **Lecciones allí donde el agente lee código.** `code.symbol` devuelve como `lessons` las memorias vinculadas a una definición; `code.search`, `code.callers`, `code.callees`, `code.impact`, `code.outline` y `code.context_pack` adjuntan las lecciones de los archivos que devuelven; cada lección incluye el estado de su vínculo. Los hooks de Claude Code y Codex muestran las memorias de un archivo (`memory.for_file`) antes de que un agente lo edite, y los hooks de Cline lo hacen cuando lo lee o lo edita. [`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Plugin de Claude Code](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **Compartido con el equipo.** Las lecciones se leen del espacio de nombres de código del repositorio y de los espacios de nombres que indica la llamada (por MCP, el del propio llamador por defecto), de modo que una lección guardada en el espacio de nombres de código del repositorio llega a todos los agentes que leen ese código.
  [Memorias vinculadas al código](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Las ramas leen su propio código.** En otra rama, una lección se comprueba contra la definición de esa misma rama, así que una rama de release que aún conserva el texto antiguo figura como `fresh`.
  [Memorias vinculadas al código](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Documentos junto al código.** Los documentos de una fuente registrada en el espacio de nombres de código del repositorio (el de su rama principal) llevan la etiqueta del repositorio, de modo que las búsquedas sobre el repositorio los leen junto con su código (el worker de sincronización necesita `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`).
  [Fuentes de documentos](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **Una lista de revisión.**
  [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) lista las memorias cuyo código cambió o desapareció, y el dashboard muestra los vínculos de cada memoria y su estado.

![Detalle de una memoria en el dashboard: su código vinculado, una definición vigente y otra modificada desde un commit](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## Primeros pasos

La [Guía de inicio rápido](../getting-started/quickstart.md) arranca Loom, y
[añadir repositorios](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md) activa el índice de código para un repositorio. Conecta tu agente de programación con su plugin o por MCP
([conectar un agente de programación](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)). Guarda una lección que nombre entre acentos graves una definición de cuatro o más caracteres, o una calificada como `` `Reader.run` ``. Los plugins de Claude Code y Codex añaden por ti la etiqueta `repo:<id>`
([cómo se deriva el id](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)); desde cualquier otro cliente, escribe la lección en el espacio de nombres de código del repositorio. Cada pasada de vinculación procesa las escrituras con al menos 60 segundos de antigüedad y las pasadas se ejecutan cada 60 segundos, así que, una vez indexada la rama principal, `code.symbol` sobre esa definición devuelve la lección en `lessons` en unos dos minutos.
