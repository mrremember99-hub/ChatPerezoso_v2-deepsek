"""HarnessWorker — QObject que envuelve HarnessSession en hilo Qt.

S6-b-1b (auditoria externa, diseño P2#24). Replica el contrato de
señales de ChatWorker para que ChatController no cambie en S6-b-2.

Traduce eventos del harness a señales Qt. El adapter
(OllamaAdapter) ya traduce dicts de Ollama a ModelDelta; este
worker traduce eventos del harness a señales de UI.

Señales sin equivalente en el harness (por ahora vacías):
  · tool_auto_approved — el harness no emite un evento aparte.
  · metrics_updated    — idem (los tokens van en métricas de
                         ChatWorker, no del harness).
  · summary_ready      — el resumen rolling no está portado a S4;
                         se gestiona en el controller hasta
                         rediseño.
"""
from __future__ import annotations

import logging
import threading
from typing import Any

from PySide6.QtCore import QObject, Signal

from core.harness.events import (
    Event,
    HarnessError,
    LoopAborted,
    LoopCorrectivePrompt,
    LoopWarning,
    MessageDelta,
    StepEnded,
    ToolCallCompleted,
    ToolCallRequested,
)
from core.harness.session import HarnessSession
from core.tool_result import ToolResult

logger = logging.getLogger(__name__)


from core.approval import CONFIRMATION_TIMEOUT_SECONDS


class HarnessWorker(QObject):
    """Un step del harness, ejecutado en un QThread.

    Uso típico (mismo patrón que ChatWorker):

        worker = HarnessWorker(session, user_message)
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.stream_ready.connect(controller._schedule_stream_drain)
        worker.tool.connect(controller._on_tool)
        ... (13 señales)
        thread.start()

    Cancelación: `cancel()` marca el event del worker Y llama a
    `session.cancel()`. El run() sale limpiamente y emite
    `cancelled`.
    """

    # Mismas señales que ChatWorker (13).
    stream_ready = Signal()
    tool = Signal(str)
    tool_result = Signal(object)          # ToolResult
    confirmation_requested = Signal(str, object, str)
    tool_auto_approved = Signal(str)
    metrics_updated = Signal(object)
    loop_warning = Signal(str, str)
    loop_corrective = Signal(str, str)
    loop_aborted = Signal(str, str)
    finished = Signal(str)
    cancelled = Signal()
    error = Signal(str)
    summary_ready = Signal(str, int)

    def __init__(
        self,
        session: HarnessSession,
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
        self._step_index = -1

    # -- puente hacia la session -----------------------------------

    @property
    def auto_approve(self):
        """Proxy mutable a HarnessConfig.auto_approve.

        HarnessConfig es frozen; la reasignacion sustituye el
        objeto entero via dataclasses.replace. La session lee
        config.auto_approve en cada step, asi que el cambio
        surte efecto inmediato (2026-10-01).
        """
        return self._session.config.auto_approve

    @auto_approve.setter
    def auto_approve(self, enabled) -> None:
        from dataclasses import replace
        self._session.config = replace(
            self._session.config,
            auto_approve=bool(enabled),
        )

    @property
    def auto_approve_shell(self):
        return self._session.config.auto_approve_shell

    @auto_approve_shell.setter
    def auto_approve_shell(self, enabled) -> None:
        from dataclasses import replace
        self._session.config = replace(
            self._session.config,
            auto_approve_shell=bool(enabled),
        )

    @property
    def verificador_hook(self):
        """Hook de verificacion post-escritura (proxy).

        ChatController.set_verificador_hook asigna este atributo
        en el worker vivo. La implementacion real vive en la
        HarnessSession; aqui delegamos. Sin esto, la asignacion
        era un no-op silencioso (2026-10-01).
        """
        return self._session.verificador_hook

    @verificador_hook.setter
    def verificador_hook(self, hook) -> None:
        self._session.verificador_hook = hook

    # -- contrato ModelClient-side ---------------------------------

    def handle_confirmation(
        self, name: str, arguments: dict[str, Any],
        *, reason: str = "",
    ) -> bool:
        """Se inyecta como `confirmation_handler` de la session.

        Bloquea el hilo del worker hasta que la UI llame a
        `resolve_confirmation(bool)` o expire el timeout.
        El `reason` (2026-10-01) se reenvia por la senal
        para que el dialogo lo muestre al usuario.
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
        text = "".join(self._text_parts)
        self._text_parts.clear()
        return text

    # -- ciclo -----------------------------------------------------

    def run(self) -> None:
        """Ejecuta el step. Consume eventos y los traduce a señales."""
        try:
            for event in self._session.step(self._user_message):
                self._translate(event)
        except Exception as exc:  # noqa: BLE001
            logger.exception("HarnessWorker.run fallo")
            self.error.emit(str(exc) or type(exc).__name__)
            return

        if self._cancel.is_set():
            self.cancelled.emit()

    # -- traduccion de eventos -------------------------------------

    def _translate(self, event: Event) -> None:
        # mypy no narrowing por ClassVar kind: usamos isinstance
        # para que las firmas tipadas de los handlers encajen.
        if isinstance(event, MessageDelta):
            self._on_message_delta(event)
        elif isinstance(event, ToolCallRequested):
            self._on_tool_requested(event)
        elif isinstance(event, ToolCallCompleted):
            self._on_tool_completed(event)
        elif isinstance(event, LoopWarning):
            self._on_loop_warning(event)
        elif isinstance(event, LoopCorrectivePrompt):
            self._on_loop_corrective(event)
        elif isinstance(event, LoopAborted):
            self._on_loop_aborted(event)
        elif isinstance(event, HarnessError):
            self._on_harness_error(event)
        elif isinstance(event, StepEnded):
            self._on_step_ended(event)
        # Otros eventos (message_completed, confirmation_*,
        # verification_*, health_snapshot, etc.) no tienen señal
        # asociada en el contrato de ChatWorker.

    def _on_message_delta(self, event: MessageDelta) -> None:
        content = getattr(event, "content", "")
        if not content:
            return
        self._text_parts.append(content)
        self.stream_ready.emit()

    def _on_tool_requested(self, event: ToolCallRequested) -> None:
        self.tool.emit(str(event.tool_name))

    def _on_tool_completed(self, event: ToolCallCompleted) -> None:
        status = str(event.status)
        result = ToolResult(
            tool_name=str(event.tool_name),
            summary=str(event.summary),
            detail=str(event.detail),
            is_error=status == "error",
            is_cancelled=status == "cancelled",
            duration_ms=int(event.duration_ms),
            metadata={},
        )
        self.tool_result.emit(result)

    def _on_loop_warning(self, event: LoopWarning) -> None:
        reason = (
            f"{event.count} repeticiones ({event.detector})"
        )
        self.loop_warning.emit(str(event.detector), reason)

    def _on_loop_corrective(
        self, event: LoopCorrectivePrompt,
    ) -> None:
        # El prompt completo va al modelo; la UI solo ve el motivo.
        reason = str(event.prompt)[:120]
        self.loop_corrective.emit(str(event.detector), reason)

    def _on_loop_aborted(self, event: LoopAborted) -> None:
        self.loop_aborted.emit(str(event.detector), str(event.reason))

    def _on_harness_error(self, event: HarnessError) -> None:
        self.error.emit(str(event.message))

    def _on_step_ended(self, event: StepEnded) -> None:
        self._step_index = int(event.step_index)
        # Si hubo error, ya se emitió desde HarnessError. Emitimos
        # finished de todas formas para que ChatController cierre el
        # estado (mismo patrón que ChatWorker).
        full_text = self.drain_text()
        self.finished.emit(full_text)
