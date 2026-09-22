"""Panel lateral como widget autocontenido."""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QCheckBox,
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
    auto_approve_changed = Signal(bool)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("Sidebar")
        self.setFixedWidth(design.SIDEBAR_WIDTH_PX)
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

        # Recomendación de uso del modelo activo. Puede ser una
        # heurística automática o un texto del usuario en models.json.
        self.recommendation_label = QLabel("")
        self.recommendation_label.setObjectName("RecommendationLabel")
        self.recommendation_label.setWordWrap(True)
        layout.addWidget(self.recommendation_label)

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

        # Piloto automático. Desactivado por defecto. Activa un modo en
        # el que las tools se auto-aprueban excepto `ejecutar_comando`.
        self.auto_approve_check = QCheckBox("Piloto automático")
        self.auto_approve_check.setObjectName("AutoApproveCheck")
        self.auto_approve_check.setToolTip(
            "Auto-aprueba las operaciones de archivos y MCP sin diálogo. "
            "El shell SIEMPRE pide confirmación aunque esté activado."
        )
        self.auto_approve_check.toggled.connect(
            self.auto_approve_changed.emit
        )
        layout.addWidget(self.auto_approve_check)

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

    def select_model(self, name: str) -> bool:
        """Selecciona un modelo por nombre sin disparar model_selected.

        Devuelve True si el modelo se encontró y seleccionó. False si
        no está en el combo (modelo desinstalado o Ollama caído).
        """
        if not name:
            return False
        idx = self.model_combo.findText(name)
        if idx < 0:
            return False
        blocked = self.model_combo.blockSignals(True)
        self.model_combo.setCurrentIndex(idx)
        self.model_combo.blockSignals(blocked)
        return True

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

    def set_recommendation(self, text: str) -> None:
        """Muestra u oculta la recomendación de uso del modelo activo."""
        self.recommendation_label.setText(text or "")
        self.recommendation_label.setVisible(bool(text))

    def set_agents(
        self,
        categories: dict[str, list[str]],
        current: str,
    ) -> None:
        """Rellena el combo agrupando por categoría.

        Si solo hay una categoría, se muestran los agentes sin
        cabecera. Si hay varias, se inserta una cabecera no
        seleccionable por cada una ("── Escritura ──").
        """
        blocked = self.agent_combo.blockSignals(True)
        self.agent_combo.clear()

        if len(categories) <= 1:
            # Una sola categoría: no mostramos cabecera.
            for names in categories.values():
                for name in names:
                    self.agent_combo.addItem(name)
        else:
            model = self.agent_combo.model()
            for category, names in categories.items():
                # Cabecera deshabilitada. El usuario ve la agrupación
                # pero no puede seleccionarla.
                header = f"── {category} ──"
                self.agent_combo.addItem(header)
                idx = self.agent_combo.count() - 1
                item = model.item(idx)
                if item is not None:
                    item.setEnabled(False)
                for name in names:
                    self.agent_combo.addItem(name)

        if current:
            idx = self.agent_combo.findText(current)
            if idx >= 0:
                self.agent_combo.setCurrentIndex(idx)

        self.agent_combo.blockSignals(blocked)

    def current_agent(self) -> str:
        return self.agent_combo.currentText().strip()

    def set_workspace_name(self, name: str) -> None:
        self.workspace_label.setText(name)

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._apply_busy()

    def set_auto_approve(self, enabled: bool) -> None:
        """Inicializa el checkbox sin disparar la señal."""
        blocked = self.auto_approve_check.blockSignals(True)
        self.auto_approve_check.setChecked(bool(enabled))
        self.auto_approve_check.blockSignals(blocked)

    def is_auto_approve(self) -> bool:
        return self.auto_approve_check.isChecked()

    # -- helpers -------------------------------------------------------------
    def _apply_busy(self) -> None:
        """Deshabilita los controles que no aplican durante streaming.

        Cambiar de agente a mitad de un turno no afecta al turno en
        curso (el worker ya tiene su snapshot), pero confunde al
        usuario: cree que su cambio surtirá efecto inmediato. Igual
        con "Nuevo agente", "Editar" y "Piloto automático".
        """
        enabled = not self._busy
        self.model_combo.setEnabled(enabled)
        self.agent_combo.setEnabled(enabled)
        self.agent_new_button.setEnabled(enabled)
        self.agent_edit_button.setEnabled(enabled)
        self.auto_approve_check.setEnabled(enabled)
