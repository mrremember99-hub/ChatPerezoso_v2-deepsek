# P2#24 — Diseño del OllamaAdapter para S6

**Origen**: auditoría externa, hallazgo P2#24 ALTO.
**HEAD auditoría**: `75a2240`.
**Estado**: diseño cerrado, pendiente de implementación (S6).

## Problema

El Protocol `ModelClient` del harness (`core/harness/model.py`)
define:

    def chat(messages, *, tools=None, stream=True, cancel_event=None)
        -> Iterator[ModelDelta]

Una **sola ronda**: envía mensajes, devuelve un iterador de
`ModelDelta` (`text` | `tool_call` | `done`). El harness controla
el bucle de tool calls (S4-b: `_agent_loop`).

Pero `OllamaClient.chat()` (core/ollama.py:727) **ya tiene su
propio bucle interno** de `max_rounds=15`:
1. `_prepare_context()` — gate de intención, filtro de tools,
   system prompt injection, cache de tokens.
2. `for _ in range(max_rounds)` — cada iteración llama a
   `self._stream()`, procesa la ronda con `strategy.process_round()`,
   ejecuta tools vía `on_tool` callback, acumula texto visible.
3. Devuelve el último texto como `str`.

Si el adapter envuelve `chat()` tal cual:
- **Bucle anidado** (harness + OllamaClient).
- `ToolCallRequested`/`ToolCallCompleted` del harness **no se
  emiten**: las tools las ejecuta `chat()` internamente.
- **LoopDetector, gate de aprobación, health, idempotencia,
  EventLog** — todo el valor del harness queda **inerte**.

## Opciones evaluadas

**A) Método nuevo en `OllamaClient`** para una sola ronda. El
adapter lo envuelve. `chat()` intacto.
- Pros: cambio acotado (~30 líneas en ollama.py). `ChatWorker`
  sigue usando `chat()` sin tocar. Rollback trivial.
- Cons: duplicación temporal (dos caminos paralelos hasta S7).

**B) Adapter intercepta `on_tool` y corta el bucle con un
sentinel.** Hacky. Fragil ante cambios en `strategy`. Descartada.

**C) Portar el bucle de `OllamaClient` al harness y dejar
`OllamaClient` como transporte puro.**
- Pros: un solo bucle, limpio a largo plazo.
- Cons: S6 pasa de "cablear" a "reescribir core/ollama.py".
  Alto riesgo. No MVP.

**Decisión: A**. Coherente con la estrategia de slices
incrementales del handoff v7.

## Sub-decisión A1 vs A2

**A1 (elegida)**: `chat_once()` minimalista — HTTP + parseo, sin
gate, sin bucle, sin ejecución de tools. El adapter traduce la
respuesta a `ModelDelta`. La lógica de gate/filtro/system prompt
vive **solo** en el harness (P2#6 ya la implementa).

**A2 (rechazada)**: `chat_once()` reutiliza `_prepare_context()`.
Fiel al comportamiento actual, pero obliga a pasar
`context_window` y `auto_approve` por método — y el harness ya
los maneja.

**Motivo de A1**: el harness es la fuente de verdad. El cliente
es transporte. Duplicar gate/filtro/system prompt en dos sitios
es una garantía de divergencia (ver regla aprendida: ChatWorker
y session ya divergen en `_is_auto_approved`, P2#5).

---

## Contrato de `chat_once()`

En `core/ollama.py` (firma propuesta):

    def chat_once(
        self,
        model: str,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        options: dict[str, Any] | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[ModelDelta]:
        """Una sola ronda: HTTP + parseo. Sin gate, sin bucle, sin
        ejecucion de tools. El harness maneja system prompt, filtro
        de tools, aprobacion, loop detection y ejecucion. Este
        metodo es solo transporte."""

Comportamiento:
- Envía `messages` a `/api/chat` con `stream=True`.
- Itera líneas, parsea con `parse_ollama_line`, acumula texto y
  thinking.
- **Al cerrar el stream**, yield `ModelDelta(kind="done")`.
- Si el mensaje tiene `tool_calls`, yield `ModelDelta(kind=
  "tool_call", tool_call={...})` **antes** del `done`.
- Si `cancel_event.is_set()`, raise `OllamaCancelled`.
- Errores HTTP → `OllamaError`.

Implementación: **reutiliza `_stream()` existente** (línea 1934),
que ya devuelve el `dict` message. `chat_once()` = una sola
llamada a `_stream()` + traducción a `ModelDelta`.

No hay lógica nueva de transporte. Solo se **expone una ronda
como generador de ModelDelta**.

## Adapter (`ui/harness_bridge.py` en S6)

    class OllamaAdapter:
        """ModelClient del harness sobre OllamaClient real."""

        def __init__(
            self,
            client: OllamaClient,
            *,
            model: str,
            options: dict[str, Any] | None = None,
        ) -> None:
            self._client = client
            self._model = model
            self._options = options

        def chat(
            self,
            messages,
            *,
            tools=None,
            stream=True,
            cancel_event=None,
        ) -> Iterator[ModelDelta]:
            yield from self._client.chat_once(
                self._model,
                messages,
                tools=tools,
                options=self._options,
                cancel_event=cancel_event,
            )

Responsabilidad del adapter:
1. Recibir la config del cliente (`model`, `options`) por
   constructor — no por `chat()` (el Protocol no tiene canal
   para eso).
2. Delegar en `chat_once()`.
3. Traducir excepciones si hace falta (`OllamaCancelled` →
   el harness ya lo trata; `OllamaError` idem).

**Lo que NO hace el adapter**:
- No prepara contexto (session lo hace).
- No ejecuta tools (session lo hace).
- No aplica gate ni allowlist (session lo hace).
- No inyecta system prompt (session lo hace).

## Traducción `dict message` → `ModelDelta`

La respuesta de `_stream()` es un dict:

    {
        "content": str,           # texto visible completo
        "thinking": str | None,
        "tool_calls": list[dict], # [{function: {name, arguments}}]
        "_stream_completed": bool,
        "_stream_done_reason": str | None,
    }

`chat_once()` lo traduce:

    # 1. Texto (opcional). ModelDelta(kind="text", text=content)
    # 2. tool_calls (0 o mas). Por cada uno:
    #    ModelDelta(kind="tool_call",
    #               tool_call={name, arguments})
    # 3. Cierre. ModelDelta(kind="done",
    #                       text=content if not emitted else "")

Nota: el harness hace `break` al primer `tool_call` (S4-b,
session.py:201). Si un turno tiene varios, solo se procesa el
primero. **Esto ya está identificado como P2#11 (MEDIO)** en la
auditoría. La resolución (acumular todos los `tool_calls` del
turno) es trabajo de P2#11, no de P2#24.

## Tests de integración (S6)

Un solo test, marcado `@pytest.mark.integration`:

    def test_chat_once_round_trip():
        """Requiere Ollama corriendo con un modelo pequeño.
        Verifica: textos sin tool_call, un tool_call,
        cancel_event, error HTTP."""
        # skip si no hay Ollama
        client = OllamaClient(...)
        for delta in client.chat_once("qwen3:1.7b", [
            {"role": "user", "content": "responde 'ok'"},
        ]):
            assert isinstance(delta, ModelDelta)

Unit tests del adapter con un `FakeOllamaClient` que devuelve
`dict message` programados: ya cubiertos por
`test_session_basic.py` + `test_session_tool_calls.py` (el
adapter solo delega).

## Riesgos residuales

1. **Doble bucle eliminado**: al usar `chat_once()`, el harness
   pasa a controlar el bucle completo. Verificado en S4-b.

2. **Gate de intención perdido**: `_prepare_context()` tenia
   `ToolIntentGate` (anti-alucinación, `core/intent.py`). Con A1
   el gate no corre. P2#25 lo identifica. **Decisión**: cubrir
   con un gate equivalente en session o en el adapter antes de
   S6-cableado.

3. **Cache de tokens perdido**: `RequestTokenCache` optimiza
   `num_ctx` por request. Con A1 no corre. **Decisión**: el
   harness pasa `num_ctx` fijo por `options` al adapter. No es
   crítico (Ollama cachea por sesión).

4. **thinking**: `_stream()` devuelve `thinking` del modelo
   (razonamiento CoT). El Protocol `ModelDelta` **no tiene canal
   para thinking**. Si el harness quiere preservarlo entre
   rondas, necesita un cuarto `kind="thinking"`. **Deuda
   anotada para P2#11/P2#25**, no bloquea S6.

## Plan de implementación

**S6-a** (30 min):
- Añadir `chat_once()` a `OllamaClient` (reusa `_stream()`).
- Añadir `tests/test_ollama_chat_once.py`: 4 casos sin red
  (mock del `_stream()`).

**S6-b** (1 sesión):
- `ui/harness_bridge.py`: `OllamaAdapter` + `HarnessBridge`
  (QObject que traduce eventos → señales Qt existentes).
- Cablear `ChatController` para usar `HarnessSession` con el
  bridge. Flag `harness_enabled` en `config.json`.
- Tests de integración de UI.

**S7**:
- Borrar `ui/workers.py` + `ui/autopilot_prompt.py` + tests
  huérfanos.
- `ChatWorker._is_auto_approved` migra a `core/approval.py`
  (ya existe).

## Decisión de cierre

**A1, `chat_once()` minimalista, adapter por constructor.**
No toca el bucle de `chat()`. `ChatWorker` intacto hasta S7.
Riesgo contenido. Rollback por flag.
