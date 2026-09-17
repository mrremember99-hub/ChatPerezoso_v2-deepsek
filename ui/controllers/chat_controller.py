from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QThread, Signal, Slot
from PySide6.QtWidgets import QWidget

from core.history import HistoryStore
from core.ollama import OllamaClient
from core.tool_provider import ToolProvider
from core.tool_result import ToolResult

from ..rendering import ChatRenderer
from ..views.dialogs import confirm_tool
from ..workers import ChatWorker


MAX_HISTORY_MESSAGES = 60
RECENT_TURNS_TO_KEEP = 12


_NARRATION_TEMPLATES = {
    "buscar_en_workspace": "Buscando en el workspace…",
    "leer_archivo": "Leyendo archivo…",
    "listar_carpeta": "Listando carpeta…",
    "crear_archivo": "Creando archivo…",
    "escribir_archivo": "Escribiendo archivo…",
    "borrar_archivo": "Borrando archivo…",
    "crear_carpeta": "Creando carpeta…",
    "ejecutar_comando": "Ejecutando comando…",
    "git_status": "Consultando estado de Git…",
    "git_diff": "Obteniendo diferencias…",
    "git_log": "Consultando historial de Git…",
    "git_show": "Mostrando commit…",
}


class ChatController(QObject):
    streaming_changed = Signal(bool)
    status = Signal(str)
    assistant_message = Signal(str)
    error_message = Signal(str)
    conversation_changed = Signal()
    mcp_error = Signal(str)
    textual_tool_attempt = Signal()

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
        self._current_actions: list[ToolResult] = []

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
        self._current_actions = []
        self._streaming = True
        self.streaming_changed.emit(True)
        self.status.emit("Generando…")
        self._spawn_worker(model, self._last_options, self._last_system_prompt)

    def regenerate(self, model: str) -> None:
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
        if last_user_idx < len(self.messages) - 1:
            self.messages = self.messages[:last_user_idx]
        self.renderer.remove_from_last_user()
        self.send(user_text, model, self._last_options, self._last_system_prompt)

    def _spawn_worker(self, model, options=None, system_prompt=""):
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
        self._compact_if_needed()
        self._persist()
        self.conversation_changed.emit()

    def _compact_if_needed(self) -> None:
        """Trunca por turnos user/assistant, no por mensaje.

        Antes se cortaba por número de mensajes, lo que podía dejar un
        mensaje ``tool`` huérfano (sin su ``assistant``) y confundir al
        modelo. Ahora se conservan los últimos N turnos completos.
        """
        if len(self.messages) <= MAX_HISTORY_MESSAGES:
            return
        user_positions = [
            i for i, m in enumerate(self.messages) if m.get("role") == "user"
        ]
        if len(user_positions) <= RECENT_TURNS_TO_KEEP:
            return
        cut_at = user_positions[-RECENT_TURNS_TO_KEEP]
        self.messages = self.messages[cut_at:]

    def _persist(self) -> None:
        self.store.save(self.messages, model=self._last_model)

    # -- slots internos ------------------------------------------------------
    @Slot(str)
    def _on_text_chunk(self, text: str) -> None:
        self.renderer.on_text(text)

    def _on_tool(self, name: str) -> None:
        narration = _NARRATION_TEMPLATES.get(name, f"Ejecutando {name}…")
        self.renderer.insert_narration(narration, active=True)

    def _on_tool_result(self, result: ToolResult) -> None:
        if result.is_error and result.summary.startswith("ERROR MCP"):
            server_id = self._extract_mcp_server(result.tool_name)
            if server_id:
                self.mcp_error.emit(server_id)
        self._current_actions.append(result)
        self.renderer.insert_tool_card(result)

    def _on_confirmation(self, name: str, arguments: dict[str, Any]) -> None:
        if self._worker is None:
            return
        approved = confirm_tool(self._parent_widget, name, arguments)
        self._worker.resolve_confirmation(approved)

    def _on_done(self, result: str) -> None:
        response_text = self.renderer.final_text(result)
        # Detectar intento de tool calling textual en el texto final.
        # OllamaClient devuelve este mensaje cuando el modelo escribio
        # el JSON como texto dos veces seguidas.
        if "escribiste el JSON de la herramienta" in response_text.lower() or \
           "no logro invocar" in response_text.lower() or \
           "no logró invocar" in response_text.lower():
            self.textual_tool_attempt.emit()
        summary = self._summarize_actions()
        if summary:
            self.renderer.insert_narration(summary, active=False)
        self._append_message({"role": "assistant", "content": response_text})
        self.assistant_message.emit(response_text)
        self._finish("Listo")

    def _summarize_actions(self) -> str:
        actions = self._current_actions
        if not actions:
            return ""
        ok = sum(1 for a in actions if a.status == "ok")
        err = sum(1 for a in actions if a.status == "error")
        cancelled = sum(1 for a in actions if a.status == "cancelled")
        total_ms = sum(a.duration_ms for a in actions)
        parts = [f"{ok} correcta(s)"]
        if err:
            parts.append(f"{err} con error")
        if cancelled:
            parts.append(f"{cancelled} cancelada(s)")
        return (
            f"Se ejecutaron {len(actions)} herramienta(s): "
            + ", ".join(parts)
            + f" · tiempo total {total_ms} ms"
        )

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