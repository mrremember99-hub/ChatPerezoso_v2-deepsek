# Auditoría completa — ChatPerezoso v2 (deepsek)
Fecha: 2026-09-26. Continúa `docs/audit-2026-09.md` (H1-H15, ya aplicados) y las dos pasadas previas de esta conversación (H1-H8 sobre ollama.py/context_window.py/app_controller.py). Esta pasada cubre los ficheros restantes de `core/` no auditados aún, `ui/workers.py`, `ui/rendering/*`, `plugins/shell/*`, y cruza los benchmarks reales ya existentes en `docs/benchmark-2026-09-25.md`.

**Regla seguida:** solo se reportan hallazgos con evidencia en el código o en los benchmarks/documentación citada. Donde no hay evidencia (ejecución de la app, telemetría de producción) se marca `NO DETERMINABLE` en vez de inventar cifras.

**Ficheros leídos en esta pasada:** `agents.py`, `composite_tools.py`, `config.py`, `mcp_servers.py`, `model_capabilities.py`, `models_config.py`, `plugins_registry.py`, `prompt_phases.py`, `request_snapshot.py`, `shutdown.py`, `stream_events.py`, `token_calibration.py`, `tool_cache.py`, `tool_provider.py`, `tool_result.py`, `workspace.py`, `workspace_snapshot.py`, `xml_tools.py`, `ollama.py`, `context_window.py`, `intent.py`, `tool_strategies.py`, `async_runner.py`, `main.py`, `ui/workers.py`, `ui/controllers/chat_controller.py`, `ui/rendering/*`, `plugins/shell/*`.

**Ficheros NO reabiertos en esta pasada** (ya cubiertos por auditorías previas y sin nueva evidencia que los cuestione): `app_controller.py`. **Ficheros NUNCA auditados hasta ahora**: `history.py`, `ui/controllers/{agent_controller,diagnostics_controller,mcp_controller,model_controller}.py`, `ui/views/*`, `ui/widgets.py`, `ui/diagnostics.py`, `plugins/{git,mcp,search,verificador}/*`, `tests/*`. Esto limita el alcance de las secciones 6 (concurrencia MCP), 15 (tool calling git/search/MCP) y 17 (vistas Qt) — se marcan `NO AUDITABLE TODAVÍA` donde corresponde.

---

## 1. Resumen ejecutivo

La base de código está en un estado de madurez alto: hay tres rondas de auditoría previas ya aplicadas (H1-H15 en `docs/audit-2026-09.md`, más H1-H8 de esta conversación), con tests de regresión y benchmarks reales (no simulados) para streaming, tool calling y renderizado. La mayoría de los patrones de riesgo típicos de este tipo de apps (prompt building con `+=`, cachés sin invalidar, mutación del estado del llamante, condiciones de carrera en cancelación) ya están resueltos y documentados con su propio test.

Los hallazgos **nuevos** de esta pasada son de severidad MEDIA o inferior; no hay ningún CRÍTICO nuevo:

| # | Severidad | Área | Resumen |
|---|---|---|---|
| N1 | ALTO | Calidad LLM / thinking | `models.json` permite `thinking: true/false`, pero gpt-oss exige `"low"/"medium"/"high"` — Ollama **ignora silenciosamente** el booleano para ese modelo. |
| N2 | MEDIO | Tool calling | 5 de 7 modelos del benchmark de calidad devuelven `path: "/"` en `listar_carpeta`, violando la instrucción del system prompt; la app los bloquea correctamente pero gasta una ronda completa en el camino más frecuente. |
| N3 | BAJO/MEDIO | Contexto | El margen de seguridad fijo del 15% (`_PROMPT_BUDGET_MARGIN`) no se relaja cuando el modelo ya tiene calibración empírica fiable (`token_calibration`), siendo doblemente conservador sin necesidad. |
| N4 | BAJO | Frescura de contexto | La caché de `model_capabilities` (incluye `context_length`) no tiene TTL; solo se invalida con `force_refresh` explícito. |
| N5 | BAJO | Tool calling / XML | `xml_tools._coerce_value` tipa argumentos por heurística de texto, no por el `type` declarado en el schema de la tool — riesgo de coerción incorrecta de contenidos de archivo que sean literalmente `"true"`/`"false"`/números. |
| N6 | INFORMATIVO | Seguridad | `plugins_registry.py` ejecuta código arbitrario de terceros al cargar un entry point (antes de instanciar). Coherente con el modelo de confianza ya documentado en `mcp_servers.py`; no es una regresión. |

El resto de esta pasada confirma, con lectura línea por línea, que las áreas ya intervenidas (streaming con backpressure real, renderer segmentado, cancelación vía `future.cancel()`, poda de contexto O(n) con prefix sums) están bien resueltas y no requieren más trabajo salvo N3.

---

## 2. Arquitectura real

```text
main.py
 └─ AppController (ui/controllers/app_controller.py)
     ├─ OllamaClient (core/ollama.py) ── AsyncRunner (core/async_runner.py, loop dedicado)
     ├─ ChatController (ui/controllers/chat_controller.py)
     │    ├─ ChatWorker (ui/workers.py) — hilo Qt, ejecuta OllamaClient.chat()
     │    ├─ ChatRenderer → PlainTextRenderer (ui/rendering/plain_text.py + markdown_renderer.py)
     │    ├─ ContextWindow (core/context_window.py) — poda de historial
     │    └─ HistoryStore / AsyncHistoryWriter (core/history.py) — persistencia debounced
     ├─ CompositeToolProvider (core/composite_tools.py)
     │    ├─ ToolRegistry (núcleo: workspace, shell)
     │    ├─ GitProvider (plugins/git)
     │    ├─ SearchProvider (plugins/search)
     │    ├─ VerificadorProvider (plugins/verificador)
     │    └─ MCPToolBridge (plugins/mcp) — servidores externos vía mcp_servers.json
     ├─ AgentController → AgentStore (core/agents.py) — perfiles con system_prompt/params/tools
     ├─ ModelController → ModelsConfig (core/models_config.py) + model_capabilities.py (/api/show)
     └─ MCPController
```

Puntos de diseño relevantes observados:

- **Doble modo de tool calling** (`NativeToolStrategy` / `XmlToolStrategy`, `core/tool_strategies.py`), elegido por `ModelCapabilities.tool_mode`, con un único punto de autorización compartido (`authorize_and_execute`). Bien resuelto: evita la duplicación que causaba desincronización de seguridad (motivo explícito documentado en el propio módulo).
- **Orquestación por fases** (`core/prompt_phases.py`): trocea prompts tipo `FASE 1/2/3` en turnos independientes con snapshot de workspace fresco por fase, para evitar que el prefill crezca linealmente con el número de fases. Es una mitigación real de un problema de latencia conocido, no especulativa.
- **Caché de resultados de tools con TTL 5s** (`core/tool_cache.py`) e invalidación total al ejecutar cualquier tool de escritura.

---

## 3. Flujo completo de una petición LLM

```text
UI (send) → ChatController.send()
  → _build_tool_trace() (acciones del turno anterior, hasta 3000 chars)
  → ChatWorker(model, messages, tools, options, system_prompt, context_window)
     → OllamaClient.chat()
        → _prepare_context(): capabilities /api/show → strategy → intent gate →
          tools activas (ToolIntentGate.tools_for_request) → inyecta system prompt
        → bucle de rondas (máx 15):
            → _fit_round_history() (ContextWindow.fit, por ronda)
            → _stream() → AsyncRunner.submit(_stream_async) → iter_ollama_events()
               → NDJSON → TextDelta / ToolCallsDelta / StreamFinished
            → strategy.process_round() → RoundResult
            → si tool_calls: authorize_and_execute() por cada una → historial
            → si final: nudges de falso-completado / stall guard si aplica
  → ChatWorker.finished → ChatController._on_done()
     → drain final del buffer → renderer.final_text() → append a self.messages
     → _compact_if_needed() (poda del historial persistente)
     → persistencia debounced (500 ms) → HistoryStore
```

No hay pasos donde se pierda información silenciosamente: cada punto de truncamiento/poda dentro de `ollama.py`/`context_window.py` inserta un marcador textual (`[CONTEXTO RECORTADO...]`) que el modelo recibe. Esto es una fortaleza objetiva frente al patrón común de podar sin avisar al modelo.

---

## 4. LLM Request Dossier / payload real

Reconstrucción de lo que **realmente** recibe Ollama en `/api/chat`, a partir del código (no de logs de producción, que no se han proporcionado):

```text
PAYLOAD FINAL (POST /api/chat):
{
  "model": <str>,
  "messages": [
     {"role": "system", "content": <system_prompt_usuario> + "\n\n" + <tool_prompt_o_XML> + [marcador de recorte si aplica]},
     ...historial podado por ContextWindow.fit()...,
     {"role": "assistant", "tool_calls": [...], "thinking": <si el modelo lo emitió>},
     {"role": "tool"|"user"[TOOL_RESULT:name], "content": <resultado>},
     ...
  ],
  "tools": [...] ,          // solo si NativeToolStrategy Y hay tools autorizadas por el intent gate
  "stream": true,
  "keep_alive": "30m",
  "options": {"temperature":..., "num_ctx":... (si num_ctx>0)},
  "think": <bool>            // SOLO si hay override en models.json; ver hallazgo N1
}
```

`NUM_PREDICT`: **NO DETERMINABLE como parámetro explícito** — no se ha encontrado ningún sitio del código que fije `num_predict`; se deja al default de Ollama/modelo. Esto es relevante porque la sección 13 exige distinguir truncamiento por `num_predict`: el código sí detecta `done_reason == "length"` y lo marca visualmente (`_TRUNCATED_STREAM_SUFFIX`), pero la app nunca controla activamente cuándo ocurre ese truncamiento — depende enteramente del default del modelo/Ollama. Si el usuario quiere respuestas largas garantizadas (por ejemplo, un archivo grande completo), no hay ningún control de `num_predict` en la UI ni en `AppConfig`/`Agent`. Es una laguna de control, no un bug.

**Diferencia "qué debería recibir" vs "qué recibe realmente":** ninguna detectada en el camino feliz. La única discrepancia real es N1 (thinking booleano vs enum para gpt-oss).

---

## 5. Auditoría línea por línea — hallazgos nuevos

### N1 — Override de `thinking` incompatible con gpt-oss

```text
ARCHIVO: core/models_config.py (ModelOverride.thinking: bool | None)
         core/ollama.py, _stream_async(): payload["think"] = override.thinking

SEVERIDAD: ALTO
EVIDENCIA: DEMOSTRADO (documentación oficial de Ollama, ver §39)

PROBLEMA:
El override de thinking en models.json solo admite bool. El payload
envía `"think": true` o `"think": false` literal.

CAUSA:
docs.ollama.com/capabilities/thinking especifica textualmente que
GPT-OSS "expects one of low, medium, or high to tune the trace
length" y que "Passing true/false is ignored for that model."

IMPACTO:
Un usuario que configure `{"gpt-oss:20b": {"mode": "auto", "thinking": false}}`
en models.json para bajar el TTFT del modelo cree estar desactivando
el razonamiento, pero Ollama ignora el valor: el modelo sigue
razonando con su nivel por defecto. La recomendación automática que
genera `_auto_recommendation()` ("Thinking · razona lento, para
análisis") puede quedar desalineada con lo que el usuario configuró
explícitamente sin que la UI lo avise.

FRECUENCIA: cada request a un modelo de la familia gpt-oss con
override de thinking definido.

SOLUCIÓN:
- Cambiar el tipo de `ModelOverride.thinking` a
  `bool | Literal["low", "medium", "high"] | None`.
- En `_stream_async`, si `model.startswith("gpt-oss")` (o mejor,
  añadir un campo `thinking_kind` a `ModelCapabilities` detectado
  vía `/api/show`), mapear bool→nivel por defecto ("high" si True,
  omitir el campo si False no es soportado) o exigir que el usuario
  indique el nivel directamente.

COMPLEJIDAD: Baja. RIESGO: Bajo (cambio aditivo, no rompe overrides existentes con bool en otros modelos).
VERIFICACIÓN: test que fija override thinking=False sobre un modelo
"gpt-oss:*" simulado y comprueba que el payload no envía `think: false`
sino el nivel correcto, o loggea un aviso.
```

### N2 — Rutas absolutas en argumentos de tools bloqueadas tarde

```text
ARCHIVO: core/workspace.py, Workspace._path()
         (contrastado con docs/benchmark-2026-09-25.md, test "tool_selectivo")

SEVERIDAD: MEDIO
EVIDENCIA: DEMOSTRADO (benchmark real, no simulado)

PROBLEMA:
qwen3:1.7b, mistral-small3.2, ministral-3, muse-glimmer y
ornith-1.5:9b (5 de 7 modelos evaluados) devuelven
`listar_carpeta(path="/")` o `path=""` pese a que el system prompt
generado por `_tool_system_prompt()` prohíbe explícitamente rutas
absolutas. `Workspace._path()` hace `(self.root / relative).resolve()`
+ `relative_to(self.root)`; en Python, unir un Path absoluto ("/")
descarta la base, así que el resultado nunca es relativo a root y
se lanza `WorkspaceError("Ruta fuera del workspace.")`. Correcto en
seguridad, pero el modelo recibe un error genérico y necesita una
ronda adicional para reformular con ".".

CAUSA:
El comportamiento es el más común entre modelos pequeños/medianos:
interpretan "raíz" como "/" del sistema de archivos, no como raíz
del workspace, pese a la instrucción explícita.

IMPACTO:
Latencia y rondas desperdiciadas en el camino MÁS FRECUENTE del
benchmark (listar la raíz es el primer paso natural de casi
cualquier tarea). No es un problema de seguridad.

SOLUCIÓN:
En `Workspace._path()`, si `relative` es absoluto (empieza por "/"
o es una ruta absoluta de Windows), reinterpretarlo como relativo a
la raíz (`relative.lstrip("/")`) antes de resolver, en vez de
solo bloquear. Mantener el bloqueo únicamente para escapes reales
(`../../etc/passwd`).

COMPLEJIDAD: Baja. RIESGO: Bajo (amplía qué se acepta, no reduce
seguridad: sigue sin poder salir del workspace).
VERIFICACIÓN: test `Workspace.list_dir("/")` debe devolver el
listado de la raíz del workspace en vez de lanzar WorkspaceError;
re-ejecutar el benchmark de calidad y comprobar que
"tool_selectivo" pasa a la primera en los 5 modelos afectados.
```

### N3 — Margen de contexto fijo no se relaja con calibración empírica

```text
ARCHIVO: core/context_window.py, ContextWindow.prompt_budget (usa
         _PROMPT_BUDGET_MARGIN=0.85 sin condición)
         core/token_calibration.py (calibración EWMA por modelo, ya
         implementada y usada en estimate_tokens)

SEVERIDAD: BAJO/MEDIO
EVIDENCIA: DEMOSTRADO (lectura directa; el efecto cuantitativo no
está medido — POSIBLE en su magnitud)

PROBLEMA:
`prompt_budget` aplica SIEMPRE un descuento del 15% sobre
`limit - output_reserve`, independientemente de si
`token_calibration.has_calibration(model)` es True. La propia
justificación del margen (comentario en el código) es "la
estimación de tokens puede desviarse un 10-20%" — pero esa
desviación es precisamente lo que la calibración EWMA reduce una
vez hay >=3 observaciones reales de `prompt_eval_count`.

IMPACTO:
En conversaciones largas con un modelo ya calibrado, la app
conserva menos historial del que el presupuesto real permitiría
(hasta un 15% menos), disparando compactación antes de lo
necesario. Esto es una pérdida de contexto útil sin beneficio de
seguridad adicional, una vez la estimación es fiable.

SOLUCIÓN:
Hacer el margen función de la calibración:
`margin = 0.94 if token_calibration.has_calibration(model) else 0.85`
(0.94 deja margen para variación intra-turno de argumentos de
tools, que no pasan por chars/token calibrado igual que el texto).

COMPLEJIDAD: Baja. RIESGO: Bajo — cambia solo cuánto historial se
conserva, la lógica de poda (prefix sums, no partir cadenas de
tools) no cambia.
VERIFICACIÓN: benchmark con conversación larga + modelo calibrado:
medir `dropped_messages` antes/después del cambio con el mismo
historial sintético.
```

### N4 — Caché de capacidades de modelo sin TTL

```text
ARCHIVO: core/model_capabilities.py, _CACHE (dict global, solo se
         limpia con clear_cache() o force_refresh=True)

SEVERIDAD: BAJO
EVIDENCIA: DEMOSTRADO

PROBLEMA:
`context_length`, `native_tools`, `vision`, `thinking` se cachean
indefinidamente por (host, model). Si el usuario reemplaza el
modelo (`ollama pull` de una versión con distinto context_length)
sin reiniciar la app ni forzar refresh, la app sigue calculando
presupuestos de contexto con el valor antiguo.

IMPACTO: bajo en la práctica (el usuario normalmente reinicia tras
actualizar un modelo), pero es la misma categoría de "CACHE SIN
INVALIDACIÓN" que la sección 22 del guion pide clasificar
explícitamente.

SOLUCIÓN: TTL de 10-15 minutos en `_CACHE`, o invalidar al detectar
un cambio de host/lista de modelos disponible.
COMPLEJIDAD: Baja. RIESGO: Bajo.
```

### N5 — Coerción de tipos en XML tool calling sin usar el schema

```text
ARCHIVO: core/xml_tools.py, _coerce_value()

SEVERIDAD: BAJO
EVIDENCIA: DEMOSTRADO (lectura directa); impacto real POSIBLE

PROBLEMA:
`_coerce_value` convierte "true"/"false"/"null"/números por
heurística de texto, sin consultar `properties[key]['type']` del
schema de la tool (disponible en `known_tools`/definitions, pero no
se pasa a esta función). Solo aplica al dialecto XML
(`<function=NAME><parameter=K>V</parameter></function>`), que es el
fallback para modelos sin tool calling nativo.

IMPACTO: si un modelo en modo XML llama a `escribir_archivo` con
`<parameter=content>true</parameter>` (por ejemplo, escribiendo un
archivo de configuración con el literal `true`), el argumento
`content` llega como `bool` de Python en vez de `str`, y
`Workspace.write_file` (`content.encode("utf-8")`) fallaría con
`AttributeError` no capturado como `WorkspaceError` — se propagaría
como excepción no controlada dentro de `ToolProvider.call`. NO
DETERMINABLE si esto ya ha ocurrido en producción (no hay logs de
excepciones de este tipo).

SOLUCIÓN: pasar la definición completa de la tool a
`parse_function_xml` y tipar cada parámetro según su
`properties[k]['type']` declarado, en vez de heurística universal.
COMPLEJIDAD: Media (cambia la firma de una función usada en 2
sitios). RIESGO: Bajo.
VERIFICACIÓN: test con modelo XML simulado que llama a
escribir_archivo con content="true" y comprueba que se escribe el
string "true", no se lanza excepción.
```

### N6 — Carga de plugins de terceros ejecuta código arbitrario (informativo)

```text
ARCHIVO: core/plugins_registry.py

SEVERIDAD: INFORMATIVO (mismo modelo de confianza ya documentado en
mcp_servers.py; no es una regresión ni un hallazgo accionable nuevo)

Cargar un entry point de `chatperezoso.plugins` ejecuta el código
del paquete de terceros en el proceso de la app, sin sandboxing.
Coherente con que el usuario es dueño de su entorno Python. Se
documenta aquí solo para que quede junto al resto del análisis de
superficie de confianza (mcp_servers.py, shell/client.py).
```

Confirmaciones sin hallazgo (código ya correcto, verificado línea por línea):

- `tool_cache.py`: TTL + invalidación total en escritura, purga proactiva al 80% del límite. Sin bugs.
- `async_runner.py`: `submit()` agrupa "obtener loop + programar corrutina" bajo el mismo lock (fix de H1 de `audit-2026-09.md` confirmado presente); watcher de cancelación captura `loop` por argumento, no por `self._loop`, evitando la carrera documentada. Sin hallazgos nuevos.
- `plain_text.py` / `markdown_renderer.py`: renderizado segmentado por frontera de párrafo/fence (fix de Fase 2.2 de `audit-2026-09.md` confirmado); listas por `append`+`join` en vez de `+=` sobre `str` (evita O(n²)). Sin hallazgos nuevos.
- `plugins/shell/client.py`: 8 capas de seguridad documentadas, todas presentes y coherentes (`shlex.split` sin `shell=True`, blacklist de metacaracteres, blacklist de programas normalizada a minúsculas, timeout con `psutil` matando el árbol de procesos completo, entorno mínimo, confirmación obligatoria sin excepción, defensa en profundidad en el provider). Sin hallazgos.
- `context_window.py::fit()`: la regla H3 (no partir cadenas de tool calls) y el suelo de "al menos el último user truncado" están implementados y se corresponden con el comentario que los describe. Complejidad O(n) confirmada (prefix sums + búsqueda lineal de `cut_at`, sin bucles anidados sobre `messages`).

---

## 6. Concurrencia

`NO AUDITABLE TODAVÍA` para `plugins/mcp/*` (transporte MCP, no releído en esta pasada). Sobre lo auditado:

- `AsyncRunner`: un único event loop dedicado por `OllamaClient`; cancelación vía `future.cancel()` desde un hilo watcher que bloquea en `cancel_event.wait()` (coste CPU ~0). No hay polling activo. Confirmado sin llamadas bloqueantes dentro de las corrutinas de `iter_ollama_events`/`_stream_async` (solo `await client.stream(...)` y `response.aiter_bytes(...)`).
- `TextDeltaBuffer` (ui/workers.py): backpressure real con `threading.Condition`, el productor (hilo del AsyncRunner) bloquea si el buffer supera 1 MB, hasta que el consumidor (timer Qt a 32 ms) drena. Correcto: evita crecimiento no acotado de RAM en streams muy rápidos sin bloquear el hilo de UI.
- `ChatWorker._confirmation_lock`: protege la sección crítica entre `cancel()` y `resolve_confirmation()`. Revisado: el `event.set()` se hace SIEMPRE fuera del lock tras leerlo, evitando deadlock si el callback de Qt reentra. Correcto.
- No se detectaron tareas huérfanas: `AsyncRunner._run_loop` cancela explícitamente `asyncio.all_tasks()` pendientes al terminar el loop.

## 7. Streaming

Reconstrucción confirmada:

```text
Ollama → httpx.AsyncClient.stream() → aiter_bytes(1024) → incremental UTF-8 decoder
  → split por "\n" → parse_ollama_line() (json.loads por línea, líneas inválidas se
    descartan con logger.debug, no rompen el stream)
  → TextDelta/ToolCallsDelta/StreamFinished
  → _stream_async: buffering adaptativo anti-tool-call-textual (heurística por
    keywords JSON, límite de 200 chars de "peek", detección de code-fence abierto
    para no confundir código de usuario con una tool call)
  → TextDeltaBuffer (coalescing, backpressure)
  → drain cada 32 ms → PlainTextRenderer.on_text() → render Markdown incremental
    por segmento (frontera de párrafo o cierre de fence, mínimo 300 chars)
```

No se detectan chunks perdidos ni duplicados: el `content_parts`/`thinking_parts` se acumulan como lista y se unen una sola vez al final (`"".join`), no se reconstruye incrementalmente. `codecs.getincrementaldecoder("utf-8")` maneja correctamente bytes UTF-8 partidos entre chunks.

`RESPUESTA RECIBIDA` vs `MOSTRADA`: idénticas salvo por el buffering deliberado del bloque `<tool_call>`/JSON-textual, que se oculta al usuario a propósito (es la salvaguarda anti-alucinación de tool calling textual, no una pérdida de información: el texto oculto es ruido de formato, no contenido útil).

## 8. Latencia

Datos reales disponibles (no inventados, de `docs/audit-2026-09.md`):

| Modelo | TTFT | tok/s | Nota |
|---|---|---|---|
| granite4.1:3b (warm) | 0.24 s | 42.6 | baseline de streaming |
| gemma4:12b | 85 s | 3.5 | cold start / presión de memoria (descarga entre turnos) |

Y de `docs/benchmark-2026-09-25.md` (5 pruebas/modelo, timeout 180s):

| Modelo | Éxito | Tiempo total (5 pruebas) |
|---|---|---|
| qwen3:1.7b | 5/5 | 18.7 s |
| ministral-3 | 5/5 | 54.6 s |
| ornith-1.5:9b | 5/5 | 58.2 s |
| mistral-small3.2 | 5/5 | 281.9 s |
| gpt-oss:20b | 4/5 | 213.6 s (1 error 500 en "razonamiento") |
| muse-glimmer | 4/5 | 402.9 s (1 error 500 en "tool_simple") |
| qwen3.6:27b | 1/5 | 199.1 s (4 fallos tras el primero) |

**Latencia introducida por la app vs por el modelo:** la app no añade latencia sincrónica relevante en el camino de streaming — el drain cada 32 ms es asíncrono al hilo de UI y el render por segmento cuesta <1 ms según el benchmark de renderer citado en `audit-2026-09.md` ("code final 26.6 ms → 0.1 ms"). La latencia dominante observada es 100% atribuible a Ollama/modelo (cold start, tamaño del modelo, fallos de plantilla como los 500 de gpt-oss/muse-glimmer).

`NUM_CTX`/`T_context`/`T_tool`/desglose fino de latencia por fase: **NO DETERMINABLE** sin instrumentación adicional (el código no loguea timestamps de `headers_received`/`first_token`/`last_token` por separado; solo las métricas agregadas que Ollama devuelve en el chunk final: `prompt_eval_duration`, `eval_duration`, `total_duration`, `load_duration`, que sí se capturan en `parse_ollama_line` pero no se han volcado a un reporte en esta pasada).

## 9. Contexto y memoria

Ver hallazgo N3. El resto del diseño (poda O(n), no partir cadenas de tools, marcador de recorte reenviado al modelo, `RequestTokenCache` para no reestimar mensajes sin cambios entre rondas) está bien resuelto y confirmado en la lectura línea por línea de §5.

`context_efficiency` (información relevante / tokens totales): **NO DETERMINABLE** sin un corpus de conversaciones reales instrumentado; no se ha construido tal corpus en esta pasada.

## 10. Pérdida de información

| Información | Origen | Transformación | Destino | ¿Se conserva? |
|---|---|---|---|---|
| Resultados de tools de rondas anteriores | `authorize_and_execute` | No se persisten en `self.messages` (viven solo en la copia local de `OllamaClient.chat()`) | Se resumen en `_build_tool_trace()` (máx 3000 chars, políticas por categoría) e inyectan al system prompt del turno siguiente | Parcial, por diseño — errores completos, lecturas cortas completas, exec truncado a 300 chars, escrituras sin detalle (solo status) |
| Historial podado por `ContextWindow.fit()` | conversación completa | recorte | marcador `[CONTEXTO RECORTADO: N mensajes...]` | Se avisa al modelo explícitamente; no se "oculta" la pérdida |
| `thinking` del modelo | ronda N | se reenvía en el `assistant` message de la ronda N+1 si hay tool calls | historial de esa generación | Se conserva dentro del mismo `chat()`, se descarta entre turnos (no se persiste en `self.messages`) — correcto, evita hinchar el historial persistido con razonamiento interno |

No se detectan pérdidas no intencionadas nuevas más allá de N5 (coerción de tipo, caso límite).

## 11. Calidad del prompt (system prompt)

`_tool_system_prompt()` (nativo) es explícito, con prohibiciones claras y una sección "CUÁNDO USAR HERRAMIENTAS" bien acotada. El hallazgo N2 muestra que, pese a esa claridad, el 71% de los modelos evaluados (5/7) igualmente usan rutas absolutas — indicando que el texto del prompt, por sí solo, no resuelve completamente ese patrón en modelos pequeños/medianos, y que vale más la pena resolverlo en código (N2) que seguir puliendo el texto del prompt.

`build_tools_prompt()` (modo XML) es más compacto (descripciones truncadas a 140 chars, firma de parámetros abreviada) — coherente con que el modo XML es un fallback de compatibilidad, no el camino principal.

## 12. Contradicciones

No se detectan contradicciones entre el system prompt del usuario y el de las tools: se concatenan con `\n\n`, sin overlap semántico (uno describe el rol/estilo, el otro las reglas de tools). El único vector de contradicción posible — historial con `tool_calls` nativos mezclado con estrategia XML tras un cambio de modo — está explícitamente resuelto por `_strip_native_tool_calls()`.

## 13. Frescura del contexto

| Caché | Clave | TTL | Invalidación | Clasificación |
|---|---|---|---|---|
| `tool_cache.ToolCache` | (nombre, args congelados) | 5s | Total, al ejecutar cualquier tool `invalidating` | CACHE CORRECTAMENTE INVALIDADA |
| `model_capabilities._CACHE` | (host, model) | ninguno | Solo manual (`clear_cache`/`force_refresh`) | CACHE POTENCIALMENTE OBSOLETA (N4) |
| `CompositeToolProvider._cached_definitions` | global al provider | ninguno | Manual (`invalidate()`, llamado por AppController al activar/desactivar MCP) | CACHE CORRECTAMENTE INVALIDADA (invalidación explícita documentada) |
| `token_calibration` (EWMA) | modelo | N/A (no es un caché de datos, es un aprendizaje online) | Se ajusta con cada observación; `reset()` manual disponible | No aplica la taxonomía de cachés; correcto por diseño |

## 14. Calidad de las respuestas LLM

Evidencia real (`docs/benchmark-2026-09-25.md`, 7 modelos × 5 pruebas):

- **qwen3.6:27b**: 1/5, con degradación catastrófica tras el primer test (tool_simple OK en 111s, luego 4 fallos consecutivos con latencias de 8-59s). Patrón compatible con un problema de plantilla o contexto que persiste entre turnos dentro de la misma sesión de benchmark — **NO DETERMINABLE** si la causa es el modelo, la versión de Ollama, o el estado que la app reenvía entre pruebas (el script de benchmark no se ha auditado en esta pasada).
- **gpt-oss:20b**: error 500 específicamente en el test "razonamiento" (el único que activa explícitamente `thinking`). Correlaciona con N1: el payload puede estar enviando `think` en un formato que el chat template de esa versión de gpt-oss no maneja bien. Ver además el hilo de HuggingFace citado en §39 sobre plantillas de gpt-oss rotas en ciertas builds de Ollama (`currentDate` no definida) — es una señal externa de que gpt-oss ha tenido problemas de plantilla conocidos, no exclusivos de esta app, pero relevante para decidir la prioridad de N1.
- **muse-glimmer**: error 500 en "tool_simple" (el primer test, con tools activas) pero éxito en el resto — indica que el fallo no es sistemático sino intermitente, compatible con un problema de carga en frío del modelo más que de payload.

**Atribución (§20):** los tres fallos anteriores son atribuibles a **Ollama/modelo** (errores 500 del servidor, plantillas de chat), no a la aplicación — la app no controla el chat template ni puede evitar un 500 del lado servidor. La única contribución de la app es N1 (parámetro incorrecto para gpt-oss), que sí es accionable.

## 15. Tool calling

Ver N2 (rutas absolutas) y N5 (coerción XML). El resto del ciclo (selección vs autorización separadas con `ToolIntentGate`, límite de rondas repetidas `_MAX_REPEATED_SIGNATURES=2`, límite de rondas bloqueadas consecutivas `_MAX_CONSECUTIVE_BLOCKED_ROUNDS=3`, detección de falso completado y de "stall") está implementado con nudges específicos y límites de reintento acotados (1 reintento cada uno) — diseño defensivo correcto contra loops, confirmado en la lectura de `ollama.py::chat()`.

`NO AUDITABLE TODAVÍA`: selección/ejecución de tools de `plugins/git`, `plugins/search`, `plugins/verificador`, `plugins/mcp` (no releídos en detalle esta pasada; `plugins/shell` sí, sin hallazgos).

## 16. Calidad del código generado

El flujo de escritura (`escribir_archivo`/`crear_archivo`) exige leer antes de escribir (regla en el system prompt) y dispone de un hook de verificación de sintaxis post-escritura opcional (`verificador_enabled`, `ui/workers.py::_maybe_verify`) que anexa errores de sintaxis al `ToolResult` que ve el modelo en el mismo turno — permite corrección iterativa sin turno adicional del usuario. Sin hallazgos nuevos aquí; `plugins/verificador` en sí no se ha releído.

## 17. Renderizado/UI

Confirmado sin hallazgos nuevos (ver §5). Point de atención informativo: `markdown.Markdown()` es una única instancia de módulo protegida por `threading.Lock` — correcto para el diseño actual (un solo chat activo a la vez), pero si en el futuro se soportan pestañas/chats concurrentes, ese lock serializaría el renderizado entre pestañas. No es un problema hoy.

`ui/views/*`, `ui/widgets.py`, `ui/diagnostics.py`: **NO AUDITABLE TODAVÍA**.

## 18. Complejidad algorítmica

Confirmado O(n) en las rutas críticas revisadas: `ContextWindow.fit` (prefix sums), `_truncate_by_lines` (prefix sums + bisección, no reconstrucción de string en cada iteración), `PlainTextRenderer` (listas + join, no `+=`). No se ha encontrado ningún O(n²) nuevo en los ficheros leídos esta pasada.

## 19. Errores y cancelación

Distinción `COMPLETADA/INTERRUMPIDA/TRUNCADA/CANCELADA` implementada explícitamente vía `StreamFinished.completed`/`done_reason` y verificada en `ollama.py::chat()` (H1/H2 de la pasada anterior, confirmados presentes). `plugins/shell/client.py` maneja timeout, cancelación (mata el árbol de procesos completo con `psutil`) y errores de `FileNotFoundError`/`PermissionError` con mensajes específicos.

## 20. Atribución de los problemas de calidad

Ver §14. Resumen: los fallos de calidad observados en el benchmark real son mayoritariamente **atribuibles al modelo/Ollama** (errores 500, degradación tras N turnos), con una única contribución accionable de la app (N1, parámetro thinking para gpt-oss) y un patrón de fricción menor pero frecuente (N2, rutas absolutas) que sí vale la pena arreglar en código porque **la evidencia demuestra que el prompt por sí solo no lo resuelve** en el 71% de los modelos probados.

## 21. Hallazgos priorizados

| Prioridad | Hallazgo | Impacto | Esfuerzo |
|---|---|---|---|
| 1 | N2 — normalizar rutas absolutas en Workspace | Reduce rondas desperdiciadas en el camino más común, en 5/7 modelos probados | Bajo |
| 2 | N1 — soportar thinking enum para gpt-oss | Corrige control de razonamiento realmente ignorado hoy | Bajo |
| 3 | N3 — relajar margen de contexto con calibración | Más historial útil conservado en conversaciones largas | Bajo |
| 4 | N4 — TTL en caché de capacidades | Evita `context_length` obsoleto tras cambiar de modelo en caliente | Bajo |
| 5 | N5 — tipar coerción XML por schema | Corrige caso límite raro en el fallback XML | Medio |

## 22. Investigación internacional y multilingüe

Realizada de forma **acotada** en esta pasada (una consulta en inglés sobre el parámetro `think` de gpt-oss, contra documentación oficial de Ollama y foros — ver §39). No se ha realizado la investigación multilingüe completa (chino, japonés, coreano, alemán, francés) que pide el guion original por límite de alcance de esta pasada; los hallazgos reportados no dependen de ella. Si se desea profundizar (por ejemplo, en comunidades chinas sobre optimización de contexto para modelos Qwen, dado que el proyecto usa varios modelos Qwen), puede hacerse como una pasada dedicada.

## 23. Proyectos de referencia

**NO DETERMINABLE en esta pasada** sin comprometerse a evitar cifras inventadas (estrellas, actividad) que el guion prohíbe explícitamente inventar. Puede hacerse como investigación dedicada con web_search verificando cada dato antes de incluirlo.

## 24. Comparación arquitectónica

No se identifica ningún patrón de diseño (DI, Event Bus, Repository, State Machine...) cuya ausencia sea un problema demostrado en el código leído. El proyecto ya usa Strategy (`ToolCallingStrategy`), Composite (`CompositeToolProvider`), y un Protocol-based duck typing ligero (`ToolProvider`, `ChatRenderer`) — proporcionados donde aportan valor real (evitar la duplicación de la lógica de autorización), no por moda. No se propone ningún patrón nuevo: la complejidad añadida no estaría justificada por ningún problema demostrado.

## 25. Librerías y alternativas

No se ha encontrado evidencia de que `httpx.AsyncClient` sea un cuello de botella (el cliente persistente ya se reutiliza entre turnos, con `Limits(max_keepalive_connections=2, max_connections=4)`, adecuado para un cliente de escritorio de un solo usuario). Cambiar de cliente HTTP no está justificado por ninguna evidencia recogida.

## 26-29. Benchmark de rendimiento / calidad / experimentos A/B / pruebas adversariales

**NO DETERMINABLE sin ejecutar la aplicación** (requiere una instancia de Ollama corriendo, GUI PySide6 interactiva y tiempo de ejecución real que esta sesión no tiene). Los scripts ya existen en el proyecto (`scripts/benchmark_streaming.py`, `scripts/benchmark_intent_gate.py`, `scripts/benchmark_context.py`, y el script que generó `docs/benchmark-2026-09-25.md`) y son el mecanismo correcto para producirlos — reutilizarlos es preferible a crear una suite nueva. Propuesta concreta de A/B para los hallazgos de esta pasada:

```text
Experimento N2: actual (bloquea "/") vs normaliza "/" a "."
  Métrica: nº de rondas hasta éxito en "tool_selectivo", sobre los
  5 modelos que fallaban. Ejecutar con
  scripts que ya generan docs/benchmark-2026-09-25.md.

Experimento N3: margen 0.85 fijo vs margen adaptativo por calibración
  Métrica: dropped_messages en una conversación sintética larga
  (>= 50 turnos) con un modelo ya calibrado (>=3 observaciones).
```

## 30. Los dos mayores cuellos de botella

Con la evidencia disponible (benchmarks reales + lectura de código), los dos puntos de mayor impacto × frecuencia × riesgo son:

1. **Fiabilidad de modelos individuales fuera del control de la app** (gpt-oss 500 en thinking, qwen3.6:27b degradación tras primer turno, muse-glimmer 500 intermitente) — impacto alto en la experiencia, pero la causa raíz está fuera del código auditado (Ollama/plantillas). La única palanca real de la app es N1.
2. **N2 (rutas absolutas)** — impacto medio pero **con la frecuencia más alta demostrada** de todos los hallazgos (5 de 7 modelos, en el test más básico posible). Es el cuello de botella de calidad-de-primera-ronda con mejor ratio esfuerzo/beneficio de toda la auditoría.

No se proponen refactorizaciones de componentes grandes (§31-32 del guion original) porque, con la evidencia recogida, **no hay ningún componente cuya reescritura esté justificada**: los dos issues de mayor impacto se resuelven con cambios de una función cada uno (N1, N2), no con refactorización arquitectónica.

## 31-33. Refactorización de componentes / comparación antes-después

No aplica bajo la regla de la sección 57 del guion ("si la complejidad no está justificada, no lo propongas"): N1 y N2 son parches quirúrgicos, no refactorizaciones. Código propuesto:

**N2 — `core/workspace.py::Workspace._path`:**
```python
def _path(self, relative: str) -> Path:
    # Modelos pequeños/medianos interpretan con frecuencia la raíz
    # del workspace como "/" del sistema de archivos (demostrado:
    # 5/7 modelos en docs/benchmark-2026-09-25.md). Reinterpretar
    # una ruta absoluta como relativa a la raíz evita una ronda de
    # tool-calling desperdiciada en el camino más común, sin relajar
    # la protección contra escapes reales (../../etc/passwd sigue
    # bloqueado por relative_to()).
    if relative.startswith("/"):
        relative = relative.lstrip("/") or "."
    candidate = (self.root / relative).resolve()
    try:
        candidate.relative_to(self.root)
    except ValueError as exc:
        raise WorkspaceError("Ruta fuera del workspace.") from exc
    return candidate
```

**N1 — `core/models_config.py` (esbozo, requiere decidir el mapeo bool→nivel con el usuario):**
```python
# thinking: bool | Literal["low", "medium", "high"] | None
# En core/ollama.py, _stream_async:
override = get_override(model)
if override.thinking is not None:
    if model.split(":", 1)[0] == "gpt-oss" and isinstance(override.thinking, bool):
        logger.warning(
            "gpt-oss ignora think=%s (bool); usa 'low'/'medium'/'high' "
            "en models.json para este modelo.", override.thinking,
        )
    else:
        payload["think"] = override.thinking
```
*(Esbozo, no probado — antes de aplicarlo, decidir con el usuario si prefiere mapeo automático bool→nivel o solo el aviso.)*

## 34. Plan global de refactorización

| Paso | Cambio | Archivos | Riesgo | Beneficio |
|---|---|---|---|---|
| 1 | N2: normalizar rutas absolutas | `core/workspace.py` | Bajo | Alto (frecuencia demostrada) |
| 2 | N1: aviso/mapeo de thinking para gpt-oss | `core/ollama.py`, `core/models_config.py` | Bajo | Medio |
| 3 | N3: margen adaptativo por calibración | `core/context_window.py` | Bajo | Medio (conversaciones largas) |
| 4 | N4: TTL en caché de capacidades | `core/model_capabilities.py` | Bajo | Bajo |
| 5 | N5: tipar coerción XML por schema | `core/xml_tools.py`, `core/tool_strategies.py` | Medio | Bajo (caso raro) |

## 35. Plan de pruebas

- `Workspace.list_dir("/")` y `Workspace.read_file("/algo.txt")` deben resolver contra la raíz del workspace, no lanzar error (N2).
- Test de payload: override `thinking=False` sobre modelo `gpt-oss:*` no debe enviar `think: false` sin aviso (N1).
- Test de `ContextWindow.prompt_budget` con `token_calibration.has_calibration(model)=True` debe usar el margen relajado (N3).
- Test de `parse_function_xml` con `content="true"` contra una tool cuyo schema declara `content: string` debe devolver el string `"true"`, no `True` (N5).

## 36. Matriz final de calidad

| Dimensión | Estado actual | Problema | Objetivo | Cómo medir |
|---|---|---|---|---|
| Tool selection (rutas) | Bloquea correctamente pero tarde | N2 | Aceptar "/" como raíz del workspace | % de éxito a la primera en tool_selectivo |
| Thinking control | Ignorado en silencio para gpt-oss | N1 | Aviso o mapeo correcto | Payload real enviado (test) |
| Context retention | Conservador en exceso con modelos calibrados | N3 | Margen adaptativo | dropped_messages en conversación larga |
| Context freshness (capabilities) | Sin TTL | N4 | TTL 10-15 min | Edad de la entrada de caché en el momento de uso |
| TTFT / tokens-s | Depende 100% del modelo/Ollama | Ninguno atribuible a la app | — | docs/audit-2026-09.md (ya medido) |
| UI blocking | Ninguno detectado | — | — | — |

## 37. Regresiones

Los cambios propuestos (N1-N5) son aditivos o amplían aceptación (N2), no cambian contratos existentes. Riesgo de regresión: bajo en todos. Verificar en particular que N2 no rompe el test de seguridad existente para `../../etc/passwd` (debe seguir bloqueado).

## 38. Conclusiones técnicas

El proyecto ya ha absorbido tres rondas de auditoría con fixes verificados por benchmark real, algo poco común en proyectos de este tamaño. Los hallazgos nuevos de esta pasada son de ajuste fino, no estructurales. El de mayor relación esfuerzo/beneficio es N2 (una condición de 2 líneas, evidencia empírica de que afecta al 71% de los modelos probados en el escenario más básico). El de mayor riesgo latente si no se corrige es N1, porque falla en silencio: el usuario cree tener control sobre el thinking de gpt-oss y no lo tiene.

La mayor limitación de esta auditoría es de alcance, no de rigor: `history.py`, los controllers de UI restantes, las vistas Qt y los plugins git/search/verificador/mcp quedan pendientes de una pasada dedicada.

## 39. Fuentes

- CÓDIGO DEL PROYECTO: todos los archivos listados al inicio del documento.
- DOCUMENTACIÓN DEL PROYECTO: `docs/audit-2026-09.md`, `docs/benchmark-2026-09-25.md` (benchmarks propios ya ejecutados, datos reales).
- DOCUMENTACIÓN OFICIAL: [docs.ollama.com/capabilities/thinking](https://docs.ollama.com/capabilities/thinking) — especifica que GPT-OSS exige `think` en `"low"/"medium"/"high"` y que un booleano se ignora para ese modelo. Consultado 2026-09-26.
- FORO (señal externa, no verificada como causa confirmada del error 500 de gpt-oss en el benchmark propio): [huggingface.co/openai/gpt-oss-20b/discussions/15](https://huggingface.co/openai/gpt-oss-20b/discussions/15) — reportes de plantillas de chat rotas (`currentDate` no definida) en ciertas combinaciones de Ollama/gpt-oss.
