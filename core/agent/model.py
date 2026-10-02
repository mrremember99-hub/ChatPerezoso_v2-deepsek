"""Interfaz minima que el harness necesita de un cliente LLM.

Spec: docs/harness-v3.md §1.3.

No depende de Ollama ni de httpx. Cualquier cliente que implemente
chat() devolviendo un Iterator[ModelDelta] sirve (Ollama real,
FakeModelClient en tests, OpenAI en el futuro).
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from dataclasses import dataclass
from typing import Any, Protocol


@dataclass(frozen=True)
class ModelDelta:
    """Unidad de salida del modelo durante el streaming.

    kind:
      · "text"      — fragmento de texto (campo text)
      · "tool_call" — el modelo pidio una tool (campo tool_call)
      · "done"      — fin de la respuesta; text puede contener el
                      total si el cliente no emitio deltas.
    """

    kind: str
    text: str = ""
    tool_call: dict[str, Any] | None = None


class ModelClient(Protocol):
    """Contrato minimo. El harness solo usa chat()."""

    def chat(
        self,
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[ModelDelta]: ...
