"""Calcula las estadísticas de sesión y las refleja en el panel.

Escucha las señales del ``ChatController`` y del ``Sidebar``:
  · ``streaming_changed(True)``  → arranca el cronómetro
  · ``streaming_changed(False)`` → cierra el cronómetro y cuenta la respuesta
  · ``conversation_changed``     → recalcula los tokens del contexto
  · ``assistant_message``        → asegura el recuento tras respuesta

No mide las tool calls individuales: la suma del tiempo ya incluye su coste
y el usuario ve en el chat cuándo se han ejecutado.
"""
from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QObject

from ..diagnostics import SessionStats
from ..views.diagnostics_panel import DiagnosticsPanel
from .chat_controller import ChatController


# Umbral mínimo para que una generación cuente como respuesta.
# Por debajo de este tiempo se asume cancelación inmediata o error
# instantáneo, y no se contabiliza. Es una constante de módulo para
# que los tests puedan ajustarla sin depender del reloj real.
MIN_RESPONSE_SECONDS = 0.3


class DiagnosticsController(QObject):
    def __init__(
        self,
        parent: QObject,
        chat: ChatController,
        panel: DiagnosticsPanel,
    ):
        super().__init__(parent)
        self.chat = chat
        self.panel = panel
        self.stats = SessionStats()
        self._timer = QElapsedTimer()
        self._in_flight = False

        chat.streaming_changed.connect(self._on_streaming_changed)
        chat.conversation_changed.connect(self._on_conversation_changed)
        # La senal textual_tool_attempt puede no existir en fakes
        # de tests. La conectamos si esta disponible.
        textual_signal = getattr(chat, "textual_tool_attempt", None)
        if textual_signal is not None:
            textual_signal.connect(self._on_textual_tool_attempt)

    # -- config del modelo ---------------------------------------------------

    def set_model(self, name: str, temperature: float, num_ctx: int) -> None:
        self.stats.model = name
        self.stats.temperature = temperature
        self.stats.num_ctx = num_ctx
        self._refresh_model()

    # -- contadores ----------------------------------------------------------

    def _on_streaming_changed(self, streaming: bool) -> None:
        if streaming:
            self._timer.start()
            self._in_flight = True
        elif self._in_flight:
            self._in_flight = False
            elapsed = self._timer.elapsed() / 1000
            # Contamos como respuesta solo si hubo texto real. Una
            # cancelación de 1s sin respuesta no debe inflar la media.
            last_text = ""
            last_text_getter = getattr(self.chat, "last_assistant_text", None)
            if callable(last_text_getter):
                last_text = last_text_getter()
            if elapsed >= MIN_RESPONSE_SECONDS and last_text:
                self.stats.add_response(elapsed)
                self._refresh_responses()
        self._refresh_textual_tool()
        self.refresh_context()

    def _on_conversation_changed(self) -> None:
        self.refresh_context()

    def _on_textual_tool_attempt(self) -> None:
        self.stats.note_textual_tool()
        self._refresh_textual_tool()

    def reset(self) -> None:
        self.stats.reset_metrics()
        self._refresh_responses()
        self.refresh_context()

    def refresh_context(self) -> None:
        """Fuerza el recálculo de tokens del contexto.

        Se expone público para que el AppController pueda invocarlo tras
        operaciones que cambian el historial sin pasar por el
        conversation_changed del ChatController (restauración).
        """
        self.stats.update_context(self.chat.messages)
        self.panel.set_context_tokens(self.stats.context_tokens)

    # -- refresco ------------------------------------------------------------

    def _refresh_model(self) -> None:
        self.panel.set_model(
            self.stats.model,
            self.stats.temperature,
            self.stats.num_ctx,
        )

    def _refresh_textual_tool(self) -> None:
        self.panel.set_textual_tool(
            self.stats.textual_tool_attempts,
            self.stats.responses,
        )

    def _refresh_responses(self) -> None:
        self.panel.set_responses(
            self.stats.responses,
            self.stats.average_response_seconds,
        )

    def _refresh_context(self) -> None:
        self.stats.update_context(self.chat.messages)
        self.panel.set_context_tokens(self.stats.context_tokens)
