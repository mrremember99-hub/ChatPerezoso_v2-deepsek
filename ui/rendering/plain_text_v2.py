"""PlainTextRenderer con paleta v2 (ambar)."""
from __future__ import annotations

from PySide6.QtWidgets import QTextEdit

from .palette import V2_PALETTE
from .plain_text import PlainTextRenderer


class PlainTextRendererV2(PlainTextRenderer):
    """Identico a v1 salvo la paleta de colores.

    El streaming, el markdown, las tarjetas de herramientas y la
    narracion se heredan tal cual. Aqui solo cambiamos la paleta.
    """

    def __init__(self, chat: QTextEdit):
        super().__init__(chat, palette=V2_PALETTE)
