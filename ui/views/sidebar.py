"""Panel lateral como widget autocontenido."""
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
    model_refresh_requested = Signal()
    model_selected = Signal(str)
    workspace_change_requested = Signal()
    mcp_toggle_requested = Signal(str, bool)
    agent_changed = Signal(str)
    agent_edit_requested = Signal()
    agent_create_requested = Signal()
    clear_chat_requested = Signal()

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(design.SIDEBAR_WIDTH_PX)
        self._busy = False
        self._mcp_buttons: dict[str, QPushButton] = {}
        self._mcp_states: dict[str, str] = {}
        self._mcp_labels: dict[str, str] = {}
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
        self.agent_new_button = QPushButton("Nuevo")
        self.agent_new_button.setObjectName("SecondaryButton")
        self.agent_new_button.clicked.connect(
            lambda: self.agent_create_requested.emit()
        )
        agent_row.addWidget(self.agent_new_button)
        self.agent_edit_button = QPushButton("Editar")
        self.agent_edit_button.setObjectName("SecondaryButton")
        self.agent_edit_button.clicked.connect(
            lambda: self.agent_edit_requested.emit()
        )
        agent_row.addWidget(self.agent_edit_button)
        layout.addLayout(agent_row)

        # -- MODELO (agrupado) --
        model_title = QLabel("MODELO")
        model_title.setObjectName("SectionTitle")
        layout.addWidget(model_title)

        self.model_combo = QComboBox()
        self.model_combo.currentTextChanged.connect(self.model_selected)
        layout.addWidget(self.model_combo)

        refresh = QPushButton("Actualizar modelos")
        refresh.setObjectName("SecondaryButton")
        refresh.clicked.connect(lambda: self.model_refresh_requested.emit())
        layout.addWidget(refresh)

        # Badge que muestra el modo de tool calling del modelo activo.
        # Se rellena en caliente cuando AppController consulta /api/show.
        self.capabilities_label = QLabel("")
        self.capabilities_label.setObjectName("CapabilitiesBadge")
        self.capabilities_label.setProperty("mode", "unknown")
        layout.addWidget(self.capabilities_label)

        # -- MCP --
        mcp_title = QLabel("MCP")
        mcp_title.setObjectName("SectionTitle")
        layout.addWidget(mcp_title)

        self.mcp_list = QVBoxLayout()
        self.mcp_list.setSpacing(6)
        layout.addLayout(self.mcp_list)

        # -- CARPETA DE TRABAJO --
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

        # -- SESIÓN --
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

    def set_capabilities(self, mode: str) -> None:
        """Refleja el modo de tool calling del modelo activo.

        mode puede ser 'native', 'xml' o 'unknown'.
        """
        labels = {
            "native": "  tool calling nativo",
            "xml": "  prompt-guided XML",
            "unknown": "  modo desconocido",
        }
        if mode not in labels:
            mode = "unknown"
        self.capabilities_label.setText(labels[mode])
        self.capabilities_label.setProperty("mode", mode)
        # Forzar el repintado para que el QSS con property selector
        # se aplique al cambio.
        self.capabilities_label.style().unpolish(self.capabilities_label)
        self.capabilities_label.style().polish(self.capabilities_label)

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
        entries: list[dict],
        active: list[str],
        pending: list[str],
        dead: list[str],
    ) -> None:
        """Refleja el estado de los servidores MCP configurados.

        Cada servidor se muestra como un botón con texto ON/OFF bien
        visible, sin checkboxes ni estilos raros.
        """
        active_set = set(active)
        pending_set = set(pending)
        dead_set = set(dead)
        self._mcp_states = {}
        self._mcp_labels = {}
        seen_ids = set()

        for entry in entries:
            sid = entry.get("id")
            if not sid:
                continue
            seen_ids.add(sid)
            self._mcp_labels[sid] = entry.get("label", sid)
            if sid in dead_set:
                self._mcp_states[sid] = "dead"
            elif sid in pending_set:
                self._mcp_states[sid] = "pending"
            elif sid in active_set:
                self._mcp_states[sid] = "active"
            else:
                self._mcp_states[sid] = "off"
            self._ensure_mcp_button(sid)

        # Limpiar botones huérfanos
        for sid in list(self._mcp_buttons):
            if sid not in seen_ids:
                button = self._mcp_buttons.pop(sid)
                self.mcp_list.removeWidget(button)
                button.deleteLater()
        self._render_mcp_buttons()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._apply_busy()

    # -- helpers -------------------------------------------------------------
    def _ensure_mcp_button(self, server_id: str) -> None:
        if server_id in self._mcp_buttons:
            return
        button = QPushButton("")
        button.setObjectName("McpToggle")
        button.setCheckable(True)
        button.setMinimumHeight(34)
        button.clicked.connect(
            lambda checked, sid=server_id: self.mcp_toggle_requested.emit(sid, checked)
        )
        self._mcp_buttons[server_id] = button
        self.mcp_list.addWidget(button)

    def _render_mcp_buttons(self) -> None:
        for sid, button in self._mcp_buttons.items():
            state = self._mcp_states.get(sid, "off")
            base = self._mcp_labels.get(sid, sid)
            blocked = button.blockSignals(True)

            if state == "dead":
                button.setChecked(False)
                button.setText(f"⚠  {base}   —   reconectar")
                button.setEnabled(not self._busy)
            elif state == "pending":
                button.setChecked(True)
                button.setText(f"◐  {base}   —   conectando…")
                button.setEnabled(False)
            elif state == "active":
                button.setChecked(True)
                button.setText(f"ON   ·   {base}")
                button.setEnabled(not self._busy)
            else:
                button.setChecked(False)
                button.setText(f"OFF  ·   {base}")
                button.setEnabled(not self._busy)

            button.blockSignals(blocked)

    def _apply_busy(self) -> None:
        self.model_combo.setEnabled(not self._busy)
        self._render_mcp_buttons()
