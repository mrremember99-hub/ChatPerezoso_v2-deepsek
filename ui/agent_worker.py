"""AgentWorker — QObject que envuelve AgentSession en hilo Qt.

Traduce eventos del núcleo (core.agent.events) a señales Qt.
El adapter (OllamaAdapter) ya traduce dicts de Ollama a
ModelDelta; este worker traduce eventos del agente a señales
de UI.

Sin EventLog, sin loop detection, sin health. Solo las señales
que la UI consume.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from PySide6.QtCore import QObject, Signal

from core.agent.events import (
    AgentError,
    Event,
    MessageDelta,
    StepEnded,
    ToolCallCompleted,
    ToolCallRequested,
)
from core.agent.session import AgentSession
from core.approval import CONFIRMATION_TIMEOUT_SECONDS
from core.tool_result import ToolResult

logger = logging.getLogger(__name__)


class AgentWorker(QObject):
    """Un step del agente, ejecutado en un QThread.

    Uso típico:

        worker = AgentWorker(session, user_message)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.stream_ready.connect(controller._schedule_stream_drain)
        worker.tool.connect(controller._on_tool)
        ... (10 señales)
        thread.start()

    Cancelación: `cancel()` marca el event del worker Y llama a
    `session.cancel()`. El run() sale limpiamente y emite
    `cancelled`.
    """

    stream_ready = Signal()
    tool = Signal(str)
    tool_result = Signal(object)          # ToolResult
    confirmation_requested = Signal(str, object, str)
    tool_auto_approved = Signal(str)
    metrics_updated = Signal(object)
    finished = Signal(str)
    cancelled = Signal()
    error = Signal(str)
    summary_ready = Signal(str, int)

    def __init__(
        self,
        session: AgentSession,
        user_message: str,
        *,
        parent: QObject | None = None,
    ) -> None:
        super().__init__(parent)
        self._session = session
        self._user_message = user_message
        self._cancel = threading.Event()
        self._confirmation_lock = threading.Lock()
        self._confirmation_event: threading.Event | None = None
        self._confirmation_approved = False
        self._text_parts: list[str] = []
        # Cachear arguments por call_id. ToolCallCompleted no lleva
        # arguments, pero el trace del turno siguiente los necesita
        # para el hint `tool(path)`.
        self._pending_args: dict[str, dict] = {}
        # drain_text corre en el hilo de UI (timer), _on_message_delta
        # en el hilo del worker. Sin lock, un delta anadido entre el
        # join y el clear se perdia.
        self._text_parts_lock = threading.Lock()
        self._step_index = -1
        # Evita emitir finished si el step acabo en error.
        self._error_emitted = False

    # ── contrato ModelClient-side ──────────────────────────

    def handle_confirmation(
        self, name: str, arguments: dict[str, Any],
        *, reason: str = "",
    ) -> bool:
        """Se inyecta como `confirmation_handler` de la session.

        Bloquea el hilo del worker hasta que la UI llame a
        `resolve_confirmation(bool)` o expire el timeout.
        """
        event = threading.Event()
        with self._confirmation_lock:
            self._confirmation_event = event
            self._confirmation_approved = False
        self.confirmation_requested.emit(
            name, dict(arguments), reason,
        )
        event.wait(timeout=CONFIRMATION_TIMEOUT_SECONDS)
        with self._confirmation_lock:
            approved = self._confirmation_approved
            self._confirmation_event = None
            self._confirmation_approved = False
        return approved

    def resolve_confirmation(self, approved: bool) -> None:
        """Llamado desde el hilo de UI. Desbloquea al worker."""
        with self._confirmation_lock:
            event = self._confirmation_event
            if event is not None:
                self._confirmation_approved = approved
        if event is not None:
            event.set()

    def cancel(self) -> None:
        """Cancelación cooperativa. Idempotente."""
        self._cancel.set()
        with self._confirmation_lock:
            event = self._confirmation_event
            if event is not None:
                self._confirmation_approved = False
        if event is not None:
            event.set()
        self._session.cancel()

    def drain_text(self) -> str:
        """Devuelve y limpia el buffer de texto acumulado."""
        with self._text_parts_lock:
            parts = self._text_parts
            self._text_parts = []
        return "".join(parts)

    # ── ciclo ──────────────────────────────────────────────

    def run(self) -> None:
        """Ejecuta el step. Consume eventos y los traduce a señales."""
        self._error_emitted = False
        try:
            for event in self._session.step(self._user_message):
                self._translate(event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("AgentWorker.run fallo")
            self.error.emit(str(exc) or type(exc).__name__)
            return

        if self._cancel.is_set():
            self.cancelled.emit()

    # ── traducción de eventos ──────────────────────────────

    def _translate(self, event: Event) -> None:
        if isinstance(event, MessageDelta):
            self._on_message_delta(event)
        elif isinstance(event, ToolCallRequested):
            self._on_tool_requested(event)
        elif isinstance(event, ToolCallCompleted):
            self._on_tool_completed(event)
        elif isinstance(event, AgentError):
            self._on_agent_error(event)
        elif isinstance(event, StepEnded):
            self._on_step_ended(event)

    def _on_message_delta(self, event: MessageDelta) -> None:
        content = getattr(event, "content", "")
        if not content:
            return
        with self._text_parts_lock:
            self._text_parts.append(content)
        self.stream_ready.emit()

    def _on_tool_requested(self, event: ToolCallRequested) -> None:
        name = str(event.tool_name)
        call_id = str(getattr(event, "call_id", ""))
        if call_id:
            args = getattr(event, "arguments", None)
            self._pending_args[call_id] = (
                dict(args) if isinstance(args, dict) else {}
            )
        self.tool.emit(name)
        if getattr(event, "auto_approved", False):
            self.tool_auto_approved.emit(name)

    def _on_tool_completed(self, event: ToolCallCompleted) -> None:
        status = str(event.status)
        call_id = str(getattr(event, "call_id", ""))
        args = self._pending_args.pop(call_id, {}) if call_id else {}
        metadata: dict = {}
        if args:
            metadata["arguments"] = args
        result = ToolResult(
            tool_name=str(event.tool_name),
            summary=str(event.summary),
            detail=str(event.detail),
            is_error=status == "error",
            is_cancelled=status == "cancelled",
            duration_ms=int(event.duration_ms),
            metadata=metadata,
        )
        self.tool_result.emit(result)

    def _on_agent_error(self, event: AgentError) -> None:
        self._error_emitted = True
        self.error.emit(str(event.message))

    def _on_step_ended(self, event: StepEnded) -> None:
        self._step_index = int(event.step_index)
        outcome = str(event.outcome)
        # finished vs cancelled/error son excluyentes.
        # ChatController._on_done interpreta finished como
        # "turno completado". Si el step acabo en cancel o error
        # y emitimos finished, el siguiente prompt de la cola
        # arrancaria con un turno cancelado a medias.
        if outcome == "cancelled":
            return  # run() emitira cancelled al salir del for
        if outcome == "failed":
            if not self._error_emitted:
                self.error.emit("El step termino con fallo")
            return
        full_text = self.drain_text()
        self.finished.emit(full_text)
