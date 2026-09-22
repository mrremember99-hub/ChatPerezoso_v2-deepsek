"""Regresión: warn() fuerza PlainText en el mensaje.

Antes, QMessageBox.warning() auto-detectaba RichText. Un mensaje con
sintaxis que pareciera HTML (por ejemplo un path con `<`) podía
renderizarse como HTML. Ahora el formato se fuerza a PlainText.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import Qt


def test_warn_uses_plain_text(qapp, monkeypatch):
    """Verifica que warn() configura PlainText antes de exec()."""
    from ui.views import dialogs as dialogs_module

    captured: dict = {}

    class FakeBox:
        class Icon:
            Warning = "warning"

        def __init__(self, parent):
            captured["parent"] = parent

        def setIcon(self, icon):
            captured["icon"] = icon

        def setWindowTitle(self, title):
            captured["title"] = title

        def setText(self, text):
            captured["text"] = text

        def setTextFormat(self, fmt):
            captured["format"] = fmt

        def exec(self):
            captured["exec"] = True
            return 0

    monkeypatch.setattr(dialogs_module, "QMessageBox", FakeBox)

    dialogs_module.warn(None, "Aviso", "<b>no html</b>")

    assert captured.get("title") == "Aviso"
    assert captured.get("text") == "<b>no html</b>"
    assert captured.get("format") == Qt.TextFormat.PlainText
    assert captured.get("exec") is True


def test_warn_passes_parent_to_box(qapp, monkeypatch):
    """El box recibe el parent (para centrarse sobre la ventana)."""
    from PySide6.QtWidgets import QWidget
    from ui.views import dialogs as dialogs_module

    captured: dict = {}

    class FakeBox:
        class Icon:
            Warning = "warning"

        def __init__(self, parent):
            captured["parent"] = parent

        def setIcon(self, _): pass
        def setWindowTitle(self, _): pass
        def setText(self, _): pass
        def setTextFormat(self, _): pass
        def exec(self): return 0

    monkeypatch.setattr(dialogs_module, "QMessageBox", FakeBox)

    parent = QWidget()
    dialogs_module.warn(parent, "t", "m")

    assert captured.get("parent") is parent
    