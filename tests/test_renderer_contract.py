"""Verifica que PlainTextRenderer cumple ChatRenderer.

`ChatRenderer` es un `runtime_checkable Protocol`. `isinstance()`
sobre un Protocol solo comprueba que los metodos/propiedades existen
por nombre (no las firmas). Es una verificacion debil pero suficiente
como alarma: si alguien anade un metodo al Protocol y olvida
implementarlo en PlainTextRenderer, este test falla.

Mismo contrato debe cumplir el futuro WidgetListRenderer de la
migracion.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QTextEdit

from ui.rendering import ChatRenderer, PlainTextRenderer


@pytest.fixture
def renderer(qapp):
    widget = QTextEdit()
    r = PlainTextRenderer(widget)
    yield r, widget
    widget.deleteLater()
    qapp.processEvents()


def test_plain_text_is_chat_renderer(renderer):
    r, _ = renderer
    assert isinstance(r, ChatRenderer)


def test_protocol_declares_required_methods():
    """El Protocol debe declarar los 11 metodos + 2 propiedades.

    Si alguien elimina un metodo del Protocol sin querer, este test
    lo caza. No verifica firmas, solo presencia.
    """
    required = {
        "response_text",
        "response_start",
        "reset",
        "reset_response_segment",
        "insert_user_message",
        "on_text",
        "insert_narration",
        "insert_tool_card",
        "insert_error",
        "final_text",
        "restore_assistant_message",
        "remove_from_last_user",
    }
    for name in required:
        assert hasattr(ChatRenderer, name), (
            f"ChatRenderer perdio {name}"
        )
