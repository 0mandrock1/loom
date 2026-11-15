<!-- i18n: source=docs/use-cases/memory-linked-to-code.md blob=9cbe1bbc3b8361789f5d82dc35b67597e77e8dbf model=claude-sonnet-5-5 -->
> Esta página es una traducción automática de la [página en inglés](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), que prevalece.
> Machine-translated from the [English page](https://github.com/ClickHouse/loom/blob/main/docs/use-cases/memory-linked-to-code.md), which is authoritative.

<a id="memory-linked-to-code-for-coding-agents"></a>

# Memoria vinculada al código para agentes de programación

Loom vincula cada memoria que guarda un agente de programación con las funciones, clases y archivos que nombra, sigue esos símbolos a través de refactorizaciones y marca la memoria cuando ese código cambia.
De forma predeterminada, una memoria cuyo código ha desaparecido por completo deja de aparecer en las búsquedas, de modo que las lecciones obsoletas dejan de llegar a los agentes. Funciona sobre los repositorios que Loom indexa (el [índice de código](code-intelligence.md), `LOOM_CODE_INGEST_ENABLED=true`), y el worker `memory_code_links`, que mantiene los vínculos, está activado de forma predeterminada.

<a id="what-you-get"></a>

## Qué obtienes

- **Lecciones asociadas al código del que tratan.** Una memoria etiquetada con un repositorio (los plugins de Claude Code y Codex etiquetan sus sesiones) o escrita en el espacio de nombres de código de un repositorio se vincula con cada definición que nombra entre comillas invertidas y que se resuelve en un único lugar (y, dentro de los archivos que lista, con las definiciones que nombra como palabra suelta), y con cada archivo que nombra y que no contiene ninguna de ellas. Un nombre calificado que aún no está en trunk se vincula si se fusiona dentro de `memory_code_links_pending_days` (14).
  [Memorias vinculadas al código](https://github.com/ClickHouse/loom/blob/main/docs/concepts/memory.md#memories-linked-to-code)
- **Vínculos que siguen las refactorizaciones.** En cada commit indexado, cada vínculo figura como `fresh`, `moved`, `changed` (indicando el commit que lo cambió), `unresolved` o `missing`. Un vínculo registra los tokens de la definición, así que los espacios en blanco, los comentarios y la mayoría de los cambios de formateador lo dejan en `fresh`.
- **Memorias obsoletas tratadas como tú elijas.** El `code_change_policy` del espacio de nombres saca de la búsqueda una memoria cuyo código ha desaparecido por completo (`retire_removed`, el valor predeterminado), solo la marca (`flag`), o hace que el modelo contraste la afirmación con el código antiguo y el nuevo y sugiera una corrección (`revise`). Loom nunca reescribe la memoria por sí mismo.
- **Las lecciones aparecen donde el agente lee código.** `code.symbol` devuelve como `lessons` las memorias vinculadas a una definición, cada una con el estado de su vínculo; `memory.for_file` entrega las memorias vinculadas al código de un archivo; el plugin de Claude Code las muestra antes de editar ese archivo. [`code.symbol`](https://github.com/ClickHouse/loom/blob/main/docs/reference/operations/code.md#codesymbol) ·
  [Plugin de Claude Code](https://github.com/ClickHouse/loom/blob/main/docs/features/integrations/claude-code.md)
- **Las ramas leen su propio código.** En una rama de lanzamiento que aún conserva el texto antiguo, una lección figura como `fresh` después de que trunk lo haya cambiado.
- **Documentos junto al código.** Los documentos de una fuente registrada en el espacio de nombres de código de trunk de un repositorio llevan la etiqueta del repositorio, de modo que las búsquedas sobre el repositorio los leen junto con su código (el worker de sincronización requiere `LOOM_KNOWLEDGE_SOURCE_SYNC_ENABLED`).
  [Fuentes de documentos](https://github.com/ClickHouse/loom/blob/main/docs/features/knowledge.md#document-sources)
- **Una lista de revisión.** `namespace.validate` lista las memorias cuyo código ha cambiado o ha desaparecido, y el dashboard muestra los vínculos de cada memoria y su estado.

<a id="start"></a>

## Primeros pasos

La [guía de inicio rápido](../getting-started/quickstart.md) pone Loom en marcha, y
[añadir repositorios](https://github.com/ClickHouse/loom/blob/main/docs/features/code-index/repositories.md) activa el índice de código para un
repositorio. Conecta tu agente de programación con su plugin o mediante MCP
([conectar un agente de programación](https://github.com/ClickHouse/loom/blob/main/docs/getting-started/connect-a-coding-agent.md)) y guarda una lección
que nombre una definición entre comillas invertidas. Una vez indexado trunk y ejecutada la siguiente pasada de vinculación
(cada 60 segundos de forma predeterminada), `code.symbol` sobre esa definición devuelve la lección en `lessons`.
