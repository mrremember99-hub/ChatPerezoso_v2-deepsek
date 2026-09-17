from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import QWidget

from core.history import HistoryStore
from core.ollama import OllamaClient
from core.tool_provider import ToolProvider

from .. import design
from ..rendering import ChatRenderer
from ..views.dialogs import confirm_tool
from ..workers import ChatWorker


MAX_HISTORY_MESSAGES = 40


class ChatController(QObject):
    """Orquesta una conversación con Ollama y un ``ToolProvider``.

    Depende exclusivamente del Protocol ``ChatRenderer`` y de
    ``ToolProvider``. Persiste el historial tras cada turno. Permite
    regenerar la última respuesta sin retipear el mensaje del usuario.
    """

    streaming_changed = Signal(bool)
    status = Signal(str)
    assistant_message = Signal(str)
    error_message = Signal(str)
    conversation_changed = Signal()
    mcp_error = Signal(str)

    def __init__(
        self,
        parent: QObject,
        parent_widget: QWidget,
        client: OllamaClient,
        tools: ToolProvider,
        renderer: ChatRenderer,
        store: HistoryStore | None = None,
        initial_messages: list[dict] | None = None,
    ):
        super().__init__(parent)
        self._parent_widget = parent_widget
        self.client = client
        self.tools = tools
        self.renderer = renderer
        self.store = store or HistoryStore()
        self.messages: list[dict] = list(initial_messages or [])
        self._thread: QThread | None = None
        self._worker: ChatWorker | None = None
        self._streaming = False
        self._last_model = ""
        self._last_options: dict[str, Any] | None = None
        self._last_system_prompt = ""

    # -- API pública ---------------------------------------------------------

    def is_streaming(self) -> bool:
        return self._streaming

    def rebind_tools(self, tools: ToolProvider) -> None:
        self.tools = tools

    def set_current_model(self, model: str) -> None:
        self._last_model = model

    def set_current_options(self, options: dict[str, Any] | None) -> None:
        self._last_options = dict(options) if options else None

    def set_current_system_prompt(self, system_prompt: str) -> None:
        self._last_system_prompt = system_prompt or ""

    def last_assistant_text(self) -> str:
        """Devuelve el texto de la última respuesta del asistente, o ""."""
        for message in reversed(self.messages):
            if message.get("role") == "assistant":
                content = message.get("content")
                return content if isinstance(content, str) else ""
        return ""

    def send(
        self,
        text: str,
        model: str,
        options: dict[str, Any] | None = None,
        system_prompt: str | None = None,
    ) -> None:
        if self._streaming or not text or not model:
            return

        self._last_model = model
        if options is not None:
            self._last_options = dict(options)
        if system_prompt is not None:
            self._last_system_prompt = system_prompt

        self.renderer.insert_user_message(text)
        self._append_message({"role": "user", "content": text})
        self.renderer.reset()
        self._streaming = True
        self.streaming_changed.emit(True)
        self.status.emit("Generando…")
        self._spawn_worker(model, self._last_options, self._last_system_prompt)

    def regenerate(self, model: str) -> None:
        """Reenvía el último mensaje del usuario sin retipearlo.

        Elimina el mensaje del asistente posterior (si lo hay) y el
        bloque visual correspondiente. Si el último mensaje del usuario
        terminó en cancelación o error, simplemente se reintenta.
        """
        if self._streaming or not self.messages or not model:
            return

        last_user_idx: int | None = None
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i].get("role") == "user":
                last_user_idx = i
                break
        if last_user_idx is None:
            return

        user_text = str(self.messages[last_user_idx].get("content", ""))
        if not user_text:
            return

        self._last_model = model
        # Truncamos el historial hasta justo antes del mensaje a regenerar.
        self.messages = self.messages[:last_user_idx]
        # Borramos la parte visual desde ese mensaje del usuario hasta el final.
        self.renderer.remove_from_last_user()
        # send() se encarga de reinsertar el mensaje de usuario y arrancar worker.
        self.send(
            user_text,
            model,
            self._last_options,
            self._last_system_prompt,
        )

    def _spawn_worker(
        self,
        model: str,
        options: dict[str, Any] | None = None,
        system_prompt: str = "",
    ) -> None:
        """Crea el worker y el thread de la generación actual."""
        self._thread = QThread(self)
        self._worker = ChatWorker(
            self.client,
            model,
            list(self.messages),
            self.tools,
            options=options,
            system_prompt=system_prompt,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        # Los slots del renderer deben ejecutarse en el main thread. Al
        # conectar a un método del propio ChatController (QObject del main
        # thread), Qt usa una QueuedConnection automáticamente. Conectar
        # directamente `self._worker.text` al renderer saltaría el cambio
        # de hilo y podría tocar QTextEdit desde el worker.
        self._worker.text.connect(self._on_text_chunk)
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
        self.store.clear()
        self.conversation_changed.emit()

    def shutdown(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
        if self._thread is not None and self._thread.isRunning():
            self._thread.quit()
            self._thread.wait(2000)
        self._persist()

    # -- historial -----------------------------------------------------------

    def _append_message(self, message: dict) -> None:
        self.messages.append(message)
        if len(self.messages) > MAX_HISTORY_MESSAGES:
            cut = len(self.messages) - MAX_HISTORY_MESSAGES
            while cut < len(self.messages) and self.messages[cut].get("role") != "user":
                cut += 1
            if cut < len(self.messages):
                self.messages = self.messages[cut:]
        self._persist()
        self.conversation_changed.emit()

    def _persist(self) -> None:
        self.store.save(self.messages, model=self._last_model)

    # -- slots internos ------------------------------------------------------

    @Slot(str)
    def _on_text_chunk(self, text: str) -> None:
        """Slot intermedio entre el worker y el renderer.

        Se ejecuta en el main thread (Qt lo encola automáticamente porque
        el receptor es un QObject). El renderer nunca se llama directo
        desde el hilo del worker.
        """
        self.renderer.on_text(text)

    def _on_tool(self, name: str) -> None:
        self.renderer.insert_tool_event(
            f"Herramienta: {name}", design.TOOL_EVENT_COLOR
        )

    def _on_tool_result(self, name: str, result: str) -> None:
        if result.startswith("ERROR MCP"):
            server_id = self._extract_mcp_server(name)
            if server_id:
                self.mcp_error.emit(server_id)
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
        response_text = self.renderer.final_text(result)
        self._append_message({"role": "assistant", "content": response_text})
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

    @staticmethod
    def _extract_mcp_server(tool_name: str) -> str | None:
        if not tool_name.startswith("mcp__"):
            return None
        parts = tool_name.split("__", 2)
        if len(parts) < 2 or not parts[1]:
            return None
        return parts[1]
