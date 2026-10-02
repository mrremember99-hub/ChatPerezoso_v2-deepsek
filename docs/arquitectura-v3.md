# Arquitectura v3

## Objetivo

Aplicación de escritorio para conversar con un modelo local (Ollama)
que puede leer, escribir y ejecutar sobre un workspace. Sencilla,
que haga bien su tarea, y ampliable con plugins.

No es un agente autónomo. El humano está delante del chat, ve lo
que pasa, y decide cuándo parar. El modelo propone; el humano
aprueba; el sistema ejecuta.

## Las 3 capas

    ┌──────────────────────────────────────────────┐
    │  Capa 1 — PRESENTACIÓN                       │
    │  PySide6 · Widgets · Controllers · Signals   │
    ├──────────────────────────────────────────────┤
    │  Capa 2 — NÚCLEO                             │
    │  Loop · Tools · Gate · Historial · Completion│
    ├──────────────────────────────────────────────┤
    │  Capa 3 — PLUGINS                            │
    │  search · git · shell · mcp · verificador    │
    └──────────────────────────────────────────────┘

Cross-cutting (contratos, no capas):

- **Undo vía git**: el usuario abre su terminal o usa el plugin.
- **Human-in-the-loop**: gate de confirmación en `core/approval.py`.

### Capa 1 — Presentación

    ui/
      main_window.py
      chat_state.py
      agent_worker.py          ← QObject que envuelve AgentSession
      autopilot_prompt.py
      controllers/
        app_controller.py
        chat_controller.py
        diagnostics_controller.py
      views/
        chat_panel.py · sidebar.py · right_panel.py · dialogs.py
      rendering/
        ...

Responsabilidad: presentación y coordinación. Conoce la API del
núcleo (`AgentSession`) y las señales del worker. No conoce
detalles internos del loop.

### Capa 2 — Núcleo

    core/
      agent/
        __init__.py
        session.py            ← loop modelo↔tools + gate
        events.py             ← eventos que emite la sesión
        policy.py             ← AgentConfig, ModelSpec, AgentSpec
        model.py              ← Protocol ModelClient
        ollama_adapter.py     ← adapter sobre OllamaClient
        completion.py         ← CompletionVerifier (opcional)
      approval.py             ← gate de confirmación
      tool_provider.py        ← registro de tools
      tool_result.py
      composite_tools.py
      xml_tools.py
      history.py              ← persistencia del chat
      config.py               ← AppConfig (persistencia)
      agents.py               ← AgentStore
      ollama.py               ← cliente HTTP a Ollama
      context_window.py
      session_summary.py
      prompt_phases.py        ← usado por completion verification
      model_capabilities.py
      models_config.py
      shutdown.py

Responsabilidad: ejecutar el ciclo de agente (prompt → modelo →
tools → repetir), aplicar el gate de confirmación, persistir
historial, verificar completion si está activado.

Nada más. Sin durable execution, sin health monitoring, sin loop
detection, sin idempotencia. Esas piezas existieron en v2 y se
podaron: resolvían problemas que en esta app ya resuelve el humano
(o git).

### Capa 3 — Plugins

    plugins/
      search/         ← buscar_en_workspace
      git/            ← git_status, git_diff, git_log, git_show
      shell/          ← ejecutar_comando (con allowlist)
      mcp/            ← cliente MCP + tool bridge
      verificador/    ← verificación de sintaxis post-escritura

Un plugin declara tools y, opcionalmente, hooks de UI. No conoce
el núcleo del agente, solo la API de registro (`ToolProvider`).

## Qué se eliminó de v2 y por qué

En v2, `core/harness/` tenía ~3500 líneas y 9 subsistemas. En
producción solo 1 estaba activo (el loop del agente). El resto
existía por si acaso:

- `EventLog` (SQLite + WAL + retención): reemplazable por logging.
- `CheckpointManager`, `IdempotencyRegistry`: sin caso de uso.
- `HarnessState`, `fold_events`, `resume`, `plan_resume`: no
  integrados.
- `LoopDetector` + correctives: sin UI para activarlo.
- `HealthMonitor`: sin consumidor.
- `CompletionVerifier`: **se conserva**. Usa fases OVERPAPER y no
  necesita infraestructura.
- `VRRStopCriterion`: eliminado en v19 (nunca usado).
- `ToolSchemaCompiler`: eliminado en v19 (stub S0).

Justificación: las aplicaciones de referencia (Jan, Open WebUI,
Aider) no tienen ninguno de estos subsistemas. Tienen un núcleo
mínimo + extensiones. Claude Code y Cursor los tienen porque
ejecutan agentes sin humano delante; esta app no.

## Referencias

- **Jan**: 3 capas (presentación / core / extensions).
- **Open WebUI**: plugins como ciudadano de primera.
- **Aider**: git como undo, human-in-the-loop.
- **Claude Code**: 4 fases del agent loop, sin durable execution
  en el host.

## Cómo añadir un plugin

1. Crear `plugins/mi_plugin/` con `__init__.py` y `provider.py`.
2. El provider implementa el contrato de `ToolProvider`: declara
   `definitions()` y `call(name, args, *, allow_destructive, cancel_event)`.
3. El plugin se descubre automáticamente (`discover_plugin_factories`).
4. Opcionalmente, declara si sus tools requieren confirmación
   (`requires_confirmation(name)`).

No hay registro manual. Si el plugin existe y expone el contrato,
la app lo carga.

## Estado actual

- HEAD: rama `v3`.
- Suite: 1750 passed, 4 skipped.
- mypy: 0 errores.
- Sin warnings.
- v2 congelado en tag `v2.0-final` (rama `main`).
