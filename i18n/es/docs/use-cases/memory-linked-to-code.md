<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=bc08f5ccede7493df24bf88e34857dcfc36ff446 model=claude-sonnet-5-5 -->
> Esta página es una traducción automática de la [página en inglés](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), que prevalece.
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# Memoria vinculada al código para agentes de programación

Loom vincula las memorias de los agentes de programación con las funciones, clases y archivos que nombran, las sigue a través de las refactorizaciones y marca una memoria cuando su código cambia. Por defecto, la búsqueda deja fuera una memoria cuyo código ha desaparecido por completo, salvo que una persona la haya fijado, bloqueado o verificado, y toda lectura que sirve una lección indica cuándo cambió su código o si ha desaparecido. Funciona sobre MCP, HTTP y los SDK en los repositorios que indexa un Loom autoalojado (el [índice de código](code-intelligence.md), `LOOM_CODE_INGEST_ENABLED=true`), y el worker `memory_code_links` que mantiene los vínculos está activado por defecto.

<a id="what-you-get"></a>

## Qué obtienes

- **Lecciones asociadas al código del que tratan.** Una memoria etiquetada `repo:<id>` (los plugins de Claude Code y Codex etiquetan lo que capturan sus hooks) o escrita en el espacio de nombres de código del repositorio se vincula con cada definición (función, clase u otro símbolo) que nombra entre comillas invertidas, o en cualquier parte de su texto como `A::b` o una llamada (`a.b()`, `name()`), que se resuelva en un único lugar; con las definiciones de los archivos que lista y que nombra con una palabra suelta en camelCase o snake_case; y con cada archivo que lista y que no contiene ninguna de ellas. Un nombre calificado que aún no está en trunk se vincula si se fusiona dentro de `LOOM_MEMORY_CODE_LINKS_PENDING_DAYS` (14 días).
  [Memorias vinculadas al código](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Vínculos que siguen las refactorizaciones.** En cada commit indexado, cada vínculo pasa a `fresh`, `moved`, `changed` (con el commit que lo cambió), `unresolved` o `missing`. Un vínculo registra los tokens de la definición, de modo que los espacios en blanco, los comentarios y la mayoría de los cambios de formateador lo dejan en `fresh`. [Estados de los vínculos](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Memorias obsoletas tratadas como tú elijas.** La política
  [`code_change_policy`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespaceregister) del espacio de nombres lo decide:
  `retire_removed` (la predeterminada) deja fuera de la búsqueda una memoria cuyo código ha desaparecido por completo y marca el código que cambió; `flag` solo marca; `revise` también deja fuera las memorias cuyo código ha desaparecido, y hace que el modelo contraste la afirmación de una memoria cambiada con el código antiguo y el nuevo y sugiera una afirmación corregida. Una memoria que una persona fijó, bloqueó o verificó nunca se deja fuera. Con cualquier política, la búsqueda suele colocar una memoria cuyo código cambió por debajo de los tres resultados siguientes en lugar de ocultarla; una memoria retenida y una fila encontrada por nombre conservan su posición. Loom nunca reescribe la memoria en sí.
  [Políticas](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Lecciones dondequiera que el agente lea código.** `code.symbol` devuelve como `lessons` las memorias vinculadas a una definición; `code.search`, `code.callers`, `code.callees`, `code.impact`, `code.outline` y el `code.context_pack` de una tarea adjuntan las lecciones de los archivos que devuelven; cada lección lleva el estado de su vínculo. Los hooks de Claude Code y Codex muestran las memorias de un archivo (`memory.for_file`) antes de que un agente lo edite, y los hooks de Cline cuando lo lee o lo edita. [`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [plugin de Claude Code](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md) ·
  [plugin de Codex](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/codex.md) ·
  [hooks de Cline](https://github.com/ClickHouse/loom/blob/main/integrations/cline/README.md) (experimental)
- **Compartidas con el equipo.** Las lecciones se leen del espacio de nombres de código del repositorio y de los espacios de nombres que nombra la llamada (por MCP, el propio del llamante por defecto), de modo que una lección guardada en el espacio de nombres de código del repositorio llega a todo agente que pueda leer ese espacio de nombres (un token restringido a su propio usuario o sesión no lee lecciones).
  [Memorias vinculadas al código](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Cada rama lee su propio código.** En otra rama, una lección se comprueba contra la definición de esa rama, así que una rama de release que aún conserva el texto antiguo la lee como `fresh`.
  [Memorias vinculadas al código](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Documentos junto al código.** Los documentos de una fuente registrada en el espacio de nombres de código del repositorio (el de su trunk) llevan la etiqueta del repositorio, de modo que las búsquedas sobre el repositorio los leen junto con su código (el worker de sincronización necesita `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`).
  [Fuentes de documentos](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **Una lista de revisión.**
  [`namespace.validate`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/namespace.md#namespacevalidate) lista las memorias cuyo código cambió, no se volvió a encontrar o ha desaparecido, y el dashboard muestra los vínculos de cada memoria y su estado.

![Detalle de una memoria en el dashboard: su código vinculado, una definición actual y otra cambiada desde un commit](https://github.com/ClickHouse/loom/raw/main/docs/assets/screenshots/memory-linked-code.webp)

<a id="start"></a>

## Primeros pasos

La [guía de inicio rápido](../getting-started/quickstart.md) arranca Loom, y
[añadir repositorios](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md) activa el índice de código para un repositorio. Conecta tu agente de programación con su plugin o por MCP
([conectar un agente de programación](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)). Guarda una lección que nombre entre comillas invertidas una definición de cuatro o más caracteres, o una calificada como `` `Reader.run` ``. Los plugins de Claude Code y Codex etiquetan lo que capturan sus hooks con `repo:<id>`, y el primer mensaje de la sesión nombra el id
([cómo se deriva](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md#configuration)). Una lección que el agente guarda por sí mismo con `loom_memory_set` se vincula cuando lleva esa etiqueta (`tags: ["repo:<id>"]`) o se escribe en el espacio de nombres de código del repositorio; cualquier cliente puede hacer una u otra cosa. Por HTTP (o `agent.call` en el SDK de Python), una lección guardada en tu propio espacio de nombres solo se lee cuando la llamada nombra ese espacio de nombres en `lesson_namespaces`. Cada pasada de vinculación vincula las escrituras con al menos 60 segundos de antigüedad y las pasadas se ejecutan cada 60 segundos, de modo que, una vez indexado trunk, `code.symbol` sobre esa definición devuelve la lección en `lessons` en unos dos minutos.

Algunas operaciones de esta página están marcadas como experimentales en la [página de estado](https://github.com/ClickHouse/loom/blob/main/docs/reference/status.md).
