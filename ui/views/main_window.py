"""Cascarón de la ventana principal."""
from __future__ import annotations

from PySide6.QtWidgets import QLabel, QMainWindow, QSplitter

from .chat_panel import ChatPanel
from .sidebar import Sidebar


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.setWindowTitle("PEREZOSO")

        splitter = QSplitter()
        splitter.setChildrenCollapsible(False)
        self.setCentralWidget(splitter)

        self.sidebar = Sidebar()
        splitter.addWidget(self.sidebar)

        self.chat_panel = ChatPanel()
        splitter.addWidget(self.chat_panel)

        splitter.setStretchFactor(0, 0)
        splitter.setStretchFactor(1, 1)

        self.status = QLabel("Listo")
        self.status.setObjectName("StatusText")
        self.statusBar().addWidget(self.status, 1)

    def set_status(self, text: str) -> None:
        self.status.setText(text)
