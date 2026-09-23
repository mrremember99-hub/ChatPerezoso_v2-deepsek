# MCP: cuando ayuda y cuando estorba

Documento de referencia sobre el plugin MCP (server-filesystem) y su
interaccion con modelos de distinto tamano.

## Estado actual

- MCP viene **desactivado por defecto** (`mcp_servers.json` con
  `enabled: false`).
- Cuando se activa, `MCPToolBridge` **oculta** las herramientas
  nativas del nucleo que el servidor MCP reemplaza
  (`_CORE_REPLACEMENTS` en `plugins/mcp/bridge.py`).
- El modelo pasa a operar con nombres largos con prefijo
  (`mcp__fs__read_text_file`, `mcp__fs__write_file`...) y
  semantica distinta a la del nucleo.

## Por que funciona mal con modelos pequenos

No hay una sola causa. Es la combinacion de siete factores que se
suman:

1. **Vocabulario mezclado.** Con MCP activo, el modelo ve dos
   convenciones a la vez: `mcp__fs__read_text_file` (MCP) y
   `borrar_archivo` (nucleo). Tiene que recordar cual aplica a cada
   accion. Modelos pequenos fallan aqui.

2. **Nombres largos.** `mcp__fs__read_text_file` (27 chars) frente
   a `leer_archivo` (13 chars). Menos margen para errores
   tipograficos.

3. **Semantica distinta.** `mcp__fs__write_file` sobrescribe.
   `mcp__fs__edit_file` requiere `oldText` y `newText` explicitos.
   El modelo pequeno los confunde y produce errores de MCP que no
   entiende.

4. **Paths absolutos.** El servidor MCP se lanza con la ruta del
   workspace como argumento y trata las rutas relativas a esa raiz.
   Si el modelo envia `"./workspace/gui.py"` en lugar de `"gui.py"`,
   el servidor rechaza. El nucleo centraliza el manejo de rutas en
   `Workspace._path()` y siempre resuelve bien.

5. **Latencia real.** MCP usa subproceso Node + JSON-RPC sobre
   stdin/stdout. Cada tool call son 50-200 ms. Con 5-10 tool calls
   en una fase, el modelo percibe lentitud y se "desincroniza".

6. **Confirmaciones extra.** Las tools MCP de escritura no estan en
   `_TRUSTED_READONLY`, asi que piden confirmacion al usuario. Si el
   usuario no responde en 10 min, el worker cancela y el modelo ve
   "OPERACION CANCELADA".

7. **System prompt mas grande.** El listado de tools permitidas pasa
   de 6 a 10-15 entradas. Menos contexto util para el modelo.

## Cuando MCP ayuda

- **Modelos grandes** (>= 20B): `qwen3-coder:30b`, `gpt-oss:20b`,
  `north-mini-code`. Manejan el doble vocabulario sin problema.
- **Tareas de exploracion avanzada.** `mcp__fs__directory_tree`,
  `mcp__fs__search_files` (via ripgrep) son mas potentes que
  `listar_carpeta` + `buscar_en_workspace` del nucleo.
- **Aislar el acceso al FS.** El servidor MCP puede apuntar a una
  raiz distinta a la del workspace nativo. Util si quieres
  restringir el alcance.

## Cuando NO usar MCP

- **Modelos <= 8B**: `granite4.1:3b`, `ministral-3`,
  `llama3.1:latest` (8B), `hermes3`.
- **Generacion de codigo archivo por archivo.** Multiples lecturas
  y escrituras por turno. Los nombres largos del MCP amplifican los
  errores.
- **Prompts en espanol con verbos concretos** ("crea gui.py",
  "anade X"). El `ToolIntentGate` del nucleo esta afinado para
  estos verbos. El MCP recibe los mismos verbos pero con reglas
  heredadas del nucleo, y a veces se confunde.

## Decision matrix

| Modelo                     | MCP off | MCP on |
| ---                        | ---     | ---    |
| granite4.1:3b              | OK      | NO     |
| ministral-3:latest         | OK      | NO     |
| llama3.1:latest (8B)       | OK      | A veces|
| hermes3:latest (8B)        | OK      | A veces|
| qwen3:14b                  | OK      | OK     |
| gpt-oss:20b                | OK      | OK     |
| qwen3-coder:30b            | OK      | OK     |
| north-mini-code-1.0        | OK      | OK     |

## Posibles mejoras

### A. Documentar y avisar (minimo)

Anadir un tooltip al boton MCP en el panel derecho que diga:
"El modelo puede verse sobrecargado con modelos pequenos (<8B).
Desactivar si el modelo es pequeno."

Coste: 5 min. Beneficio: el usuario entiende el trade-off.

### B. Deteccion automatica por nombre (medio)

Heuristica: si el nombre del modelo termina en `:Xb` con X <= 8, o
contiene `mini` / `small` / `granite` / `ministral`, mostrar un badge
de advertencia en el boton MCP cuando esta activo.

Problema: los nombres no siempre indican tamano (`llama3.1:latest`
es 8B pero no lo dice).

Coste: 20 min. Beneficio: aviso contextual.

### C. No ocultar las tools nativas con MCP activo (invasivo)

Cambiar `MCPToolBridge._rebuild_definitions` para NO ocultar los
nativos. El modelo elegiria entre `leer_archivo` y
`mcp__fs__read_text_file`.

Riesgo: el modelo puede confundirse AUN MAS al ver dos formas de
hacer lo mismo. Podria empeorar el problema que intenta resolver.

Coste: 30 min. Beneficio: incierto.

### D. Eliminar MCP (nuclear)

Borrar plugins/mcp/, el toggle del panel derecho, el controller,
mcp_servers.json, los tests relacionados.

No recomendado:
- El diseno es correcto para modelos potentes.
- El plugin esta bien aislado.
- Otros usuarios con modelos grandes lo aprovechan.
- Borrarlo deshace trabajo que funciona.

Coste: 2-3 h. Beneficio: solo en el caso de modelos pequenos.

## Decision pendiente

Por defecto: **opcion A**. Documentar y avisar. Las opciones B, C, D
quedan disponibles si el problema se hace recurrente.
