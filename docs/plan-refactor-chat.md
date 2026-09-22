# Plan de refactor — `OllamaClient.chat()`

Estado: **fase de diseño**. Sin código cambiado.
Objetivo: dividir `chat()` (~200 líneas) en métodos privados con responsabilidades acotadas, preservando la API pública y todos los tests actuales.

## Análisis actual

`chat()` mezcla cinco responsabilidades:

1. **Preparación** (una vez por chat): capabilities, strategy, gate, system prompt, `send_tools`, `buffer_only`.
2. **Streaming + métricas** (por ronda): fit de contexto, stream, calibrar, notificar `on_metrics`.
3. **Procesar ronda**: delegar en la strategy.
4. **Ejecutar tool calls**: appendar assistant, autorizar, ejecutar, formatear, computar firma.
5. **Evaluar ronda**: retry textual, firma repetida, contador de bloqueos.

## Descomposición propuesta

### `_ChatContext` (dataclass)

Agrupa todo lo que es constante durante el bucle.

```python
@dataclass
class _ChatContext:
    # Recibidos por parámetro
    model: str
    options: dict[str, Any] | None
    on_text: Callable[[str], None]
    on_tool: Callable[[str, dict[str, Any]], str]
    on_metrics: Callable[[dict[str, int]], None] | None
    cancel_event: threading.Event | None
    context_window: ContextWindow | None

    # Preparados por `_prepare_context`
    strategy: ToolCallingStrategy
    history: list[dict[str, Any]]       # se reasigna en cada ronda
    authorization_text: str
    gate: ToolIntentGate
    tool_names: set[str]
    send_tools: list[dict[str, Any]] | None
    buffer_only: bool