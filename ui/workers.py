"""Workers que corren en hilos aparte: orquestan Ollama/MCP y no tocan
directamente ningún widget (se comunican solo por señales Qt).
"""
from __future__ import annotations

import threading
from typing import Any

from PySide6.QtCore import QObject, Signal

from core.ollama import OllamaCancelled, OllamaClient, OllamaError
from plugins.mcp import MCPClient, MCPError


class ModelWorker(QObject):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, client: OllamaClient):
        super().__init__()
        self.client = client

    def run(self) -> None:
        try:
            self.finished.emit(self.client.list_models())
        except OllamaError as exc:
            self.error.emit(str(exc))


class MCPWorker(QObject):
    """Consulta las herramientas de un servidor MCP en su propio hilo."""

    finished = Signal(str, object, list)  # server_id, client, tools
    error = Signal(str, str)              # server_id, message

    def __init__(self, server_id: str, client: MCPClient):
        super().__init__()
        self.server_id = server_id
        self.client = client

    def run(self) -> None:
        try:
            tools = self.client.list_tools()
            self.finished.emit(self.server_id, self.client, tools)
        except MCPError as exc:
            self.error.emit(self.server_id, str(exc))


class ChatWorker(QObject):
    text = Signal(str)
    tool = Signal(str)
    tool_result = Signal(str, str)
    confirmation_requested = Signal(str, object)
    finished = Signal(str)
    cancelled = Signal()
    error = Signal(str)

    def __init__(
        self,
        client: OllamaClient,
        model: str,
        messages: list[dict],
        tools: Any,
        options: dict | None = None,
        system_prompt: str = "",
    ):
        super().__init__()
        self.client = client
        self.model = model
        self.messages = messages
        self.tools = tools
        self.options = options
        self.system_prompt = system_prompt
        self._cancel_event = threading.Event()
        self._confirmation_event: threading.Event | None = None
        self._confirmation_name = ""
        self._confirmation_arguments: dict[str, Any] = {}
        self._confirmation_approved = False

    def run(self) -> None:
        try:
            result = self.client.chat(
                self.model,
                self.messages,
                self.tools,   # el provider completo, no solo las definiciones
                self.text.emit,
                self._call_tool,
                cancel_event=self._cancel_event,
                options=self.options,
                system_prompt=self.system_prompt,
            )
            self.finished.emit(result)
        except OllamaCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.error.emit(str(exc))

    def cancel(self) -> None:
        self._cancel_event.set()
        event = self._confirmation_event
        if event is not None:
            self._confirmation_approved = False
            event.set()

    def _call_tool(self, name: str, arguments: dict) -> str:
        self.tool.emit(name)
        if self.tools.requires_confirmation(name):
            result = self._request_confirmation(name, arguments)
        else:
            result = self.tools.call(
                name,
                arguments,
                cancel_event=self._cancel_event,
            )
        self.tool_result.emit(name, result)
        return result

    def _request_confirmation(self, name: str, arguments: dict[str, Any]) -> str:
        """Pide confirmación a la UI y ejecuta el tool en este mismo hilo.

        La UI solo marca aprobado/rechazado y libera el evento; la ejecución
        del tool sigue viviendo en el hilo del worker, que es donde se hizo
        la llamada original a ``client.chat``. Así no hay carreras entre el
        hilo de UI y este al usar ``self.tools``.
        """
        event = threading.Event()
        self._confirmation_event = event
        self._confirmation_name = name
        self._confirmation_arguments = dict(arguments)
        self._confirmation_approved = False
        self.confirmation_requested.emit(name, dict(arguments))
        event.wait()

        if self._confirmation_approved and not self._cancel_event.is_set():
            result = self.tools.call(
                name,
                arguments,
                allow_destructive=True,
                cancel_event=self._cancel_event,
            )
        else:
            result = (
                "OPERACIÓN CANCELADA POR EL USUARIO: "
                "no se ha ejecutado ninguna operación."
            )

        self._confirmation_event = None
        self._confirmation_name = ""
        self._confirmation_arguments = {}
        self._confirmation_approved = False
        return result

    def resolve_confirmation(self, approved: bool) -> None:
        """Llamado desde el hilo de UI. Solo marca el flag y libera el evento."""
        event = self._confirmation_event
        if event is None:
            return
        self._confirmation_approved = approved
        event.set()
