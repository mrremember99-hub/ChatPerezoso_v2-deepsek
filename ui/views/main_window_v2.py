"""Cascaron v2 de la ventana principal.

Cambio estetico base: header con logo + sloth, splitter con
proporciones 320/645/310, tema oscuro ambar sobre negro.

Reutiliza los paneles v1 (Sidebar, ChatPanel, RightPanel) para no
romper la interfaz que espera AppController. Los paneles v2 se
sustituyen uno a uno en sesiones posteriores.

Uso:
    CHATPEREZOSO_UI=v2 python bootstrap.py
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap
from PySide6.QtSvg import QSvgRenderer
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMainWindow,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from .. import theme_v2
from .chat_panel_v2 import ChatPanelV2 as ChatPanel
from .right_panel_v2 import RightPanelV2 as RightPanel
from .sidebar_v2 import SidebarV2 as Sidebar


class _Header(QWidget):
    """Header con logo a la izquierda y sloth a la derecha."""

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Header")
        self.setFixedHeight(theme_v2.HEADER_HEIGHT)

        layout = QHBoxLayout(self)
        layout.setContentsMargins(16, 4, 16, 4)
        layout.setSpacing(12)

        logo = QLabel("perezoso")
        logo.setObjectName("Logo")
        layout.addWidget(logo)

        layout.addStretch(1)

        self.status = QLabel("Listo")
        self.status.setObjectName("HeaderStatus")
        layout.addWidget(self.status)

        sloth = QLabel()
        sloth.setFixedSize(56, 56)
        if theme_v2.SLOTH_SVG.exists():
            renderer = QSvgRenderer(str(theme_v2.SLOTH_SVG))
            pm = QPixmap(56, 56)
            pm.fill(Qt.transparent)
            from PySide6.QtGui import QPainter
            painter = QPainter(pm)
            renderer.render(painter)
            painter.end()
            sloth.setPixmap(pm)
        layout.addWidget(sloth)

    def set_text(self, text: str) -> None:
        self.status.setText(text)


class MainWindowV2(QMainWindow):
    """Ventana principal v2.

    Expone los mismos atributos que MainWindow v1 para que
    AppController no necesite cambios:
      - self.sidebar
      - self.chat_panel
      - self.right_panel
      - self.set_status(text)
    """

    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PEREZOSO")

        central = QWidget()
        self.setCentralWidget(central)
        outer = QVBoxLayout(central)
        outer.setContentsMargins(0, 0, 0, 0)
        outer.setSpacing(0)

        self._header = _Header()
        outer.addWidget(self._header)

        splitter = QSplitter(Qt.Horizontal)
        splitter.setChildrenCollapsible(False)
        splitter.setHandleWidth(6)
        outer.addWidget(splitter, 1)

        self.sidebar = Sidebar()
        splitter.addWidget(self.sidebar)

        self.chat_panel = ChatPanel()
        splitter.addWidget(self.chat_panel)

        self.right_panel = RightPanel()
        splitter.addWidget(self.right_panel)

        # Proporciones del SVG: 320 / 645 / 310.
        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)
        splitter.setStretchFactor(2, 0)
        splitter.setSizes([
            theme_v2.SIDEBAR_WIDTH,
            645,
            theme_v2.RIGHT_PANEL_WIDTH,
        ])

        # Status bar v1 que AppController puede seguir usando.
        # Ocultamos la statusbar estandar y usamos el header.
        self.statusBar().hide()
        # Atributo `status` que MainWindow v1 expone y algunos
        # tests/controllers pueden consultar directamente.
        self.status = self._header.status

    def set_status(self, text: str) -> None:
        self._header.set_text(text)
