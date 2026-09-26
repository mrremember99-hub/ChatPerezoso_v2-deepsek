cat > README.md << 'BLOQUE1'
# ChatPerezoso v2

Cliente de escritorio para Ollama. PySide6 + httpx + asyncio. Núcleo pequeño, plugins todo lo demás. Proyecto personal, en español, un solo desarrollador.

## Arranque rápido

### macOS / Linux
git clone <repo> chatperezoso
cd chatperezoso
./setup.sh --mcp
source .venv/bin/activate
python bootstrap.py

text

### Desarrollo
pip install -e ".[dev]"

text

El extra `dev` instala `ruff`, `mypy`, `pytest`, `pytest-qt`, `pytest-timeout`.

## Stack

| Componente | Versión | Notas |
|---|---|---|
| Python | ≥ 3.11 | 3.12.14 en desarrollo |
| PySide6 | ≥ 6.6 | Última estable: 6.11.1 (mayo 2026) |
| httpx | ≥ 0.27 | Cliente HTTP async |
| psutil | ≥ 5.9 | Métricas de sistema |
| regex | ≥ 2024.5.15 | Fallback de búsqueda |
| google-re2 | ≥ 1.1 | Motor preferido (lineal, sin ReDoS) |
| markdown | ≥ 3.5 | Render de respuestas |
| pygments | ≥ 2.17 | Resaltado de código |
| mcp | ≥ 2,<3 | Opcional, plugin MCP |

## Arquitectura
chatperezoso/
├── core/ # Lógica sin Qt: Ollama, tools, agentes, historial, contexto
│ ├── ollama.py # Cliente Ollama + bucle de tool calling
│ ├── tools.py # Registro de herramientas
│ ├── tool_result.py # Dataclass ToolResult
│ ├── tool_strategies.py # Estrategias nativa y XML
│ ├── tool_provider.py # Abstracción de providers
│ ├── composite_tools.py # Composición de providers
│ ├── intent.py # ToolIntentGate
│ ├── context_window.py # Presupuesto de contexto y poda
│ ├── history.py # Persistencia de conversación (JSON)
│ ├── agents.py # Agentes
│ ├── config.py # Configuración
│ ├── workspace.py # Workspace aislado
│ ├── models_config.py # Overrides + VERIFIED_TOOL_MODELS
│ ├── prompt_phases.py # Orquestación determinista (FASE N)
│ └── workspace_snapshot.py
├── ui/
│ ├── views/ # Widgets puros
│ ├── controllers/ # Lógica de UI
│ ├── rendering/ # Renderers inyectables (Protocol ChatRenderer)
│ └── workers.py # QThreads: ChatWorker, ModelWorker, CapabilitiesWorker
├── plugins/
│ ├── git/ # Git solo lectura (status/diff/log/show)
│ ├── search/ # Búsqueda en workspace
│ ├── shell/ # Comandos con confirmación
│ ├── verificador/ # Verificación post-escritura
│ └── mcp/ # Adaptador MCP (server-filesystem)
├── scripts/ # Benchmarks y utilidades
├── tests/ # 755 passed, 4 skipped
└── docs/ # Auditorías y notas técnicas

text

**Principio**: el núcleo debe ser pequeño, funcional y robusto. Todo lo que no sea imprescindible para hablar con Ollama y usar herramientas pertenece a plugins.
BLOQUE1
wc -l README.md
tail -3 README.md
Esperado: ~85 líneas, terminando con "…pertenece a plugins.".

Bloque 2 — añade el resto:

bash
cat >> README.md << 'BLOQUE2'

## Features

### Orquestación determinista de prompts (2026-09-25)

Si el usuario pega un prompt con `FASE 1`, `FASE 2`, ... `FASE N`, la app lo trocea automáticamente en N conversaciones independientes. Cada fase recibe:

- El **preamble** (reglas globales, lo que va antes de FASE 1).
- El **cuerpo** de su propia fase.
- Un **snapshot fresco del workspace** en el momento de enviarse.

**Implementación**: `core/prompt_phases.py`, `core/workspace_snapshot.py`, `ChatController.send_user_input()`, `ChatController._advance_queue()`.

**Resultados OVERPAPER (9 fases)**:

| Modelo | Resultado |
|---|---|
| `gpt-oss:20b` | 9/9 ✅ (más rápido que sin orquestación) |
| `ministral-3` | 8.5/9 ✅ |
| `muse-glimmer` (eliminado) | 6/9 ⚠️ (falla en Fase 6, output grande) |
| `gemma4:12b` | 5/9 ⚠️ (timeout prefill) |
| `qwen3-coder:30b`, `granite`, `rnj-1` | 0-3/9 ❌ |

**Conclusión**: la orquestación resuelve el prefill del prompt global. NO resuelve fases que reescriben archivos grandes. Para eso haría falta edición por diff (proyecto aparte).

### Tool trace persistente (2026-09-25)

El resumen de las tools ejecutadas en el turno anterior se inyecta al system prompt del siguiente turno como bloque `[ACCIONES DEL TURNO ANTERIOR]`. Sin esto, el modelo no sabe qué archivos tocó o qué comandos ejecutó antes.

Formato:
[ACCIONES DEL TURNO ANTERIOR]
escribir_archivo(gui.py) -> ok
ejecutar_comando(python -m py_compile gui.py) -> ok
Estas acciones YA se ejecutaron en el turno anterior. No las repitas sin motivo.

text

**Implementación**: `_build_tool_trace()` + `_format_tool_arg_hint()` en `ChatController`. Los argumentos se guardan en `ToolResult.metadata["arguments"]` desde `workers.py`. El historial persistente queda limpio (solo `role`/`content`).

### Detección temprana de modelos sin tool calling (2026-09-25)

Contador de fallos consecutivos de tool calling textual. Tras 2 fallos con un modelo no verificado, se muestra un diálogo modal con un combo de modelos verificados y opción de cambiar en caliente.

**Modelos verificados** (`VERIFIED_TOOL_MODELS` en `core/models_config.py`): `gpt-oss:20b`, `ministral-3`. Comparación sin tag (`is_verified_tool_model()`).

### Cola de prompts

- **Enviar todo**: parte por separadores `---` / `===`. Si detecta fases (`FASE N`), delega en la orquestación determinista.
- **Pausable**: si una fase falla, la cola se pausa y el usuario decide reintentar, saltar o cancelar.
- El panel derecho muestra el progreso (`1/9`, `2/9`, ...).

### Otras features

- **Streaming incremental** con troceo de Markdown durante la respuesta.
- **Tool calling nativo** y **fallback XML** prompt-guided.
- **ToolIntentGate**: barrera de intención que filtra tools según la petición.
- **MCP** (opcional): server-filesystem del workspace, desactivado por defecto.
- **Agentes**: system prompt, temperatura, `num_ctx` y tools permitidas por agente.
- **Historial**: persistencia entre sesiones en `history.json`.
- **Diagnósticos**: panel con estadísticas de sesión en tiempo real.
- **Verificador**: niveles de verificación post-escritura (sintaxis, secretos, conflictos, ruff, mypy).
BLOQUE2
wc -l README.md
tail -3 README.md
Esperado: ~150 líneas, terminando con "…ruff, mypy).".

Bloque 3 — features finales y modelos:

bash
cat >> README.md << 'BLOQUE3'

## Modelos

### Stack actual

| Modelo | Tamaño | Rol | Benchmark |
|---|---|---|---|
| `gpt-oss:20b` | 13 GB | Principal, multi-fase | 5/5 · 59.5s |
| `ministral-3:latest` | 6.0 GB | Secundario, código ligero | 5/5 · 54.6s |
| `mistral-small3.2:latest` | 15 GB | Contexto largo, verificación | 5/5 · 281.9s |
| `ornith-1.5:9b` | 6.6 GB | Alternativa ligera | 5/5 · 58.2s |
| `qwen3:1.7b` | 1.4 GB | Chat rápido, sin pensar | 5/5 · 18.7s |

### Novedades 2026 (búsqueda web)

**Tool calling en Ollama** — modelos con soporte nativo fiable: `qwen3` (todas las tallas), `gpt-oss:20b`, `llama3.1:8b`, `mistral-small3.2`, `gemma4` (≥ 0.20.0). Evitar `qwen3.5` (tool calling roto en marzo 2026, PR #14603). Evitar `qwen3.6:27b` (falla con tools en esta version de Ollama).

**Benchmark de modelos pequeños** (MikeVeerman/tool-calling-benchmark, 2026):
- 🥇 `qwen3:1.7b` — 0.960 Agent Score.
- ⚡ `lfm2.5:1.2b` — 0.920, 1,567 ms (7× más rápido).
- El conteo de parámetros es mal predictor: `qwen3:0.6b` (0.880) supera a `phi4-mini` (3.8B, 0.780).
- La mayoría de modelos <4B fallan en juicio (cuándo NO llamar a una tool).

**Context length en Ollama** (docs.ollama.com, 2026): por defecto, <24 GB VRAM → 4k tokens; 24-48 GB → 32k; ≥48 GB → 256k. Para agentes, coding y web search: mínimo 64000 tokens. Configurar con `OLLAMA_CONTEXT_LENGTH=64000 ollama serve` o `PARAMETER num_ctx` en Modelfile.

**MCP 2026-07-28** — mayor revisión desde el lanzamiento:
- **Stateless core**: se elimina el handshake `initialize` y `Mcp-Session-Id`. Cada request lleva su versión de protocolo y capacidades en `_meta`.
- `server/discover`: los servidores deben implementarlo para anunciar versiones.
- `subscriptions/listen`: stream único de notificaciones server-to-client.
- Se gradúan extensiones oficiales: interfaces interactivas server-rendered y tareas asíncronas de larga duración.

**PySide6 6.11** (marzo 2026): Python ≥ 3.10. Emisión de señales optimizada. Nuevo módulo `QtCanvasPainter`. Opciones de `pyside6-uic` y `pyside6-rcc` configurables en `pyproject.toml`.
BLOQUE3
wc -l README.md
tail -3 README.md
Esperado: ~180 líneas.

Bloque 4 — cierre:

bash
cat >> README.md << 'BLOQUE4'

## MCP: cuándo ayuda y cuándo estorba

MCP viene **desactivado por defecto**. Cuando se activa, oculta las herramientas nativas que reemplaza y el modelo opera con nombres largos (`mcp__fs__read_text_file`).

**Funciona mal con modelos ≤ 8B** por combinación de: vocabulario mezclado, nombres largos, semántica distinta, paths absolutos, latencia de subproceso Node (50-200 ms por tool call), confirmaciones extra y system prompt más grande.

| Modelo | MCP off | MCP on |
|---|---|---|
| `granite4.1:3b` | OK | NO |
| `ministral-3:latest` | OK | NO |
| `llama3.1:latest` (8B) | OK | A veces |
| `qwen3:14b` | OK | OK |
| `gpt-oss:20b` | OK | OK |
| `qwen3-coder:30b` | OK | OK |

**Decisión pendiente**: opción A — documentar y avisar (tooltip en el botón MCP).

## Auditoría 2026-09

Documento completo: `docs/audit-2026-09.md`.

**Métricas**: 506 → 523 tests (+17). Pico de render en code final: 26.6 ms → 0.1 ms (−99.6 %). Streaming warm TTFT: 0.24 s (`granite4.1:3b`).

**Hallazgos aplicados**: race `AsyncRunner.submit/close` (crítico); `_inject_system_prompts` mutaba dict del llamante (alto); señal muerta `ChatWorker.text` (medio); coste de render en streaming (alto); `ToolIntentGate._RULES_REGISTRY` global mutable (medio); `AsyncRunner.__del__` sin `close_callback` (medio); cambio de agente durante streaming (bajo); guard en `_render_markdown_block` (bajo).

**Descartado con datos**: Fase 2.1 (`setLayoutEnabled(False)` + `beginEditBlock`); timeouts adaptativos (`httpx.Timeout(read=300)` es por lectura, no total).

## Migración del renderer (posible, no urgente)

Estado: fase de diseño. Posponer tras Fase 2.2.

`PlainTextRenderer` gestiona un único `QTextEdit`. El coste de layout crece con el tamaño total del documento. Arquitectura objetivo: `QScrollArea` + `QVBoxLayout` con un `QTextBrowser` por mensaje. Criterio de éxito: el tiempo de añadir un mensaje nuevo no debe crecer con el número de mensajes previos.

## Tests
pytest -q # 755 passed, 4 skipped
pytest -q tests/test_X.py # Un archivo
pytest -x # Parar al primer fallo

text

## Pendientes

### Prioridad alta

- [ ] Verificación visual del plugin verificador (niveles 2 y 3 con ruff/mypy).
- [ ] Refactor del chat a widgets reales (3-4 h, pospuesto).

### Prioridad media

- [ ] Migrar plugin de búsqueda a google-re2 (1-2 h).
- [ ] Evaluar Outlines o XGrammar (1-2 días).
- [ ] Herramientas: `snapshot_workspace`, `ejecutar_pruebas`, `memoria_proyecto`.

### Prioridad baja

- [ ] Structured outputs de Ollama para tool calling: no viable hoy (bug #13750).
- [ ] Ampliar parser XML (estilo Koi).
- [ ] Memoria a largo plazo, automatización, benchmarks.
- [ ] Registro de plugins externos con UI.
- [ ] Documentación pública de cómo hacer un plugin.

### Mitigación pendiente: "falso completado"

Detectado con `mistral-small3.2`: llama a `ejecutar_comando` sin `command` (falla) y declara "FASE VERIFICADA" sin haber escrito el archivo. Mitigación propuesta (30 min): si `ejecutar_comando` se invoca sin que haya habido un `escribir_archivo` exitoso en el mismo turno, bloquear con mensaje.

## Notas de seguridad (aplicadas, no revertir)

- `command` y `args` de servidores MCP vienen SOLO de `mcp_servers.json`.
- Tool results en modo XML llevan prefijo `[TOOL_RESULT:name]`.
- Diálogo de confirmación de shell escapa el comando con `html.escape` + `PlainText`.
- Git `show(ref)` valida ref contra `_REF_PATTERN`.
- Workspace rechaza symlinks que escapan del root.

## Cómo trabajamos

- Todo en español.
- Leer el código real antes de proponer parches.
- Tests con `pytest -q`.
- Commits con tags: `feat-X`, `fix-X`.
- El usuario decide el diseño. El asistente propone.
- **Terminal**: los heredocs `python3 - <<'PYEOF'` se rompen al pegar. Usar `python3 -c "..."` de una línea o scripts `apply_patch.py` que verifiquen el reemplazo.
- **Scripts de parche**: el check de "ya presente" debe buscar
  `def nombre` o un marcador único, NUNCA el nombre suelto del
  símbolo. El propio parche puede insertar el símbolo antes del
  check (referencias, condiciones), causando falso skip.

## Historial de sesiones recientes

### 2026-09-25
- `697b4ae` — fix(queue): 'Enviar todo' delega en orquestación determinista.
- `46ae075` — feat(trace): tool trace persistente en system prompt.
- `eea11ce` — feat(tools): detección temprana de modelos sin tool calling.
- `a4090f7` — fix(queue): limpiar widgets del panel al reemplazar la cola.
- Tests: 733 → 755 passed, 4 skipped.

### 2026-09-24
- `8fd69b4` — fix(agents): preservar `top_p`/`top_k`/`repeat_penalty`.
- `a4809c1` — fix(context): marcador visible al model cuando se poda el historial.
- `81cc17e` — fix(stream): cancelar buffering JSON cuando es prosa.
- `61f4f8f` — fix(intent): `weak_verbs` para 'dónde' sin falsos positivos.
- `1185730` — feat(orquestación): troceo determinista de prompts multi-fase.
- `33ebfca` — docs(todo): cerrar orquestación determinista.

## Estado del proyecto

- **Rama**: `main`
- **Tests**: 755 passed, 4 skipped
- **Árbol**: limpio
- **Bundles de seguridad**: `../chatperezoso-2026-09-25.bundle` (y 23, 24)

---
## Deuda técnica conocida

Símbolos detectados como nunca usados por `vulture`, no eliminados por
relación riesgo/beneficio. Son ~100 líneas cosméticas. Si algún día se
borran, hacerlo a mano con `pytest -q` entre medias.

- `core/async_runner.py` — `is_cancelled_error()`
- `core/context_window.py` — `estimate_request()`
- `core/ollama.py` — `force_close_active()`
- `core/tool_strategies.py` — `ToolCallingStrategy` (Protocol huérfano)
- `core/token_calibration.py` — `last_actual` (asignado, nunca leído)
- `ui/controllers/chat_controller.py` — `MIN_TURNS_TO_KEEP`, `error_message` (Signal), `has_queue()`, `is_queue_paused()`
- `ui/controllers/mcp_controller.py` — `pending_ids` (property)
- `ui/rendering/plain_text.py` — `_code_fence_lang` (asignado, nunca leído)
- `ui/views/chat_panel.py` — `set_streaming()`
- `ui/views/right_panel.py` — `hide_queue_paused()`
- `ui/views/sidebar.py` — `is_auto_approve()`, `is_verificador()`

Herramienta usada: `vulture core/ ui/ plugins/ scripts/ tests/ --min-confidence 60`.

## Bugs identificados en el test OVERPAPER (2026-09-26)

Del test end-to-end con prompt OVERPAPER 9 fases + gpt-oss:20b:

### P1 — Gate bloquea confirmaciones conversacionales

`ToolIntentGate` no reconoce "sí", "vale", "ok", "adelante", "hazlo"
como respuestas afirmativas a una pregunta del modelo. Si el modelo
pregunta "¿puedo leer gui.py?" y el usuario responde "sí", el gate
no autoriza `leer_archivo` y el modelo repite la pregunta.

**Fix propuesto**: en `ToolIntentGate.tool_is_requested()`, si el
último mensaje del modelo (no solo el del usuario) menciona una tool
concreta y el mensaje actual del usuario es una confirmación corta
("sí", "vale", "ok", "adelante", "hazlo", "confirma"), autorizar esa
tool. Requiere que el gate tenga acceso al último assistant message.

**Test**: modelo pregunta "¿leo x.py?", usuario responde "sí", se
autoriza `leer_archivo("x.py")`.

### P2 — Orquestación no resetea historial entre fases

`_advance_queue()` regenera el prompt de la fase con snapshot fresco,
pero NO limpia `self.messages`. Cada fase arrastra el historial
completo de las anteriores (user + assistant + tool_results). Fase 8
recibía ~14000 tokens de prompt cuando solo necesitaba ~2000.

**Fix propuesto**: en `_advance_queue()`, guardar referencia al
historial antes de la primera fase, y en cada avance hacer
`self.messages = []` + reinsertar el `user` de la fase actual.
Mantener aparte el render visual (las fases anteriores se ven en
el chat, pero no se reenvían al modelo).

**Test**: prompt de 3 fases, verificar que `len(self.messages)` al
inicio de la fase 2 es 1 (solo el user actual).

### Mitigación aplicada (no fix de fondo)

`core/ollama.py::_stream_async` topa `num_ctx` a 32k para no forzar
KV cache gigante en modelos con context_length alto (gpt-oss:
131072 → ~26 GB KV, no cabe en M4 Air).

## Feature pendiente — Editar la cola de mensajes

Con la orquestación determinista, el usuario puede querer:

- **Editar un prompt pendiente** antes de que se envíe (por ejemplo,
  reescribir la Fase 5 sin tener que cancelar y reenviar el prompt
  completo).
- **Eliminar un item** de la cola.
- **Reordenar** (mover arriba/abajo).

**Diseño propuesto**:
- Click derecho en un `QueueRow` → menú contextual con Editar /
  Eliminar / Subir / Bajar.
- Diálogo modal con `QPlainTextEdit` para editar.
- `chat_controller` recibe señales nuevas: `queue_edit_item(idx, text)`,
  `queue_remove_item(idx)`, `queue_move_item(idx, delta)`.
- Mantener sincronizados `_queue`, `_phase_bodies`, `_queue_total`,
  y el `_queue_rows` de la UI.

**Complejidad**: media. Toca `right_panel.py` (QueueRow) +
`chat_controller.py` (estado de cola) + `app_controller.py` (wiring).

**Test**: mock de cola con 3 items, editar el segundo, verificar
que `_queue[1]` y `_phase_bodies[1]` cambian coherentemente.
## Feature pendiente — Editar la cola de mensajes

Con la orquestación determinista, el usuario puede querer:

- **Editar un prompt pendiente** antes de que se envíe.
- **Eliminar un item** de la cola.
- **Reordenar** (mover arriba/abajo).

**Diseño propuesto**:
- Click derecho en un `QueueRow` → menú contextual con Editar /
  Eliminar / Subir / Bajar.
- Diálogo modal con `QPlainTextEdit` para editar.
- `chat_controller` recibe señales: `queue_edit_item(idx, text)`,
  `queue_remove_item(idx)`, `queue_move_item(idx, delta)`.
- Mantener sincronizados `_queue`, `_phase_bodies`, `_queue_total`,
  y `_queue_rows` de la UI.

**Complejidad**: media. Toca `right_panel.py` + `chat_controller.py`
+ `app_controller.py`.

**Test**: mock de cola con 3 items, editar el segundo, verificar
coherencia entre `_queue[1]` y `_phase_bodies[1]`.

## Feature pendiente — Editar la cola de mensajes

Con la orquestación determinista, el usuario puede querer editar un
prompt pendiente, eliminar un item, o reordenar la cola.

**Diseño**: click derecho en un QueueRow → menú contextual con
Editar / Eliminar / Subir / Bajar. Diálogo modal con QPlainTextEdit.
Señales nuevas en chat_controller: queue_edit_item,
queue_remove_item, queue_move_item. Mantener sincronizados _queue,
_phase_bodies, _queue_total y _queue_rows.
