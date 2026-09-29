"""ChatPanel v2: mismo contrato que v1, paleta ambar.

Hereda de ChatPanel. Solo cambia el stylesheet del documento
Markdown para que use la paleta v2. Los atajos, señales, metodos
y atributos se conservan: AppController no necesita cambios.
"""
from __future__ import annotations

from collections.abc import Callable

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QTextEdit

from ..rendering import ChatRenderer, PlainTextRendererV2
from ..theme_v2 import ACCENT, BG_APP, DOCUMENT_STYLESHEET_V2
from .chat_panel import ChatPanel

RendererFactory = Callable[[QTextEdit], ChatRenderer]


def _apply_dark_palette(widget) -> None:
    """Fuerza el fondo del viewport al negro de la app.

    Combina palette + setStyleSheet directo al widget. Qt6 a veces
    ignora el palette en el viewport de QTextEdit si hay un estilo
    de sistema (macOS) que se aplica con mas especificidad. El
    setStyleSheet directo gana siempre.
    """
    pal = widget.palette()
    pal.setColor(QPalette.ColorRole.Base, QColor(BG_APP))
    pal.setColor(QPalette.ColorRole.Window, QColor(BG_APP))
    pal.setColor(QPalette.ColorRole.Text, QColor(ACCENT))
    widget.setPalette(pal)
    widget.viewport().setAutoFillBackground(True)
    widget.viewport().setPalette(pal)
    # Fuerza directa: gana a cualquier QSS global o al estilo del
    # sistema.
    widget.setStyleSheet(
        f"background-color: {BG_APP};"
        f"color: {ACCENT};"
    )


class ChatPanelV2(ChatPanel):
    def __init__(
        self,
        renderer_factory: RendererFactory = PlainTextRendererV2,
    ) -> None:
        super().__init__(renderer_factory)
        # 1. Stylesheet del documento Markdown con la paleta v2.
        self.chat.document().setDefaultStyleSheet(
            DOCUMENT_STYLESHEET_V2
        )
        # 2. Palette oscuro del viewport para el chat.
        _apply_dark_palette(self.chat)
        # 3. Idem para el input (QPlainTextEdit).
        _apply_dark_palette(self.input)
