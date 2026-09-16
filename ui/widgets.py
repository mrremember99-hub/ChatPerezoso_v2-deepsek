"""Widgets pequeños y autocontenidos del chat."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtGui import QKeyEvent
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit


class ChatView(QTextEdit):
    """Vista del chat. Los mensajes de usuario se insertan como tablas HTML
    alineadas a la derecha; el widget no necesita pintar nada por su cuenta."""
    pass


class ChatInput(QPlainTextEdit):
    submitted = Signal()

    def keyPressEvent(self, event: QKeyEvent) -> None:
        if event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter) and not (
            event.modifiers() & Qt.KeyboardModifier.ShiftModifier
        ):
            self.submitted.emit()
            return
        super().keyPressEvent(event)
