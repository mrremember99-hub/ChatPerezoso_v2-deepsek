"""Tests de PlainTextRendererV2 y de la paleta inyectada."""
from __future__ import annotations

import dataclasses

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QTextEdit

from ui import design, theme_v2
from ui.rendering import (
    ChatRenderer,
    PlainTextRenderer,
    PlainTextRendererV2,
)
from ui.rendering.palette import V1_PALETTE, V2_PALETTE, RendererPalette


@pytest.fixture
def widget(qapp):
    w = QTextEdit()
    yield w
    w.deleteLater()
    qapp.processEvents()


def test_v2_is_chat_renderer(widget):
    r = PlainTextRendererV2(widget)
    assert isinstance(r, ChatRenderer)


def test_v1_default_palette_matches_design(widget):
    """Sin palette= explicito, v1 debe usar exactamente design."""
    r = PlainTextRenderer(widget)
    assert r.palette == V1_PALETTE
    assert r.palette.response_text == design.RESPONSE_TEXT_COLOR
    assert r.palette.user_message_bg == design.USER_MESSAGE_BG_COLOR
    assert r.palette.user_message_text == design.USER_MESSAGE_TEXT_COLOR


def test_v2_palette_uses_theme_v2(widget):
    """La paleta v2 debe coincidir con los colores del theme."""
    r = PlainTextRendererV2(widget)
    assert r.palette == V2_PALETTE
    assert r.palette.response_text == theme_v2.ACCENT
    assert r.palette.user_message_bg == theme_v2.BOX_FILL
    assert r.palette.narration == theme_v2.ACCENT_DIM
    assert r.palette.tool_card_border == theme_v2.ACCENT_BORDER


def test_v1_and_v2_palettes_differ_on_visible_colors(widget):
    """El bug original: v1 y v2 deben pintar distinto."""
    v1 = PlainTextRenderer(widget)
    v2 = PlainTextRendererV2(widget)
    assert v1.palette.user_message_bg != v2.palette.user_message_bg
    assert v1.palette.response_text != v2.palette.response_text
    assert v1.palette.user_label != v2.palette.user_label


def test_v1_and_v2_render_user_message_text(widget):
    for cls in (PlainTextRenderer, PlainTextRendererV2):
        w = QTextEdit()
        r = cls(w)
        r.insert_user_message("hola mundo")
        assert "hola mundo" in w.toPlainText()
        w.deleteLater()


def test_renderer_palette_is_frozen():
    assert dataclasses.is_dataclass(RendererPalette)
    with pytest.raises(dataclasses.FrozenInstanceError):
        V1_PALETTE.response_text = "#000000"  # type: ignore[misc]
