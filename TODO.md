# TODO — Pendientes futuros

## Prioridad alta (próxima sesión)
- [ ] **Refactor del chat a widgets reales**
  Migrar de QTextEdit a QScrollArea + QVBoxLayout con widgets por mensaje.
  Permite: esquinas redondeadas de verdad, sombras, hover, imágenes
  embebidas, botones dentro de mensajes.
  Esfuerzo: 3-4 h.

## Prioridad media (cuando apetezca)
- [ ] **Migrar plugin de búsqueda a google-re2**
  Reemplaza `regex` + timeout por un motor con garantía de tiempo lineal.
  Elimina ReDoS por diseño, no por mitigación.
  - Wheels precompilados existen para macOS ARM64.
  - No soporta lookahead ni backreferences: mostrar error claro si el
    usuario escribe un patrón con esas features.
  - `\Z` de Python → `\z` en RE2.
  - Flags con `re2.Options()` en lugar de constantes.
  - Fallback a `regex` si `re2` no está instalado (opcional).
  Esfuerzo: 1-2 h.

- [ ] **Evaluar Outlines o XGrammar** para casos donde necesites JSON
  estructurado garantizado (memoria a largo plazo, automatización).
  No para tool calling (ver abajo).
  Esfuerzo: 1-2 días de investigación + integración.

## Prioridad baja (no urgente, documentado)
- [ ] **Structured outputs de Ollama para tool calling**: NO VIABLE hoy.
  Motivo: bug conocido de Ollama self-hosted (#13750). Cuando pasas
  `format` y `tools` en la misma petición, el modelo se fuerza a
  generar JSON y NUNCA considera las herramientas. Funciona en
  Ollama.com (cloud) pero no en local.
  Alternativas si algún día se resuelve:
    a) Dos fases: llamada sin format para que el modelo decida, y
       segunda llamada con format para forzar JSON válido en args.
       Coste: duplica latencia.
    b) Migrar a Outlines/XGrammar (integración con logits, no con la
       API de Ollama).
  Mientras el bug no se resuelva, el prompt-guided XML actual es lo
  que usan ZeroClaw, Koi y Hermes Agent. Mantenerlo.

## Ideas para más adelante
- [ ] Ampliar el parser XML para soportar más formatos (como hace Koi:
      `<function=name>`, `<action tool=...>`, JSON-in-markdown, etc.).
- [ ] Memoria a largo plazo (resumen persistente + embeddings).
- [ ] Automatización (tareas en bucle sin intervención).
- [ ] Benchmarks (matriz de modelos × velocidad × calidad).
- [ ] Registro de plugins externos con UI de activación/desactivación.
- [ ] Documentación pública de cómo hacer un plugin.

## Notas de seguridad (ya aplicadas, no revertir)
- `command` y `args` de servidores MCP vienen SOLO de
  `mcp_servers.json`. Nunca de la UI ni del modelo. Ver docstring de
  `core/mcp_servers.py`.
- Tool results en modo XML llevan prefijo `[TOOL_RESULT:name]` para
  que `_last_user_text` los ignore. Salvaguarda frente a confused
  deputy. Ver `tests/test_confused_deputy.py`.
- Diálogo de confirmación de shell escapa el comando con
  `html.escape` + `PlainText`. Mitigación anti-spoofing.
- Git `show(ref)` valida ref contra `_REF_PATTERN` (primer carácter
  alfanumérico, sin flags). Bloquea `--output=/tmp/x`.
- Workspace rechaza symlinks que escapan del root. Ver
  `tests/test_workspace.py`.
## Auditoría 2026-09 — fase de performance

### Aplicado
- **Fase 2.2 — Trocear Markdown durante streaming.**
  `PlainTextRenderer.on_text` ahora renderiza a Markdown cuando el
  segmento cruza una frontera de párrafo o cierra un bloque de
  código, en lugar de acumular todo y convertirlo de golpe en
  `final_text`.
  Medición: `code final` 26.6 ms → 0.1 ms (−99.6 %). Pico de
  congelación al cerrar la respuesta eliminado. Coste total +11 %
  (repartido durante el streaming).

### Evaluado y descartado
- **Fase 2.1 — `setLayoutEnabled(False)` + `beginEditBlock`.**
  Medición: prose accum 145 → 154 ms, code accum 12 → 21 ms.
  El patrón real de ChatPerezoso es 1 mutación por drain (30 veces
  por segundo), no múltiples mutaciones seguidas. Deshabilitar el
  layout en cada drain es overhead puro.
  Revertido. No reintentar sin cambiar el patrón de streaming.

---

## Auditoría 2026-09 — estado

Auditoría completa en `docs/audit-2026-09.md`. Resumen:

**Aplicado:**
- Fase 1: race `AsyncRunner`, mutación system prompt, señal muerta,
  bloqueo `_refresh_capabilities`.
- Fase 2.2: trocear Markdown durante streaming (−99.6% en el pico de
  render al cerrar respuesta).
- Fase 3: `ToolIntentGate` sin registro global mutable.
- Fase 4: `__del__` con close_callback, sidebar busy, guard en
  `_render_markdown_block`.

**Descartado con datos:**
- Fase 2.1 (`setLayoutEnabled`): empeora el rendimiento en el patrón
  real de la app. No reintentar sin cambiar el patrón de streaming.

**Nuevos pendientes que aparecieron durante la auditoría:**
- [ ] Comprobar si `granite4.1:3b` es un buen modelo por defecto para
      usuarios con poca RAM. `gemma4:12b` da TTFT 85 s si el sistema
      está cargado (presión de memoria).
- [ ] Evaluar timeouts adaptativos: extender el `read` de httpx si el
      stream sigue produciendo datos. Hoy es fijo a 300 s.
- [ ] Benchmark de MikeVeerman si el repo existe: `tool-calling-benchmark`.
      El benchmark propio del `ToolIntentGate` (`scripts/benchmark_intent_gate.py`)
      cubre el 80% del valor.

### Pendientes descartados en la continuación

- **Timeouts adaptativos.** Medido con `qwen3:14b` thinking ON:
  peor TTFT = 42 s (carga en frío). `httpx.Timeout(read=300)` es
  por lectura, no total. No hay caso donde se dispare. Si aparece
  un TTFT > 300 s, subir `read` a 600 s en `core/ollama.py` es una
  línea. No hace falta diseño adaptativo.

- **Benchmark de MikeVeerman.** El repo no existe con ese nombre.
  Usar `scripts/benchmark_intent_gate.py` como referencia.


## Cierre de sesión 2026-09 — pendientes anotados

- [ ] Verificación visual del plugin verificador en la app real (niveles 2 y 3 con ruff/mypy).
- [ ] Candidatos para próximas sesiones: `snapshot_workspace` (red de seguridad), `ejecutar_pruebas` (test runner), `memoria_proyecto` (contexto persistente).
- [ ] Migración QTextEdit -> QScrollArea + widgets: sin urgencia tras Fase 2.2.

### P1 pendiente — intent gate

`buscar_en_workspace` autoriza preguntas generales con "dónde"
(por ejemplo "¿dónde está la capital de Asturias?"), porque
`requires_target=False` para todos los verbos. Revertido el intento
de fix porque `requires_target=True` rompe casos legítimos
(`encuentra 'def test_'`, `encuentra los TODO`).

Diseño pendiente: campo `IntentRule.verbs_strong` (verbos de acción
que siempre autorizan: busca, encuentra) vs `verbs_weak` (verbos de
pregunta que requieren target: dónde). Estimado: 1-2h.

## Proyecto: sistema de etiquetas XML para modelos sin tool calling nativo

**Motivación**: 3 de 5 modelos probados (granite, rnj-1, qwen3-coder:30b en
contexto largo) no emiten tool calls nativas fiables. El fallback XML actual
se quitó porque los modelos que fallan tampoco escriben XML coherente cuando
se les pide. Pero hay toda una familia de proyectos (OpenLovable, Bolt.diy,
Claude Artifacts, Aider, Chisel) que resuelven esto con un **cambio de
paradigma**: el modelo nunca intenta tool calling, solo escribe texto con
etiquetas personalizadas, y la app las parsea en streaming.

### Diferencia con lo que ya probamos

| Fallback XML antiguo | Sistema de etiquetas |
|----------------------|----------------------|
| El modelo intenta tool nativo primero | El modelo solo escribe texto |
| Si falla, reinterpretamos su output | No hay tool calling, solo etiquetas |
| El modelo está "confundido" entre modos | Un único modo: texto con etiquetas |

### Diseño propuesto

Modo dual en `core/ollama.py`:
- **Nativo** (actual): para `gpt-oss:20b` y modelos con tool calling fiable.
- **Etiquetas** (nuevo): para el resto. No se envían `tools` al payload.
  El system prompt pide formato XML y el parser lo convierte a acciones.

Formato propuesto:

## Sprint modelos 2026-09 (cerrado)

Resultados OVERPAPER (9 fases):
- gpt-oss:20b      9/9  ✅
- ministral-3      8.5/9 ✅
- muse-glimmer     6/9  ⚠️ prefill en Air
- gemma4:12b       5/9  ⚠️ prefill en Air
- qwen3-coder:30b  3/9  ❌ pierde tool calling
- granite, rnj-1   0/9  ❌ no tool calling

Stack final: 7 modelos, cada uno con rol.
Recomendación: gpt-oss:20b para multi-fase, ministral-3 para rápido.

## Próximo objetivo

**Orquestación determinista de prompts con fases** (3-4h).
Detectar "FASE N" en el prompt y trocear en conversaciones
independientes. Cada fase recibe solo su prompt (800 tokens)
+ estado mínimo del workspace. Resuelve el prefill killer
en Air y desbloquea muse-glimmer, gemma4 y mistral-small3.2
para tareas largas. Ya implementado parcialmente: la cola
pausable de `feat-queue-pause` es la base.

### Mitigación de "falso completado"

Detectado con mistral-small3.2: el modelo llama a `ejecutar_comando`
sin `command` (falla la tool) y luego declara "FASE VERIFICADA" y
"PROYECTO COMPLETADO" sin haber llamado a `escribir_archivo`.

El stall guard no lo detecta porque el modelo sí emite tool calls.

Mitigación propuesta (30 min): si `ejecutar_comando` se invoca sin
que haya habido un `escribir_archivo` exitoso en el mismo turno,
bloquear con mensaje: "no has escrito el archivo, hazlo primero".

Integrar en la orquestación determinista (próxima sesión) que ya
verifica externamente el estado del workspace entre fases.