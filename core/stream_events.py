"""Eventos tipados del stream de Ollama.

Antes de este módulo, `OllamaClient.chat()` recibía callbacks:
`on_text(delta)` y `on_tool(name, args)`. El transporte llamaba a
esos callbacks directamente, mezclando "qué hacer con el texto" con
"cómo leer los bytes del socket".

Con `StreamEvent`, el stream de Ollama se convierte en un
`AsyncIterator[StreamEvent]`. Cada evento es una pieza tipada de lo
que llegó. El consumidor decide qué hacer con cada tipo. Esto:
  · desacopla el transporte de la UI
  · permite testear el transporte sin mocks de callbacks
  · hace explícito qué tipos de "cosa" puede emitir Ollama

Eventos actuales:
  · TextDelta(text)          — fragmento de texto para mostrar.
  · ToolCallsDelta(calls)    — llamadas a herramientas nativas detectadas.
  · StreamFinished(message)  — fin del stream, con el mensaje completo.

No se incluye `StreamError` como evento: los errores se propagan por
excepción (OllamaError, OllamaCancelled), no como eventos, para que
el consumidor no pueda "olvidarlos" silenciosamente.
"""
from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any


@dataclass(frozen=True)
class TextDelta:
    """Fragmento de texto generado por el modelo."""
    text: str


@dataclass(frozen=True)
class ToolCallsDelta:
    """Llamadas a herramientas nativas presentes en un chunk."""
    calls: tuple[dict[str, Any], ...]


@dataclass
class StreamFinished:
    """Fin del stream.

    `message` es el mensaje completo en formato Ollama (`role`,
    `content`, opcionalmente `tool_calls` y `_textual_tool_name`).
    Es mutable porque se construye incrementalmente durante el stream
    y se entrega al final ya completo.

    `metrics` contiene contadores reales de tokens reportados por
    Ollama en el último chunk con `done=true` (por ejemplo
    `prompt_eval_count`, `eval_count`, `prompt_eval_duration`). Si el
    modelo o la versión de Ollama no los envía, queda como dict vacío.
    """
    message: dict[str, Any] = field(default_factory=dict)
    metrics: dict[str, int] = field(default_factory=dict)
    # True si Ollama envió done=true antes de cerrar la conexión.
    # False si el socket se cerró sin done (corte de red, kill de
    # Ollama, EOF inesperado). Permite distinguir respuesta completa
    # de respuesta interrumpida.
    completed: bool = True
    # Motivo de cierre reportado por Ollama en el chunk final: "stop"
    # (fin normal), "length" (truncado por num_predict), "unload",
    # etc. None si no vino en el chunk.
    done_reason: str | None = None

    @property
    def content(self) -> str:
        return str(self.message.get("content", ""))


# Alias de tipo para consumidores. No se usa `TypeAlias` con `|` en
# Python 3.10+ porque el proyecto requiere 3.11+, así que la union
# está disponible.
StreamEvent = TextDelta | ToolCallsDelta | StreamFinished
