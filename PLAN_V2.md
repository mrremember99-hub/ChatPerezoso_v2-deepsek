# ChatPerezoso v2 — Plan de desarrollo

## Principio
El núcleo debe ser pequeño, funcional y robusto. Todo lo que no sea imprescindible para hablar con Ollama y usar herramientas pertenece a plugins.

## Fases

### Fase 0 — Base
- [x] Proyecto independiente
- [x] Configuración mínima
- [x] Workspace aislado
- [x] Tests básicos

### Fase 1 — Ollama
- [x] Cliente Ollama
- [x] Listado de modelos
- [x] Streaming
- [x] Bucle básico de herramientas

### Fase 2 — Interfaz funcional
- [x] Ventana principal
- [x] Selector de modelo
- [x] Workspace seleccionable
- [x] Chat con streaming sin bloquear la interfaz
- [x] Indicadores de estado
- [x] Manejo básico de errores
- [x] Limpieza de conversación
- [x] Cancelación segura de una tarea en curso

### Fase 3 — Herramientas núcleo
- [x] Revisar contrato de herramientas
- [x] Mejorar presentación de resultados
- [x] Protección explícita de operaciones destructivas
- [x] Pruebas de conversación + herramientas con Ollama real

### Fase 4 — MCP como plugin
- [x] Adaptador MCP independiente
- [x] Activación/desactivación del plugin
- [x] Sin dependencias MCP en el núcleo
- [x] Activación y descubrimiento MCP fuera del hilo de UI
- [x] Herramientas MCP expuestas con prefijo `mcp__`
- [x] Bloqueo de herramientas MCP hasta confirmación por defecto

La primera entrega del plugin usa servidores MCP por stdio y carga el SDK de forma diferida. El núcleo puede ejecutarse sin instalar MCP.

**Actualización:** la UI ya no permite añadir servidores MCP arbitrarios por
nombre/comando. Se simplificó a un interruptor único que activa/desactiva el
servidor de archivos (`server-filesystem`) del workspace actual
(`ui/views/sidebar.py`, `ui/controllers/mcp_controller.py`). El soporte
interno multi-servidor (`MCPToolBridge`) se mantiene y sigue teniendo
reconexión automática si el proceso muere, pero no hay UI para más de un
servidor.

### Fase 4.1 — Estabilización antes de nuevos plugins
- [x] Separar llamadas textuales de herramienta del texto visible
- [x] Evitar contaminar el historial con llamadas textuales de herramienta
- [x] Normalizar y conservar saltos de párrafo en respuestas
- [x] Carga de modelos sin bloquear la interfaz
- [x] Cancelación real de la generación en curso
- [x] Cancelación también durante una confirmación de operación destructiva
- [x] Limpieza de ciclo de vida de workers y threads
- [x] Protección explícita de operaciones MCP potencialmente destructivas
- [x] Añadir pruebas de cancelación y seguridad MCP
- [x] Limpiar artefactos de desarrollo del paquete distribuible
- [x] Ejecutar pruebas de integración con Ollama real en el Mac
- [x] Prueba manual de UI con PySide6 + Ollama + MCP en el Mac (edición de
      archivos y activación MCP verificadas a mano; shell, git, búsqueda,
      agentes y restauración de historial solo probados por tests, no a mano)

**Bloque A de autorización aplicado:**
- [x] No autorizar una herramienta por mera mención de su nombre
- [x] Rechazar negaciones explícitas de la operación propuesta
- [x] Exigir acción explícita + nombre para herramientas MCP genéricas
- [x] Mantener la confirmación destructiva fuera del modelo

**Decisión:** el adaptador MCP mantiene actualmente una sesión stdio persistente por servidor, en un hilo/event loop dedicado. Se cierra al desactivar el servidor. No se considera un bloqueo para continuar.

**Validación real:** la batería de integración se ha ejecutado con `llama3.1:latest`,
`ornith-1.5:9b`, `glm-4.7-flash:latest`, `gemma4:12b`, `rnj-1:latest`,
`ministral-3:latest`, `lfm2.5:latest`, `hermes3:latest`, `devstral:latest`,
`qwen3:14b` y `granite4.1:3b`. Todos superan las cuatro pruebas. `deepseek-r1:latest`
no emitió la llamada nativa de herramienta y `hdnh2006/salamandra-7b-instruct:latest`
rechazó las herramientas con HTTP 400.

### Fase 5 — Plugins
- [x] Git — `plugins/git` (solo lectura: status/diff/log/show, sin push/commit/checkout como tools)
- [x] Búsqueda — `plugins/search` (grep en el workspace, con límites de tamaño/ReDoS)
- [x] Shell — `plugins/shell` (ejecución de comandos con confirmación obligatoria y lista de riesgos; no estaba en el plan original, se añadió y se revisó a fondo)
- [x] Diagnósticos — `ui/diagnostics.py` + panel (estadísticas de la sesión)
- [x] Agentes — `core/agents.py` + `AgentController` (system prompt, temperature, num_ctx, herramientas permitidas por agente)
- [x] Historial — `core/history.py` (persistencia de conversación entre sesiones; no es memoria a largo plazo ni RAG)
- [ ] Memoria (más allá del historial de la sesión actual)
- [ ] Automatización
- [ ] Benchmarks

### Fase 6 — Arquitectura MVC (no estaba en el plan original)
- [x] Separación en `ui/views/` (widgets puros) + `ui/controllers/` (lógica) + `ui/rendering/` (renderers inyectables vía protocolo)
- [x] `core/composite_tools.py` / `core/tool_provider.py`: abstracción común para componer providers (core + MCP + git + search + shell) y filtrarlos por agente

## Criterio de cierre del núcleo
Debe poder:
1. Abrirse.
2. Conectarse a Ollama.
3. Mostrar modelos instalados.
4. Mantener una conversación con streaming.
5. Seleccionar un workspace.
6. Listar y leer archivos mediante herramientas.
7. Seguir funcionando si Ollama devuelve un error.

No se añade una nueva capa de arquitectura para resolver un problema que todavía no existe.

### Pendiente de verdad ahora mismo
- [ ] Probar a mano en el Mac: shell, git, búsqueda, agentes (crear/editar/borrar), regenerar respuesta, restaurar conversación desde `history.json`
- [ ] `pytest-timeout` no está en `requirements.txt`; el test con `@pytest.mark.timeout(15)` en `test_mcp_plugin.py` no aplica el timeout de verdad, solo avisa
- [ ] Memoria a largo plazo, automatización y benchmarks (Fase 5) siguen sin empezar


### Fase 3 — Duplicidad núcleo/MCP
- `server-filesystem` sustituye `leer_archivo`/`listar_carpeta` cuando ofrece `read_file`/`list_directory`.
- La barrera de intención reconoce los alias MCP.
- Al desactivar MCP se restauran las herramientas locales.

---

## Auditoría 2026-09

Completada en la rama `audit-fixes`. Ver `docs/audit-2026-09.md` para
detalles.

**Tags de referencia:**
- `baseline-antes-de-fixes` (c9982b6) — estado antes de la auditoría
- `post-5` (8bf1bb7) — estado después de todos los fixes aplicados

**Métricas:**

| Métrica | Antes | Después |
|---|---|---|
| Tests | 506 passed | 523 passed (+17) |
| Pico render (code final) | 26.6 ms | 0.1 ms |
| Pico render (prose accum) | 146 ms | 146 ms (sin cambio) |
| Streaming warm TTFT | no medido | 0.24 s (granite4.1:3b) |
| ToolIntentGate | no medido | 10/10 casos correctos |

**Fases 6 del plan original (arquitectura MVC) siguen completas.**
Los plugins (git, search, shell) también. Lo que queda pendiente
está en `TODO.md`.
