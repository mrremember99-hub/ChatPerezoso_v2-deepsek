# S6-b-2 — Diseño de migración UI → harness

## Objetivo

Cablear `HarnessWorker` en `ChatController` detrás de flag
`harness_enabled`, sustituyendo a `ChatWorker` en producción sin
romper rollback.

## Decisión clave

**Las 13 señales de `HarnessWorker` coinciden exactamente** en
nombre y firma con las de `ChatWorker` (verificado en grep del
2026-09-30). Esto significa:

- Las 18 conexiones de `_spawn_worker` (líneas 785-802 del
  controller) **no cambian**.
- Solo cambia **qué objeto se asigna a `self._worker`**.
- Bifurcación mínima: un `if` antes de `self._worker = ChatWorker(...)`.

## Decisión sobre las 3 señales vaciadas

| Señal | En modo harness | Handlers en controller |
|---|---|---|
| `tool_auto_approved` | nunca emite | `_on_tool_auto_approved` |
| `metrics_updated` | nunca emite | `_on_worker_metrics` |
| `summary_ready` | nunca emite | `_on_summary_ready` |

**Decisión S6-b-2: aceptar vacías.** Handlers no se invocan; no
hay crash, solo ausencia de información.

- `tool_auto_approved` → pérdida cosmética (no se muestra badge).
- `metrics_updated` → sin contadores de tokens en UI. El harness
  no expone tokens por el Protocol `ModelClient`.
- `summary_ready` → resumen rolling no portado a S4. El
  controller lo gestiona; sin él, no se regenera el resumen. No
  bloquea conversación.

**Puente: fuera de alcance** hasta ver si Fase 2 lo necesita.

## Punto de inyección del flag

Atributo nuevo en `ChatController.__init__`:

    self._harness_enabled = False  # S6-b-2, OFF por defecto

Coherente con `_loop_detection_enabled` (línea 144). El setter
público `set_harness_enabled(bool)` replica el patrón existente
para `_auto_approve`.

Origen del valor: **constante local en S6-b-2**.
Migración a `config.json` en S6-b-3 o S7, cuando la prueba
manual determine los valores correctos. No complicar el primer
pase.

## Construcción de HarnessSession en `_spawn_worker`

Bloque nuevo antes de `self._worker = ChatWorker(...)`:

    if self._harness_enabled:
        from core.harness.ollama_adapter import OllamaAdapter
        from core.harness.policy import (
            AgentSpec, HarnessConfig, ModelSpec,
        )
        from core.harness.session import HarnessSession
        from ui.harness_worker import HarnessWorker

        adapter = OllamaAdapter(self.client, model=model,
                                options=options)
        cfg = HarnessConfig(
            run_id=<uuid>,
            workspace_root=<de tools/workspace>,
            storage_dir=<tmpdir o workspace/.harness>,
            model=ModelSpec(name=model),
            agent=AgentSpec(name="chat"),
        )
        session = HarnessSession(
            cfg,
            model_client=adapter,
            tool_registry=<ver 1.2-c>,
            loop_detector=loop_detector,  # ya construido arriba
            health_monitor=None,
            eventlog=None,
            confirmation_handler=None,   # se asigna abajo
        )
        worker = HarnessWorker(
            session, user_message=<ver 1.2-d>,
        )
        session.confirmation_handler = worker.handle_confirmation
        self._worker = worker
    else:
        self._worker = ChatWorker(...)  # sin cambios

## Chicken-and-egg del confirmation_handler

`HarnessSession.confirmation_handler` es atributo público
(`session.py:94`). Construir la session con `None`, luego asignar
`worker.handle_confirmation` **antes** de `self._thread.start()`.

La session no invoca el handler hasta `session.step()`, que
ocurre dentro de `worker.run()` (ya en el thread). Cero riesgo
de carrera.

## Huecos a verificar en 1.2 (antes de codear)

### Hueco A — `ToolProvider` vs `ToolRegistry`

El controller tiene `self.tools` (ToolProvider). La session
espera `tool_registry` con interfaz:

- `requires_confirmation(name) -> bool`
- `call(name, arguments, *, allow_destructive, cancel_event) -> str`

**Verificar**: ¿`ToolProvider` implementa esos métodos? Si no,
hace falta adapter `ui/harness_tool_adapter.py` (similar al
OllamaAdapter). Coste estimado: ~30 min + tests.

### Hueco B — `user_message` en `HarnessWorker`

`ChatWorker` recibe `list(self.messages)` (histórico). La
`HarnessSession` mantiene su propio `_messages`. Pregunta: ¿el
worker debe recibir el mensaje actual o inyectar todo el
histórico? Decisión tentativa: **migrar el histórico a
`session._messages`** antes del primer `step()`, luego llamar
`step(user_message)`.

**Verificar**: ¿hay API pública en `HarnessSession` para
precargar mensajes? Si no, hace falta añadirla (probablemente
`add_message(role, content)` o similar).

### Hueco C — `system_prompt`

`ChatWorker` lo recibe como arg. En harness, el system prompt
tiene que ir como primer mensaje del array `_messages`.

**Verificar**: ¿`HarnessSession` respeta mensajes system
precargados? P2#6 (`7f0348b`) dice "session envia system prompt
+ tools filtradas", así que probablemente sí.

## Rollback

Flag `_harness_enabled=False` → `ChatWorker` intacto. Cero
cambios visibles, cero tests rotos. Reversible en un commit.

## Tests a añadir en 1.2

1. `test_spawn_worker_harness_off_usa_chat_worker` — flag False,
   verificar `isinstance(self._worker, ChatWorker)`.
2. `test_spawn_worker_harness_on_usa_harness_worker` — flag True,
   verificar `isinstance(self._worker, HarnessWorker)`.
3. `test_harness_session_recibe_handler_del_worker` — verificar
   `session.confirmation_handler == worker.handle_confirmation`.
4. `test_señales_identicas_off_y_on` — para cada una de las 13
   señales, verificar que existe y tiene misma firma.

Tests existentes del controller **no se tocan** (flag OFF por
defecto).

## No-objetivos explícitos

- Puentear las 3 señales vaciadas.
- Mover `_auto_approve` / `_verificador_hook` al harness. El
  harness tiene sus propias policies; conviven en S6-b-2.
- Gestionar context window / compactación desde el harness.
- Migrar `_ChatContext` a la session.
- EventLog persistente. `eventlog=None` en S6-b-2. Se activa en
  S6-b-3 si Fase 2 lo justifica.

## Riesgos

1. **Huecos A/B/C sin resolver** → 1.2 puede necesitar 1h más.
2. **Prueba manual obligatoria** con app abierta.
3. **Handlers `_on_loop_warning`** reciben `(str, str)`; verificar
   que `HarnessWorker` emite con misma semántica que `ChatWorker`
   (detector name + signature vs detector + reason). Si difiere,
   la UI mostrará texto incoherente — cosmético, no crash.
4. **Cancelación cruzada**: `HarnessWorker.cancel()` marca el
   event del worker Y llama `session.cancel()`. Verificar que el
   controller solo llama al worker, no a la session.

## Próximo paso

1.2-a: resolver huecos A/B/C con grep (30 min).
1.2-b: escribir el cambio en `_spawn_worker` + tests (30 min).
1.2-c: prueba manual con app abierta (30 min).
