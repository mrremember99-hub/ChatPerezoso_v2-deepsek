"""Panel central: chat, indicadores y caja de entrada."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QElapsedTimer, QTimer, Signal
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QPushButton,
    QTextEdit,
    QVBoxLayout,
    QWidget,
)

from .. import design
from ..chat_state import ChatState
from ..rendering import ChatRenderer, PlainTextRenderer
from ..theme import DOCUMENT_STYLESHEET
from ..widgets import ChatInput, ChatView

RendererFactory = Callable[[QTextEdit], ChatRenderer]


class ChatPanel(QWidget):
    message_submitted = Signal()
    send_all_requested = Signal()
    cancel_requested = Signal()
    regenerate_requested = Signal()
    copy_requested = Signal()
    clear_requested = Signal()

    def __init__(self, renderer_factory: RendererFactory = PlainTextRenderer) -> None:
        super().__init__()
        self.setObjectName("ChatArea")
        self._build()

        self.renderer: ChatRenderer = renderer_factory(self.chat)

        self._elapsed = QElapsedTimer()
        self._thinking_step = 0
        self._state: ChatState = ChatState.IDLE

        self._thinking_timer = QTimer(self)
        self._thinking_timer.setInterval(design.THINKING_TIMER_INTERVAL_MS)
        self._thinking_timer.timeout.connect(self._animate_thinking)

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(design.ELAPSED_TIMER_INTERVAL_MS)
        self._elapsed_timer.timeout.connect(self._update_elapsed)

        self._install_shortcuts()

    # -- construcción --------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 18)
        layout.setSpacing(12)

        self.chat = ChatView()
        self.chat.setObjectName("ChatView")
        self.chat.setReadOnly(True)
        self.chat.document().setUndoRedoEnabled(False)
        self.chat.document().setMaximumBlockCount(5000)
        # CSS para el HTML insertado (Markdown renderizado, tarjetas).
        self.chat.document().setDefaultStyleSheet(DOCUMENT_STYLESHEET)
        layout.addWidget(self.chat, 1)

        indicator_row = QHBoxLayout()
        indicator_row.setSpacing(8)
        self.thinking_label = QLabel("•••")
        self.thinking_label.setObjectName("ThinkingLabel")
        self.thinking_label.hide()
        indicator_row.addWidget(self.thinking_label)

        self.elapsed_label = QLabel("")
        self.elapsed_label.setObjectName("ElapsedIndicator")
        self.elapsed_label.hide()
        indicator_row.addWidget(self.elapsed_label)
        indicator_row.addStretch(1)
        layout.addLayout(indicator_row)

        input_row = QHBoxLayout()
        input_row.setSpacing(8)
        self.input = ChatInput()
        self.input.setObjectName("ChatInput")
        self.input.setFixedHeight(design.CHAT_INPUT_HEIGHT_PX)
        self.input.setPlaceholderText(
            "Escribe un mensaje. Enter para enviar, Shift+Enter para nueva línea."
        )
        self.input.submitted.connect(self._on_submit)
        input_row.addWidget(self.input, 1)

        self.send_all = QPushButton("Enviar todo")
        self.send_all.setFixedWidth(120)
        self.send_all.setToolTip(
            "Divide el texto por líneas con `---` o `===` y los envía "
            "en secuencia, esperando a que cada turno termine."
        )
        self.send_all.clicked.connect(self._on_send_all_clicked)
        input_row.addWidget(self.send_all)

        self.send = QPushButton("Enviar")
        self.send.setFixedWidth(84)
        self.send.clicked.connect(self._on_send_clicked)
        input_row.addWidget(self.send)
        layout.addLayout(input_row)

    def _install_shortcuts(self) -> None:
        QShortcut(QKeySequence("Ctrl+R"), self).activated.connect(
            self.regenerate_requested.emit
        )
        QShortcut(QKeySequence("Ctrl+Shift+C"), self).activated.connect(
            self.copy_requested.emit
        )
        QShortcut(QKeySequence("Ctrl+L"), self).activated.connect(
            self._on_clear_shortcut
        )
        QShortcut(QKeySequence("Ctrl+K"), self).activated.connect(
            self.input.setFocus
        )

    # -- señales de entrada --------------------------------------------------
    def _on_submit(self) -> None:
        if self._state.is_active:
            return
        if not self.input.toPlainText().strip():
            return
        self.message_submitted.emit()

    def _on_send_clicked(self) -> None:
        if self._state.is_active:
            # Feedback inmediato: aunque la cancelación real puede tardar
            # unos ms (el evento se propaga al worker asincrono), el
            # usuario ve que su pulsación se ha registrado.
            self.send.setText("Cancelando…")
            self.send.setEnabled(False)
            self.cancel_requested.emit()
        else:
            self._on_submit()

    def _on_send_all_clicked(self) -> None:
        if self._state.is_active:
            return
        if not self.input.toPlainText().strip():
            return
        self.send_all_requested.emit()

    def _on_clear_shortcut(self) -> None:
        if self._state.is_active:
            return
        self.clear_requested.emit()

    # -- API pública ---------------------------------------------------------
    @property
    def state(self) -> ChatState:
        """Estado actual del panel."""
        return self._state

    def set_streaming(self, streaming: bool) -> None:
        """Compatibilidad: convierte bool a ChatState."""
        self.set_state(ChatState.STREAMING if streaming else ChatState.IDLE)

    def set_state(self, state: ChatState) -> None:
        """Refleja el estado del ChatController en la UI.

        IDLE / ERROR: botón "Enviar", input activo, indicadores parados.
        STREAMING:    botón "Detener", input deshabilitado, indicadores activos.
        CANCELLING:   botón "Cancelando…" deshabilitado, indicadores activos.
        """
        self._state = state

        if state is ChatState.STREAMING:
            self.send.setText("Detener")
            self.send.setEnabled(True)
            self.input.setEnabled(False)
            self._start_indicators()
        elif state is ChatState.CANCELLING:
            self.send.setText("Cancelando…")
            self.send.setEnabled(False)
            self.input.setEnabled(False)
            # No reiniciamos los indicadores: siguen corriendo mientras
            # el worker termina de cancelar.
        else:
            # IDLE o ERROR
            self.send.setText("Enviar")
            self.send.setEnabled(True)
            self.input.setEnabled(True)
            self._stop_indicators()

        # "Enviar todo" solo tiene sentido cuando no hay turno activo.
        self.send_all.setEnabled(not state.is_active)

    def clear_chat(self) -> None:
        self.chat.clear()
        self.renderer.reset()

    def restore_conversation(self, messages: list[dict]) -> None:
        for message in messages:
            role = message.get("role")
            content = message.get("content")
            if not isinstance(content, str) or not content:
                continue
            if role == "user":
                self.renderer.insert_user_message(content)
            elif role == "assistant":
                self.renderer.restore_assistant_message(content)

    def take_input(self) -> str:
        text = self.input.toPlainText().strip()
        self.input.clear()
        return text

    # -- indicadores ---------------------------------------------------------
    def _start_indicators(self) -> None:
        self._thinking_step = 0
        self.thinking_label.setText("•••")
        self.thinking_label.show()
        self._elapsed.start()
        self.elapsed_label.setText("● 0.0 s")
        self.elapsed_label.show()
        self._thinking_timer.start()
        self._elapsed_timer.start()

    def _stop_indicators(self) -> None:
        self._thinking_timer.stop()
        self._elapsed_timer.stop()
        self.thinking_label.hide()
        seconds = self._elapsed.elapsed() / 1000 if self._elapsed.isValid() else 0.0
        self.elapsed_label.setText(f"● {seconds:.1f} s")

    def _animate_thinking(self) -> None:
        states = ("•", "••", "•••")
        self._thinking_step = (self._thinking_step + 1) % len(states)
        self.thinking_label.setText(states[self._thinking_step])

    def _update_elapsed(self) -> None:
        self.elapsed_label.setText(f"● {self._elapsed.elapsed() / 1000:.1f} s")
