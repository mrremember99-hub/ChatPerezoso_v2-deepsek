"""Panel lateral como widget autocontenido."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox, QHBoxLayout, QLabel, QPushButton, QVBoxLayout, QWidget,
)

from .. import design


class Sidebar(QWidget):
    model_refresh_requested = Signal()
    model_selected = Signal(str)
    workspace_change_requested = Signal()
    mcp_add_requested = Signal()
    mcp_deactivate_requested = Signal(str)
    clear_chat_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(design.SIDEBAR_WIDTH_PX)
        self._build()

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        title = QLabel("PEREZOSO")
        title.setObjectName("AppTitle")
        layout.addWidget(title)

        model_title = QLabel("MODELO")
        model_title.setObjectName("SectionTitle")
        layout.addWidget(model_title)

        self.model_combo = QComboBox()
        self.model_combo.currentTextChanged.connect(self.model_selected)
        layout.addWidget(self.model_combo)

        mcp_title = QLabel("SERVIDORES MCP")
        mcp_title.setObjectName("SectionTitle")
        layout.addWidget(mcp_title)

        self.mcp_add_button = QPushButton("Añadir servidor MCP")
        self.mcp_add_button.setObjectName("SecondaryButton")
        self.mcp_add_button.clicked.connect(lambda: self.mcp_add_requested.emit())
        layout.addWidget(self.mcp_add_button)

        self.mcp_servers_widget = QWidget()
        self.mcp_servers_layout = QVBoxLayout(self.mcp_servers_widget)
        self.mcp_servers_layout.setContentsMargins(0, 0, 0, 0)
        self.mcp_servers_layout.setSpacing(4)
        layout.addWidget(self.mcp_servers_widget)

        refresh = QPushButton("Actualizar modelos")
        refresh.setObjectName("SecondaryButton")
        refresh.clicked.connect(lambda: self.model_refresh_requested.emit())
        layout.addWidget(refresh)

        layout.addSpacing(14)
        workspace_title = QLabel("CARPETA DE TRABAJO")
        workspace_title.setObjectName("SectionTitle")
        layout.addWidget(workspace_title)

        self.workspace_label = QLabel("—")
        self.workspace_label.setObjectName("FolderLabel")
        self.workspace_label.setWordWrap(True)
        self.workspace_label.setAutoFillBackground(False)
        self.workspace_label.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        layout.addWidget(self.workspace_label)

        choose = QPushButton("Cambiar carpeta")
        choose.setObjectName("SecondaryButton")
        choose.clicked.connect(lambda: self.workspace_change_requested.emit())
        layout.addWidget(choose)
        layout.addStretch(1)

        clear = QPushButton("Nueva conversación")
        clear.clicked.connect(lambda: self.clear_chat_requested.emit())
        layout.addWidget(clear)

    def set_models(self, models: list[str], current: str | None) -> None:
        blocked = self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(models)
        if current and current in models:
            self.model_combo.setCurrentText(current)
        self.model_combo.blockSignals(blocked)

    def current_model(self) -> str:
        return self.model_combo.currentText().strip()

    def set_workspace_name(self, name: str) -> None:
        self.workspace_label.setText(name)

    def set_mcp_servers(self, active: list[str], pending: list[str]) -> None:
        while self.mcp_servers_layout.count():
            item = self.mcp_servers_layout.takeAt(0)
            if item is None:
                continue
            widget = item.widget()
            if widget is not None:
                widget.deleteLater()

        for server_id in active:
            self.mcp_servers_layout.addWidget(self._active_row(server_id))
        for server_id in pending:
            if server_id not in active:
                self.mcp_servers_layout.addWidget(self._pending_row(server_id))

    def set_busy(self, busy: bool) -> None:
        self.model_combo.setEnabled(not busy)
        has_pending = self._has_pending_mcp()
        self.mcp_add_button.setEnabled(not busy and not has_pending)
        for button in self.mcp_servers_widget.findChildren(QPushButton):
            button.setEnabled(not busy)

    def _active_row(self, server_id: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        label = QLabel(server_id)
        label.setObjectName("FolderLabel")
        button = QPushButton("Desactivar")
        button.setObjectName("SecondaryButton")
        button.clicked.connect(
            lambda checked=False, sid=server_id: self.mcp_deactivate_requested.emit(sid)
        )
        layout.addWidget(label, 1)
        layout.addWidget(button)
        return row

    def _pending_row(self, server_id: str) -> QWidget:
        row = QWidget()
        layout = QHBoxLayout(row)
        layout.setContentsMargins(0, 0, 0, 0)
        label = QLabel(f"{server_id} (conectando…)")
        label.setObjectName("FolderLabel")
        layout.addWidget(label)
        return row

    def _has_pending_mcp(self) -> bool:
        for i in range(self.mcp_servers_layout.count()):
            item = self.mcp_servers_layout.itemAt(i)
            if item is None:
                continue
            widget = item.widget()
            if widget is None:
                continue
            if widget.findChild(QPushButton) is None:
                return True
        return False
