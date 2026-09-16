from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QWidget

from core.ollama import OllamaClient
from plugins.mcp import MCPToolBridge
from .. import design
from ..rendering import ChatRenderer, PlainTextRenderer
from ..views.dialogs import confirm_tool
from ..workers import ChatWorker


class ChatController(QObject):
    streaming_changed = Signal(bool)
    status = Signal(str)
    assistant_message = Signal(str)
    error_message = Signal(str)

    def __init__(
        self,
        parent: QObject,
        parent_widget: QWidget,
        client: OllamaClient,
        bridge: MCPToolBridge,
        renderer: ChatRenderer,
    ):
        super().__init__(parent)
        self._parent_widget = parent_widget
        self.client = client
        self.bridge = bridge
        self.renderer = renderer
        self.messages: list[dict] = []
        self._thread: QThread | None = None
        self._worker: ChatWorker | None = None
        self._streaming = False

    def is_streaming(self) -> bool:
        return self._streaming

    def rebind_bridge(self, bridge: MCPToolBridge) -> None:
        self.bridge = bridge

    def send(self, text: str, model: str) -> None:
        if self._streaming or not text or not model:
            return

        self.renderer.insert_user_message(text)
        self.messages.append({"role": "user", "content": text})
        self.renderer.reset()
        self._streaming = True
        self.streaming_changed.emit(True)
        self.status.emit("Generando…")

        self._thread = QThread(self)
        self._worker = ChatWorker(self.client, model, list(self.messages), self.bridge)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.text.connect(self.renderer.on_text)
        self._worker.tool.connect(self._on_tool)
        self._worker.tool_result.connect(self._on_tool_result)
        self._worker.confirmation_requested.connect(self._on_confirmation)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._worker.cancelled.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup)
        self._thread.start()

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()

    def clear(self) -> None:
        if self._streaming:
            return
        self.messages.clear()

    def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)

    def _on_tool(self, name: str) -> None:
        self.renderer.insert_tool_event(
            f"Herramienta: {name}", design.TOOL_EVENT_COLOR
        )

    def _on_tool_result(self, name: str, result: str) -> None:
        if result.startswith("ERROR:"):
            label = f"Error en {name}"
            color = design.TOOL_EVENT_ERROR_COLOR
        elif result.startswith("OPERACIÓN CANCELADA"):
            label = "Operación cancelada"
            color = design.TOOL_EVENT_CANCELLED_COLOR
        else:
            label = f"Resultado: {name}"
            color = design.TOOL_EVENT_COLOR
        self.renderer.insert_tool_event(label, color)
        preview = result.strip()
        if len(preview) > 1200:
            preview = preview[:1200] + "\n…"
        if preview:
            self.renderer.insert_tool_result(preview)

    def _on_confirmation(self, name: str, arguments: dict[str, Any]) -> None:
        if self._worker is None:
            return
        approved = confirm_tool(self._parent_widget, name, arguments)
        self._worker.resolve_confirmation(approved)

    def _on_done(self, result: str) -> None:
        if not self.renderer.response_text and result:
            self.renderer.on_text(result)

        response_text = self.renderer.response_text or result
        response_text = PlainTextRenderer.clean_response_text(
            PlainTextRenderer.display_response_text(response_text)
        )
        self.messages.append({"role": "assistant", "content": response_text})
        self.assistant_message.emit(response_text)
        self._finish("Listo")

    def _on_cancelled(self) -> None:
        self._finish("Cancelado")

    def _on_error(self, message: str) -> None:
        self.renderer.insert_error(message)
        self._finish("Error")

    def _finish(self, status: str) -> None:
        self._streaming = False
        self.streaming_changed.emit(False)
        self.status.emit(status)
        self.renderer.reset()

    def _cleanup(self) -> None:
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None
