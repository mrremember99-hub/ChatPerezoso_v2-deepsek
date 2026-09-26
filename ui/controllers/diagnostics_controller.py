"""Calcula las estadísticas de sesión y las refleja en el panel.

Escucha las señales del ``ChatController``:
  · ``state_changed(STREAMING)``  → arranca el cronómetro
  · ``state_changed(CANCELLING)`` → ignora (el worker aún trabaja)
  · ``state_changed(IDLE/ERROR)`` → cierra el cronómetro y cuenta
  · ``conversation_changed``      → recalcula los tokens del contexto
  · ``textual_tool_attempt``      → cuenta intentos de tool-call textual

No mide las tool calls individuales: la suma del tiempo ya incluye su
coste y el usuario ve en el chat cuándo se han ejecutado.
"""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QElapsedTimer, QObject

from ..chat_state import ChatState
from ..diagnostics import SessionStats
from ..views.diagnostics_panel import DiagnosticsPanel
from .chat_controller import ChatController


# Umbral mínimo para que una generación cuente como respuesta.
# Por debajo de este tiempo se asume cancelación inmediata o error
# instantáneo, y no se contabiliza. Es una constante de módulo para
# que los tests puedan ajustarla sin depender del reloj real.
MIN_RESPONSE_SECONDS = 0.3


class DiagnosticsController(QObject):
    # Atributo usado por tests para mantener viva la referencia al
    # QObject padre. La app real no lo asigna.
    _owner: Any = None

    def __init__(
        self,
        parent: QObject | None,
        chat: ChatController | Any,
        panel: DiagnosticsPanel,
    ):
        super().__init__(parent)
        self.chat = chat
        self.panel = panel
        self.stats = SessionStats()
        self._timer = QElapsedTimer()
        self._in_flight = False

        # Preferimos state_changed (mas expresivo). Fallback a
        # streaming_changed para fakes de tests que solo tienen el
        # signal booleano.
        state_signal = getattr(chat, "state_changed", None)
        if state_signal is not None:
            state_signal.connect(self._on_state_changed)
        else:
            chat.streaming_changed.connect(self._on_streaming_changed)
        chat.conversation_changed.connect(self._on_conversation_changed)
        # La senal textual_tool_attempt puede no existir en fakes
        # de tests. La conectamos si esta disponible.
        textual_signal = getattr(chat, "textual_tool_attempt", None)
        if textual_signal is not None:
            textual_signal.connect(self._on_textual_tool_attempt)

    # -- config del modelo ---------------------------------------------------

    def set_metrics(self, metrics: dict) -> None:
        """Formatea y muestra las métricas reales de la última ronda.

        Ollama envía `prompt_eval_count` (tokens de prompt), `eval_count`
        (tokens generados) y `eval_duration` (nanosegundos) en el chunk
        final. Si el modelo o la versión no los envía, se oculta la
        línea.
        """
        if not metrics:
            self.panel.set_last_metrics("")
            return
        prompt_tokens = int(metrics.get("prompt_eval_count", 0))
        gen_tokens = int(metrics.get("eval_count", 0))
        eval_ns = int(metrics.get("eval_duration", 0))
        tok_s = (
            gen_tokens / (eval_ns / 1e9) if eval_ns > 0 else 0.0
        )
        parts: list[str] = []
        if prompt_tokens:
            parts.append(f"{prompt_tokens} prompt")
        if gen_tokens:
            parts.append(f"{gen_tokens} gen")
        if tok_s > 0:
            parts.append(f"{tok_s:.1f} tok/s")
        if not parts:
            self.panel.set_last_metrics("")
            return
        self.panel.set_last_metrics("Real: " + " · ".join(parts))

    def set_model(self, name: str, temperature: float, num_ctx: int) -> None:
        self.stats.model = name
        self.stats.temperature = temperature
        self.stats.num_ctx = num_ctx
        self._refresh_model()

    # -- contadores ----------------------------------------------------------

    def _on_state_changed(self, state: ChatState) -> None:
        """Handler principal: reacciona a los cambios de estado del chat."""
        if state is ChatState.STREAMING:
            # Nueva generación: arrancar cronómetro.
            self._timer.start()
            self._in_flight = True
        elif state is ChatState.CANCELLING:
            # El usuario pulsó Detener pero el worker aún no ha
            # terminado. Mantenemos el cronómetro corriendo: el
            # tiempo de cancelación forma parte del tiempo total.
            pass
        elif self._in_flight:
            # IDLE o ERROR: fin de la generación. Contamos como
            # respuesta solo si hubo texto real y pasó el umbral.
            self._in_flight = False
            elapsed = self._timer.elapsed() / 1000
            last_text = ""
            last_text_getter = getattr(self.chat, "last_assistant_text", None)
            if callable(last_text_getter):
                last_text = last_text_getter()
            if elapsed >= MIN_RESPONSE_SECONDS and last_text:
                self.stats.add_response(elapsed)
                self._refresh_responses()
        self._refresh_textual_tool()
        self.refresh_context()

    def _on_streaming_changed(self, streaming: bool) -> None:
        """Fallback para fakes de tests con la señal booleana antigua."""
        if streaming:
            self._on_state_changed(ChatState.STREAMING)
        else:
            self._on_state_changed(ChatState.IDLE)

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
