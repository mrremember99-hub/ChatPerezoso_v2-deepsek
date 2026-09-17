"""Panel lateral como widget autocontenido.

No conoce Ollama ni MCP: emite señales cuando el usuario interactúa y expone
métodos ``set_*`` para que el controlador refleje el estado.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QComboBox,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import design
from .diagnostics_panel import DiagnosticsPanel


class Sidebar(QWidget):
    # interacción del usuario
    model_refresh_requested = Signal()
    model_selected = Signal(str)
    workspace_change_requested = Signal()
    mcp_toggle_requested = Signal(bool)
    agent_changed = Signal(str)
    agent_edit_requested = Signal()
    clear_chat_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(design.SIDEBAR_WIDTH_PX)
        # Estado explícito del único servidor MCP soportado (archivos).
        self._mcp_active = False
        self._mcp_pending = False
        self._mcp_dead = False
        self._busy = False
        self._build()

    # -- construcción --------------------------------------------------------

    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        title = QLabel("PEREZOSO")
        title.setObjectName("AppTitle")
        layout.addWidget(title)

        agent_title = QLabel("AGENTE")
        agent_title.setObjectName("SectionTitle")
        layout.addWidget(agent_title)

        agent_row = QHBoxLayout()
        agent_row.setSpacing(4)
        self.agent_combo = QComboBox()
        self.agent_combo.currentTextChanged.connect(self.agent_changed)
        agent_row.addWidget(self.agent_combo, 1)
        self.agent_edit_button = QPushButton("Editar")
        self.agent_edit_button.setObjectName("SecondaryButton")
        self.agent_edit_button.clicked.connect(
            lambda: self.agent_edit_requested.emit()
        )
        agent_row.addWidget(self.agent_edit_button)
        layout.addLayout(agent_row)

        model_title = QLabel("MODELO")
        model_title.setObjectName("SectionTitle")
        layout.addWidget(model_title)

        self.model_combo = QComboBox()
        self.model_combo.currentTextChanged.connect(self.model_selected)
        layout.addWidget(self.model_combo)

        mcp_title = QLabel("MCP (ARCHIVOS)")
        mcp_title.setObjectName("SectionTitle")
        layout.addWidget(mcp_title)

        self.mcp_toggle_button = QPushButton("Activar MCP")
        self.mcp_toggle_button.setObjectName("SecondaryButton")
        self.mcp_toggle_button.setCheckable(True)
        self.mcp_toggle_button.toggled.connect(self._on_mcp_toggled)
        layout.addWidget(self.mcp_toggle_button)

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

        layout.addSpacing(14)
        diagnostics_title = QLabel("SESIÓN")
        diagnostics_title.setObjectName("SectionTitle")
        layout.addWidget(diagnostics_title)
        self.diagnostics = DiagnosticsPanel()
        layout.addWidget(self.diagnostics)

        layout.addStretch(1)

        clear = QPushButton("Nueva conversación")
        clear.clicked.connect(lambda: self.clear_chat_requested.emit())
        layout.addWidget(clear)

    # -- API pública ---------------------------------------------------------

    def set_models(self, models: list[str], current: str | None) -> None:
        blocked = self.model_combo.blockSignals(True)
        self.model_combo.clear()
        self.model_combo.addItems(models)
        if current and current in models:
            self.model_combo.setCurrentText(current)
        self.model_combo.blockSignals(blocked)

    def current_model(self) -> str:
        return self.model_combo.currentText().strip()

    def set_agents(self, names: list[str], current: str) -> None:
        blocked = self.agent_combo.blockSignals(True)
        self.agent_combo.clear()
        self.agent_combo.addItems(names)
        if current and current in names:
            self.agent_combo.setCurrentText(current)
        self.agent_combo.blockSignals(blocked)

    def current_agent(self) -> str:
        return self.agent_combo.currentText().strip()

    def set_workspace_name(self, name: str) -> None:
        self.workspace_label.setText(name)

    def set_mcp_servers(
        self,
        active: list[str],
        pending: list[str],
        dead: list[str] | None = None,
    ) -> None:
        """Refleja el estado del único servidor MCP soportado (archivos).

        Sólo importa si hay algo en cada lista, no el id concreto: la
        sidebar ya no distingue servidores por nombre.
        """
        self._mcp_active = bool(active)
        self._mcp_pending = bool(pending)
        self._mcp_dead = bool(dead)
        self._render_mcp_button()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._apply_busy()

    # -- helpers -------------------------------------------------------------

    def _on_mcp_toggled(self, checked: bool) -> None:
        self.mcp_toggle_requested.emit(checked)

    def _render_mcp_button(self) -> None:
        button = self.mcp_toggle_button
        blocked = button.blockSignals(True)
        if self._mcp_dead:
            button.setChecked(False)
            button.setText("MCP sin respuesta · reconectar")
            button.setEnabled(not self._busy)
        elif self._mcp_pending:
            button.setChecked(True)
            button.setText("Conectando MCP…")
            button.setEnabled(False)
        elif self._mcp_active:
            button.setChecked(True)
            button.setText("MCP activo (desactivar)")
            button.setEnabled(not self._busy)
        else:
            button.setChecked(False)
            button.setText("Activar MCP")
            button.setEnabled(not self._busy)
        button.blockSignals(blocked)

    def _apply_busy(self) -> None:
        self.model_combo.setEnabled(not self._busy)
        self._render_mcp_button()
