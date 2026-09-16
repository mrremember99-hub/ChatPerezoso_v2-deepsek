import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

pytest.importorskip("PySide6")
from PySide6.QtWidgets import QApplication

from ui.main_window import MainWindow


def test_user_message_renders_as_bubble(monkeypatch):
    monkeypatch.setattr(MainWindow, "_load_models", lambda self: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window._insert_user_message("Pregunta de prueba")
        assert "Pregunta de prueba" in window.chat.toPlainText()
    finally:
        window.close()
        app.processEvents()


def test_assistant_response_is_constrained_to_75_percent_width(monkeypatch):
    monkeypatch.setattr(MainWindow, "_load_models", lambda self: None)
    app = QApplication.instance() or QApplication([])
    window = MainWindow()
    try:
        window.resize(1000, 700)
        window.chat.resize(760, 500)
        window._on_text("Respuesta de prueba")
        cursor = window.chat.textCursor()
        cursor.setPosition(window._response_start)
        block_format = cursor.blockFormat()
        assert block_format.rightMargin() > 0
        expected = window.chat.viewport().width() * 0.25
        assert abs(block_format.rightMargin() - expected) < 1.0
    finally:
        window.close()
        app.processEvents()
