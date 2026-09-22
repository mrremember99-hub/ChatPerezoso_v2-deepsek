"""Tests del guard de _render_markdown_block."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QTextEdit

from ui.rendering.plain_text import PlainTextRenderer


@pytest.fixture
def renderer(qapp):
    widget = QTextEdit()
    r = PlainTextRenderer(widget)
    yield r, widget
    widget.deleteLater()
    qapp.processEvents()


def test_render_markdown_aborts_when_positions_invalid(renderer):
    """Si response_start/segment_end están fuera del documento, no rompe.

    Antes: QTextCursor con posiciones inválidas no seleccionaba nada
    y el Markdown se perdía silenciosamente. Ahora resetea el estado.
    """
    r, _ = renderer
    r.on_text("hola mundo\n\n")  # deja segment_start/segment_end válidos
    # Ensuciar las posiciones para simular un estado inconsistente.
    r._response_start = 999_999
    r._segment_end = 999_999

    # No debe lanzar.
    r._render_markdown_block()

    # El estado debe quedar limpio.
    assert r._response_start is None
    assert r._segment_end is None


def test_render_markdown_aborts_when_positions_inverted(renderer):
    """Si segment_end < response_start, tampoco rompe."""
    r, _ = renderer
    r.on_text("texto\n\n")
    r._response_start = 5
    r._segment_end = 2

    r._render_markdown_block()

    assert r._response_start is None
    assert r._segment_end is None


def test_render_markdown_still_works_in_normal_case(renderer):
    """El guard no rompe el caso normal."""
    r, widget = renderer
    r.on_text("# Titulo\n\n")
    # Forzar el render del segmento completo.
    r.reset_response_segment()
    r.on_text("Cuerpo\n\n")

    # El documento tiene contenido.
    assert widget.document().characterCount() > 1
