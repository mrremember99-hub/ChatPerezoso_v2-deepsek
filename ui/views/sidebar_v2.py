"""Sidebar v2: 5 cards apiladas (mascara visual).

Hereda de Sidebar y SOLO sobrescribe el layout. Los atributos
publicos (agent_combo, model_combo, auto_approve_check, diagnostics,
etc.) y los metodos set_*/is_* se conservan tal cual: AppController
no necesita saber cual version esta activa.
"""
from __future__ import annotations

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox,
    QFrame,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .diagnostics_panel import DiagnosticsPanel
from .sidebar import Sidebar


def _card(title: str) -> tuple[QFrame, QVBoxLayout]:
    """Crea una card con titulo. Devuelve (frame, layout_interno)."""
    frame = QFrame()
    frame.setObjectName("Card")
    lay = QVBoxLayout(frame)
    lay.setContentsMargins(10, 6, 10, 10)
    lay.setSpacing(6)
    label = QLabel(title)
    label.setObjectName("CardTitle")
    lay.addWidget(label)
    return frame, lay


class SidebarV2(Sidebar):
    """Sidebar con diseno de cards.

    Reusa el contrato publico de Sidebar. Solo el `_build` cambia.
    """

    def _build(self) -> None:
        root = QVBoxLayout(self)
        root.setContentsMargins(12, 12, 12, 12)
        root.setSpacing(10)

        # -- Card AGENTE ------------------------------------------------
        card, lay = _card("agente")
        self.agent_combo = QComboBox()
        self.agent_combo.currentTextChanged.connect(self.agent_changed)
        lay.addWidget(self.agent_combo)

        row = QHBoxLayout()
        row.setSpacing(6)
        self.agent_new_button = QPushButton("crear")
        self.agent_new_button.setObjectName("SecondaryButton")
        self.agent_new_button.clicked.connect(
            lambda: self.agent_create_requested.emit()
        )
        row.addWidget(self.agent_new_button)

        self.agent_edit_button = QPushButton("modificar")
        self.agent_edit_button.setObjectName("SecondaryButton")
        self.agent_edit_button.clicked.connect(
            lambda: self.agent_edit_requested.emit()
        )
        row.addWidget(self.agent_edit_button)
        lay.addLayout(row)
        root.addWidget(card)

        # -- Card MODELO ------------------------------------------------
        card, lay = _card("modelo")
        self.model_combo = QComboBox()
        self.model_combo.currentTextChanged.connect(self.model_selected)
        lay.addWidget(self.model_combo)

        refresh = QPushButton("actualizar")
        refresh.setObjectName("SecondaryButton")
        refresh.clicked.connect(lambda: self.model_refresh_requested.emit())
        lay.addWidget(refresh, alignment=Qt.AlignmentFlag.AlignRight)

        # Caja marron con info del modelo (capabilities + recommendation)
        info_box = QWidget()
        info_box.setObjectName("BoxContent")
        info_lay = QVBoxLayout(info_box)
        info_lay.setContentsMargins(8, 6, 8, 6)
        info_lay.setSpacing(2)
        self.capabilities_label = QLabel("")
        self.capabilities_label.setObjectName("CapabilitiesBadge")
        self.capabilities_label.setProperty("mode", "unknown")
        info_lay.addWidget(self.capabilities_label)
        self.recommendation_label = QLabel("")
        self.recommendation_label.setObjectName("RecommendationLabel")
        self.recommendation_label.setWordWrap(True)
        info_lay.addWidget(self.recommendation_label)
        info_box.setMinimumHeight(80)
        lay.addWidget(info_box)
        root.addWidget(card)

        # -- Card PILOTO AUTOMATICO ------------------------------------
        card, lay = _card("piloto automático")
        from PySide6.QtWidgets import QCheckBox
        self.auto_approve_check = QCheckBox("leer/escribir")
        self.auto_approve_check.setObjectName("AutoApproveCheck")
        self.auto_approve_check.setToolTip(
            "Auto-aprueba las operaciones de archivos y MCP sin dialogo."
        )
        self.auto_approve_check.toggled.connect(
            self._on_auto_approve_toggled
        )
        lay.addWidget(self.auto_approve_check)

        self.auto_approve_shell_check = QCheckBox("scripts")
        self.auto_approve_shell_check.setObjectName("AutoApproveShellCheck")
        self.auto_approve_shell_check.setToolTip(
            "Auto-aprueba tambien ejecutar_comando (el modelo podra "
            "lanzar comandos sin dialogo). Opt-in."
        )
        self.auto_approve_shell_check.setEnabled(False)
        self.auto_approve_shell_check.toggled.connect(
            self.auto_approve_shell_changed.emit
        )
        lay.addWidget(self.auto_approve_shell_check)

        self.verificador_check = QCheckBox("sintaxis")
        self.verificador_check.setObjectName("VerificadorCheck")
        self.verificador_check.setToolTip(
            "Tras crear o escribir un archivo, verifica su sintaxis."
        )
        self.verificador_check.toggled.connect(
            self.verificador_changed.emit
        )
        lay.addWidget(self.verificador_check)
        root.addWidget(card)

        # -- Card CARPETA DE TRABAJO -----------------------------------
        card, lay = _card("carpeta de trabajo")
        self.workspace_label = QLabel("—")
        self.workspace_label.setObjectName("FolderLabel")
        self.workspace_label.setWordWrap(True)
        self.workspace_label.setAttribute(
            Qt.WidgetAttribute.WA_TranslucentBackground, True
        )
        lay.addWidget(self.workspace_label)

        choose = QPushButton("cambiar")
        choose.setObjectName("SecondaryButton")
        choose.clicked.connect(
            lambda: self.workspace_change_requested.emit()
        )
        lay.addWidget(choose, alignment=Qt.AlignmentFlag.AlignHCenter)

        # Caja marron con listado de archivos del workspace.
        files_box = QLabel("")
        files_box.setObjectName("BoxContent")
        files_box.setWordWrap(True)
        files_box.setMinimumHeight(120)
        files_box.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
        files_box.setContentsMargins(8, 6, 8, 6)
        files_box.setText("(vacio)")
        self._files_box = files_box
        lay.addWidget(files_box)
        root.addWidget(card)

        # -- Card SESION -----------------------------------------------
        card, lay = _card("sesión")
        self.context_usage_label = QLabel("")
        self.context_usage_label.setObjectName("ContextUsageBadge")
        self.context_usage_label.setVisible(False)
        lay.addWidget(self.context_usage_label)
        self.diagnostics = DiagnosticsPanel()
        lay.addWidget(self.diagnostics)
        root.addWidget(card)

        root.addStretch(1)

        # -- Boton nuevo chat (fuera de cards, abajo) ------------------
        clear = QPushButton("nuevo chat")
        clear.setObjectName("PrimaryButton")
        clear.clicked.connect(lambda: self.clear_chat_requested.emit())
        root.addWidget(clear, alignment=Qt.AlignmentFlag.AlignHCenter)
