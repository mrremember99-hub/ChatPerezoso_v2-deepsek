"""OllamaAdapter — ModelClient sobre OllamaClient.

Diseño: docs/harness-p2-24-adapter.md (opcion A1).

El harness (S4-b) controla el bucle de agente: pide una ronda al
ModelClient, decide si ejecutar tools, y vuelve a pedir. El
OllamaAdapter traduce entre ese contrato y el metodo
`OllamaClient.chat_once()` (S6-a), que hace exactamente una
ronda HTTP sin bucle interno ni ejecucion de tools.

Separacion de capas: `core/ollama.py` no conoce `ModelDelta`; el
adapter es quien traduce los dicts `{kind: text|tool_call|done}`
que devuelve `chat_once()`.
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from core.harness.model import ModelDelta

if TYPE_CHECKING:
    from core.ollama import OllamaClient


class OllamaAdapter:
    """Implementa el Protocol ModelClient sobre un OllamaClient.

    Uso:
        adapter = OllamaAdapter(client, model="qwen3:30b-a3b",
                                options={"temperature": 0.1})
        for delta in adapter.chat(messages):
            ...

    La config (model, options) se pasa por constructor, no por
    chat(), porque el Protocol ModelClient no tiene canal para
    parametros por request. Coincide con el diseño P2#24.
    """

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
        messages: list[dict[str, Any]],
        *,
        tools: list[dict[str, Any]] | None = None,
        stream: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[ModelDelta]:
        """Una ronda. Delega en OllamaClient.chat_once() y traduce
        los dicts planos a ModelDelta."""
        for raw in self._client.chat_once(
            self._model,
            messages,
            tools=tools,
            options=self._options,
            cancel_event=cancel_event,
        ):
            if not isinstance(raw, dict):
                continue
            kind = raw.get("kind")
            if kind == "text":
                text = raw.get("text", "")
                if isinstance(text, str) and text:
                    yield ModelDelta(kind="text", text=text)
            elif kind == "tool_call":
                tc = raw.get("tool_call")
                if isinstance(tc, dict):
                    yield ModelDelta(kind="tool_call", tool_call=tc)
            elif kind == "done":
                yield ModelDelta(kind="done")
