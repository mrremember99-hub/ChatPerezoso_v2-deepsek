"""Workers que corren en hilos aparte."""
from __future__ import annotations

import threading
import time
from typing import Any

from PySide6.QtCore import QObject, Signal

from core.ollama import OllamaCancelled, OllamaClient, OllamaError
from core.tool_result import ToolResult
from plugins.mcp import MCPClient, MCPError


# Tiempo máximo que un worker espera una confirmación del usuario.
# Sin límite, un cierre de ventana dejaba el worker colgado.
CONFIRMATION_TIMEOUT_SECONDS = 600  # 10 minutos


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


class CapabilitiesWorker(QObject):
    """Consulta /api/show para saber si el modelo soporta tools.

    Se ejecuta en un hilo aparte porque la consulta implica una
    peticion HTTP que puede tardar hasta 5s si Ollama esta ocupado.
    """

    finished = Signal(str, object)  # model_name, ModelCapabilities
    error = Signal(str, str)        # model_name, mensaje

    def __init__(self, host: str, model: str):
        super().__init__()
        self.host = host
        self.model = model

    def run(self) -> None:
        from core.model_capabilities import get_capabilities
        try:
            caps = get_capabilities(self.host, self.model)
            self.finished.emit(self.model, caps)
        except Exception as exc:
            self.error.emit(self.model, str(exc))


class MCPWorker(QObject):
    finished = Signal(str, object, list)
    error = Signal(str, str)

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
    tool_result = Signal(object)          # ToolResult
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
                self.tools,
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
        # Forzar el cierre de la respuesta HTTP activa. Sin esto, un
        # iter_lines() bloqueado en el socket no ve el cancel_event.
        try:
            self.client.force_close_active()
        except Exception:
            pass
        event = self._confirmation_event
        if event is not None:
            self._confirmation_approved = False
            event.set()

    # -- ejecución de herramientas -----------------------------------------

    def _call_tool(self, name: str, arguments: dict) -> str:
        self.tool.emit(name)
        start = time.monotonic()

        if self.tools.requires_confirmation(name):
            result = self._request_confirmation(name, arguments)
        else:
            result = self.tools.call(
                name,
                arguments,
                cancel_event=self._cancel_event,
            )

        duration_ms = int((time.monotonic() - start) * 1000)
        tool_result = self._build_result(name, result, duration_ms)
        self.tool_result.emit(tool_result)
        return tool_result.to_text()

    @staticmethod
    def _build_result(name: str, raw: str, duration_ms: int) -> ToolResult:
        raw = raw or "(sin resultado)"
        if raw.startswith("ERROR MCP") or raw.startswith("ERROR:"):
            return ToolResult(
                tool_name=name,
                summary=raw.split("\n", 1)[0],
                detail=raw,
                is_error=True,
                duration_ms=duration_ms,
            )
        if raw.startswith("OPERACIÓN CANCELADA"):
            return ToolResult(
                tool_name=name,
                summary="Operación cancelada por el usuario.",
                detail=raw,
                is_cancelled=True,
                duration_ms=duration_ms,
            )
        first_line, _, rest = raw.partition("\n")
        summary = first_line.strip()
        truncated = "truncad" in rest.lower()
        return ToolResult(
            tool_name=name,
            summary=summary,
            detail=rest.strip(),
            duration_ms=duration_ms,
            truncated=truncated,
        )

    def _request_confirmation(self, name: str, arguments: dict[str, Any]) -> str:
        event = threading.Event()
        self._confirmation_event = event
        self._confirmation_name = name
        self._confirmation_arguments = dict(arguments)
        self._confirmation_approved = False
        self.confirmation_requested.emit(name, dict(arguments))

        # Con timeout: si la UI no responde (ventana cerrada, por ejemplo),
        # el worker se desbloquea y sigue su curso.
        event.wait(timeout=CONFIRMATION_TIMEOUT_SECONDS)

        if self._confirmation_approved and not self._cancel_event.is_set():
            result = self.tools.call(
                name,
                arguments,
                allow_destructive=True,
                cancel_event=self._cancel_event,
            )
        elif self._cancel_event.is_set():
            result = (
                "OPERACIÓN CANCELADA POR EL USUARIO: "
                "no se ha ejecutado ninguna operación."
            )
        else:
            result = (
                "OPERACIÓN CANCELADA: no se recibió confirmación del usuario "
                "a tiempo. No se ha ejecutado ninguna operación."
            )

        self._confirmation_event = None
        self._confirmation_name = ""
        self._confirmation_arguments = {}
        self._confirmation_approved = False
        return result

    def resolve_confirmation(self, approved: bool) -> None:
        event = self._confirmation_event
        if event is None:
            return
        self._confirmation_approved = approved
        event.set()