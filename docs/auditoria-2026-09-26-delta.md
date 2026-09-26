# Auditoría delta — ChatPerezoso_v2 (2026-09-26, pasada 4)

**Estado: CERRADA al 100% (D1-D7 + N4) — 2026-09-26 pasada 5.**

Ámbito: cambios reales desde la última pasada registrada en memoria (commits `8d3fd88`..`1e1b1ac`, 29 commits, más cambios sin commitear en `ui/controllers/app_controller.py`). No se repite lo ya auditado en `docs/auditoria-2026-09-26-completa.md`; esta pasada se centra en el delta y confirma/actualiza hallazgos previos donde aplica.

Archivos con cambio sustancial en este delta: `core/ollama.py`, `core/session_summary.py` (nuevo), `core/tools.py`, `core/workspace.py`, `core/model_capabilities.py`, `ui/controllers/app_controller.py`, `ui/controllers/chat_controller.py`, `ui/views/sidebar.py`, más 10 archivos de test nuevos.

**NO AUDITABLE EN ESTA PASADA** (sin cambios detectados o no releídos): `core/history.py`, `ui/controllers/{agent,diagnostics,mcp,model}_controller.py`, `ui/views/*` salvo `sidebar.py` (solo diff, no archivo completo), `ui/widgets.py`, `ui/diagnostics.py`, `plugins/{git,mcp,search,verificador}/*`, contenido de `tests/*` (solo confirmada su existencia por el diffstat).

---

## 1. Resumen ejecutivo

| # | Hallazgo | Severidad | Evidencia |
|---|---|---|---|
| D1 | Resumen de sesión bloquea el hilo de UI de forma síncrona en `send()` | **ALTO** | DEMOSTRADO |
| D2 | "Resumen rolling" deja de actualizarse tras 2 ciclos mientras se sigue inyectando | **ALTO** | DEMOSTRADO |
| D3 | Coste del resumen crece sin cota con el tamaño total del historial | MEDIO | DEMOSTRADO |
| D4 | `@Slot` eliminados en conexiones cross-thread (`_on_capabilities_ready`, `_on_mcp_servers_changed`) | MEDIO | DEMOSTRADO (cambio) / POSIBLE (impacto) |
| D5 | `OllamaClient` accede a un método privado (`_is_short_confirmation`) de `ToolIntentGate` | MEDIO | DEMOSTRADO |
| D6 | Modelo de resumen (`qwen3:1.7b`) hardcodeado sin comprobar disponibilidad | **RESUELTO** | `86ce889` |
| D7 | Posible pérdida silenciosa de argumento por colisión de alias con mismo nombre canónico en una misma llamada | **RESUELTO** | `fdff32d` |
| — | `ui/controllers/app_controller.py` tiene cambios sin commitear | INFO | DEMOSTRADO |
| — | N4 (caché de capabilities sin TTL, pasada anterior) | **RESUELTO** | DEMOSTRADO |

La prioridad #1 del protocolo (calidad de respuesta) se ve afectada directamente por D2/D3: el mecanismo nuevo pensado para mejorar conversaciones largas (resumen rolling) puede degradar la calidad pasados ~40 mensajes al inyectar contexto congelado/obsoleto, y su coste de latencia (D1) es peor cuanto más larga es la conversación — justo el escenario que debía mejorar.

---

## 2. Hallazgos con evidencia

### D1 — UI bloqueada por el resumen de sesión (ALTO)

**RESUELTO en `4462440`.** El cálculo del resumen se movió al `ChatWorker`: se ejecuta al final del turno, en su hilo, y emite `summary_ready(raw, new_index)`. `send()` ya no llama al modelo; solo construye el prompt (puro). Además se cierra el hueco de 6 mensajes entre ciclos (`keep_recent=0` en modo incremental) y `_reset_phase_history` resetea el resumen entre fases.

**Archivo:** `ui/controllers/chat_controller.py`, método `send()` (~línea 583) y `_maybe_update_summary()` (~línea 523).

**Cadena de evidencia:**
- `ChatController.send()` se invoca desde `AppController._on_message_submitted`, un `@Slot()` normal — se ejecuta en el hilo de UI, sin `QThread`.
- Dentro de `send()`, antes de `_spawn_worker()` (que sí delega a un hilo aparte), se llama `self._maybe_update_summary()` de forma síncrona.
- `_maybe_update_summary()` invoca `self.client.chat(...)` — llamada HTTP bloqueante. El propio comentario del código lo admite: *"puede tardar 1-2s con qwen3:1.7b"*.
- Se dispara cada 20 mensajes nuevos (`should_update`), hasta 2 veces por conversación.

**Impacto:** justo en el instante en que el usuario pulsa enviar, la UI se congela el tiempo que tarde el modelo de resumen en responder. Si `qwen3:1.7b` no está cargado en memoria por Ollama (cold start) el bloqueo puede ser bastante mayor a 1-2s. Rompe el patrón establecido en el resto de la aplicación, donde todo el trabajo de red pasa por `ChatWorker`/`QThread`.

**Solución propuesta:** ejecutar `_maybe_update_summary()` en un `QThread` (o en el propio `ChatWorker` del turno siguiente, aplicando el resultado al turno N+1 en vez de al N), de forma que nunca bloquee el turno que el usuario está esperando.

**Verificación:** medir con timers el tiempo entre clic de "enviar" y aparición de "Generando…"; test con `client.chat` mockeado a `time.sleep(5)` y comprobar que la UI sigue respondiendo (no debe ser reproducible sin ese mock: no hay test nuevo de los 10 añadidos que cubra este bloqueo).

---

### D2 — El resumen "rolling" no es rolling (ALTO)

**RESUELTO en `520b723`.** `should_update` sin `max_cycles`; `build_summary_prompt` con `since_index` + `previous_summary` (incremental). 6 tests nuevos.

**Archivo:** `core/session_summary.py`.

**Evidencia:**
- `SessionSummary.should_update(..., max_cycles=2)`: a partir del segundo ciclo (40 mensajes nuevos) devuelve `False` para siempre — el resumen deja de regenerarse.
- `chat_controller.send()` sigue inyectando `summary.text` en el system prompt de **todos** los turnos posteriores, sin marcar que está obsoleto.
- `build_summary_prompt(messages, keep_recent=6)` no recibe el resumen anterior como parámetro: cada regeneración relee **todos** los mensajes salvo los últimos 6 desde cero, no solo lo nuevo desde el último resumen.

**Impacto:** en conversaciones largas —exactamente el escenario que el protocolo pide probar explícitamente (50+ turnos, sección 38)— el bloque `[RESUMEN DE LA SESIÓN]` que se envía al modelo queda congelado en el estado del mensaje ~40 y se vuelve progresivamente obsoleto el resto de la sesión. Es un caso directo de "información obsoleta enviada de forma silenciosa" (protocolo, secciones 13/22).

**Solución propuesta:**
1. Quitar el tope fijo de ciclos o basarlo en crecimiento real, no en un contador fijo de 2.
2. Pasar `summary.text` (el resumen anterior) a `build_summary_prompt` para que el modelo lo actualice de forma incremental en vez de releer todo el histórico antiguo cada vez.

**Verificación:** test de integración con 60+ mensajes simulados comprobando que `summary.text` cambia después del mensaje 40 (no existe hoy; `test_session_summary_integration.py` habría que revisarlo para confirmar si cubre este caso — no releído línea a línea en esta pasada).

---

### D3 — Coste del resumen sin cota (MEDIO)

**RESUELTO en `520b723`.** Consecuencia directa de D2: `since_index` acota el transcript a lo nuevo desde el último resumen.

Consecuencia directa de D2: al no acotar el transcript a "lo nuevo desde el último resumen" sino a "todo menos los últimos 6 mensajes", el prompt enviado al modelo de resumen crece con el tamaño total de la conversación, no con el incremento. Esto agrava D1 (más tokens = más latencia bloqueando la UI) cuanto más larga es la conversación.

**Solución:** acotar el transcript a una ventana desde el último resumen (`last_message_count`) en vez de "todo menos los últimos 6".

---

### D4 — `@Slot` eliminados en conexiones cross-thread (MEDIO)

**RESUELTO en `e6baf23`.** Decoradores restaurados con firma exacta (`@Slot(str, object, int)` y `@Slot(list, list, list, list)`).

**Archivo:** `ui/controllers/app_controller.py`.

**Evidencia:** el diff elimina `@Slot(str, object, int)` sobre `_on_capabilities_ready` y `@Slot(list, list, list, list)` sobre `_on_mcp_servers_changed`, durante el refactor de `_refresh_capabilities`/`_cleanup_caps_thread` → `_start_caps_worker`/`_on_caps_thread_finished`. `_on_capabilities_ready` sigue conectado a una señal emitida desde `CapabilitiesWorker`, que corre en un `QThread` distinto al hilo de UI.

Sin `@Slot`, PySide6 puede seguir conectando el método (duck-typing), pero se pierde la firma explícita que usa para el marshaling de tipos en conexiones encoladas entre hilos. No se ha visto en el diff ninguna mención de que sea intencional.

**Severidad:** MEDIO — el cambio en sí está DEMOSTRADO; el impacto real (fallos intermitentes cross-thread) es POSIBLE, no reproducido en esta pasada.

**Solución:** restaurar los decoradores `@Slot` con la firma exacta, salvo que se confirme que la eliminación fue deliberada.

---

### D5 — Acceso a método privado de otra clase (MEDIO)

**RESUELTO en `e6baf23`.** `_is_short_confirmation` → `is_short_confirmation` (público). Sin cambios en `intent.py` internos ni en los tests.

**Archivo:** `core/ollama.py`, `OllamaClient._effective_auth_text()`.

**Evidencia:**
```python
from .intent import ToolIntentGate
...
if not ToolIntentGate._is_short_confirmation(text):
```

Esto profundiza el hallazgo H1 de la pasada anterior (duplicación de heurísticas de intención entre `ollama.py` e `intent.py`): en vez de unificar la lógica de "confirmación corta" en un sitio, `ollama.py` ahora depende directamente de un método interno (prefijo `_`) de `intent.py`. Cualquier cambio de firma o comportamiento en `_is_short_confirmation` puede romper silenciosamente el nudge de falso completado sin que el test suite de `intent.py` lo detecte (son módulos con tests separados).

**Solución:** exponer `_is_short_confirmation` como método público de `ToolIntentGate`, o mover esa lógica a un módulo neutral compartido por ambos.

---

### D6 — Modelo de resumen sin comprobación de disponibilidad (BAJO/MEDIO)

**RESUELTO en `86ce889`.** `AppConfig.summary_model` (default `"qwen3:1.7b"`) configurable y persistido; `core.model_capabilities.is_model_available()` cachea 60s el probe de `/api/show` (`False` solo en 404, `True` en error de red para no deshabilitar por flakiness); `ChatController` recibe el modelo por kwarg opcional y gatea el resumen con un único warning por sesión si no está instalado. El chat sigue funcionando.

`self._summary_model: str = "qwen3:1.7b"` está hardcodeado, sin pasar por el mecanismo de `/api/show`/`model_capabilities` que sí usa el resto de la app para validar modelos. Si el usuario no tiene ese modelo descargado, cada intento de resumen falla (excepción capturada, log de warning) consumiendo igualmente el bloqueo de UI de D1 (timeout de conexión/petición) sin avisar en la interfaz, solo en el log.

**Solución:** exponerlo como opción de configuración, o comprobar su disponibilidad una vez (cacheada) y desactivar la función con aviso visible si no existe.

---

### D7 — Colisión de alias dentro de una misma llamada (BAJO, POSIBLE)

**RESUELTO en `fdff32d`.** `_normalise_args` ahora normaliza tanto alias como canónicos y detecta colisiones: si dos claves resuelven al mismo canónico con el MISMO valor, colapsa sin error; con valores DISTINTOS, devuelve un `str` de error que `_call_tool` propaga tal cual. Cubre el caso `content`+`text` reportado abajo.

**Archivo:** `core/tools.py`, `_normalise_args()`.

El escaneo confirma que la resolución de alias está correctamente acotada por tool (`_ALIASES` solo se aplica si el canónico está en `properties` de esa tool concreta), así que **no hay** colisión entre `content`/`text` de distintas tools como podría parecer a primera vista — eso funciona bien y no se reporta como problema.

Sí queda un caso de borde real: si el modelo manda en una misma llamada dos claves que resuelven al mismo canónico (p. ej. `insertar_en_archivo` con `content` y `text` a la vez), el orden de iteración del diccionario de argumentos decide cuál gana, sobrescribiendo la otra sin aviso ni error — porque la clave literal `text` no pasa por la comprobación `seen_canonical` (solo la pasan los alias resueltos vía `alias_to_canonical`). Es un caso de baja probabilidad (un modelo mandando dos argumentos redundantes a la vez) y no se ha observado en el proyecto; se marca POSIBLE, no DEMOSTRADO en producción.

**Solución (opcional, prioridad baja):** si tras normalizar dos claves distintas resuelven al mismo canónico con valores distintos, devolver error en vez de sobrescribir en silencio.

---

## 3. Hallazgos de la pasada anterior: estado

- **N4 (caché de `/api/show` sin TTL) → RESUELTO.** `core/model_capabilities.py` añade `_CACHE_TTL_S = 60.0` y guarda `(timestamp, caps)`; confirmado por lectura directa del código actual.
- **H1 (duplicación de heurísticas ollama.py / intent.py) → NO RESUELTO, PROFUNDIZADO.** Ver D5: en vez de unificarse, ahora hay acoplamiento directo a un método privado.
- El resto de hallazgos de pasadas anteriores (N2, N3, N5, H2, H3, H8) no han sido tocados por este delta (no hay cambios en `xml_tools.py`, `context_window.py` budget margin, ni en la normalización de `path="/"`); siguen abiertos tal como se documentaron en `docs/auditoria-2026-09-26-completa.md`.

---

## 4. Observaciones menores (no accionables sin más evidencia)

- `Workspace.insert_in_file` y `Workspace.edit_file` (nuevas) tienen buenas guardas (tamaño máximo, validación UTF-8, unicidad de `old_string` al estilo Claude/Aider/OpenCode) pero, igual que `create_file`/`write_file` ya existentes, escriben con `file.write_bytes(data)` directo, sin patrón atómico (escribir a temporal + rename). No es una regresión de este delta —es el patrón preexistente en todo `workspace.py`— pero sigue siendo un riesgo ante cancelación/crash a mitad de escritura (protocolo, sección 41). Se deja como observación, no como hallazgo nuevo con severidad propia, porque no hay evidencia de que haya ocurrido.
- `ui/controllers/app_controller.py` tiene cambios **sin commitear** en el árbol de trabajo (`git status --short` → ` M ui/controllers/app_controller.py`). No es un problema de calidad, pero conviene commitear antes de seguir iterando para no perder el trabajo.

---

## 5. Preguntas para acotar (si se quiere seguir con más precisión)

No han sido imprescindibles para completar este informe, pero ayudarían a confirmar severidad/alcance si se quiere profundizar después:

1. ¿`qwen3:1.7b` está realmente descargado en el Ollama de este equipo? (condiciona si D6 se manifiesta ya o es solo un riesgo latente).
2. ¿La eliminación de los `@Slot` en D4 fue deliberada (p. ej. por algún problema de tipado con PySide6) o un descuido del refactor?

No se ha modificado nada del proyecto durante esta auditoría.
