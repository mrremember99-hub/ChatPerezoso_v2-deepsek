"""Panel central: chat, indicadores y caja de entrada."""
from __future__ import annotations

from typing import Callable

from PySide6.QtCore import QElapsedTimer, QTimer, Signal
from PySide6.QtWidgets import (
    QHBoxLayout, QLabel, QPushButton, QTextEdit, QVBoxLayout, QWidget,
)

from .. import design
from ..rendering import ChatRenderer, PlainTextRenderer
from ..widgets import ChatInput, ChatView

RendererFactory = Callable[[QTextEdit], ChatRenderer]


class ChatPanel(QWidget):
    message_submitted = Signal(str)
    cancel_requested = Signal()

    def __init__(self, renderer_factory: RendererFactory = PlainTextRenderer) -> None:
        super().__init__()
        self.setObjectName("ChatArea")
        self._build()

        self.renderer: ChatRenderer = renderer_factory(self.chat)

        self._elapsed = QElapsedTimer()
        self._thinking_step = 0
        self._streaming = False

        self._thinking_timer = QTimer(self)
        self._thinking_timer.setInterval(design.THINKING_TIMER_INTERVAL_MS)
        self._thinking_timer.timeout.connect(self._animate_thinking)

        self._elapsed_timer = QTimer(self)
        self._elapsed_timer.setInterval(design.ELAPSED_TIMER_INTERVAL_MS)
        self._elapsed_timer.timeout.connect(self._update_elapsed)

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(28, 22, 28, 18)
        layout.setSpacing(12)

        self.chat = ChatView()
        self.chat.setObjectName("ChatView")
        self.chat.setReadOnly(True)
        self.chat.document().setUndoRedoEnabled(False)
        self.chat.document().setMaximumBlockCount(5000)
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

        self.send = QPushButton("Enviar")
        self.send.setFixedWidth(84)
        self.send.clicked.connect(self._on_send_clicked)
        input_row.addWidget(self.send)
        layout.addLayout(input_row)

    def _on_submit(self) -> None:
        if self._streaming:
            return
        text = self.input.toPlainText().strip()
        if text:
            self.message_submitted.emit(text)

    def _on_send_clicked(self) -> None:
        if self._streaming:
            self.cancel_requested.emit()
        else:
            self._on_submit()

    def set_streaming(self, streaming: bool) -> None:
        self._streaming = streaming
        self.send.setText("Detener" if streaming else "Enviar")
        self.input.setEnabled(not streaming)
        if streaming:
            self._start_indicators()
        else:
            self._stop_indicators()

    def clear_chat(self) -> None:
        self.chat.clear()
        self.renderer.reset()

    def take_input(self) -> str:
        text = self.input.toPlainText().strip()
        self.input.clear()
        return text

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
