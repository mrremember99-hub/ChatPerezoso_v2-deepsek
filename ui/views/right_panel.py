"""Panel derecho: plugins (MCP y futuros).

Estructura paralela a la sidebar izquierda: ancho fijo, secciones
con título, widgets autocontenidos. Hoy solo tiene la lista de
servidores MCP. Cuando haya más plugins con UI, cada uno añade su
sección aquí.
"""
from __future__ import annotations

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QLabel,
    QPushButton,
    QVBoxLayout,
    QWidget,
)

from .. import design


class QueueRow(QLabel):
    """Fila del todo list: símbolo + N/M. Cambia de color por estado."""

    _SYMBOLS = {
        "pending": "▢",
        "running": "◐",
        "done": "✓",
        "error": "✗",
        "cancelled": "⊘",
    }

    def __init__(self, index: int, total: int) -> None:
        super().__init__()
        self.setObjectName("QueueRow")
        self._index = index
        self._total = total
        self._status = "pending"
        self._render()

    def set_status(self, status: str) -> None:
        if status not in self._SYMBOLS:
            return
        self._status = status
        self._render()
        # Forzar repintado del QSS con property selector.
        self.style().unpolish(self)
        self.style().polish(self)

    def _render(self) -> None:
        symbol = self._SYMBOLS[self._status]
        self.setText(f"  {symbol}   {self._index}/{self._total}")
        self.setProperty("status", self._status)


class RightPanel(QWidget):
    mcp_toggle_requested = Signal(str, bool)

    def __init__(self) -> None:
        super().__init__()
        self.setObjectName("RightPanel")
        self.setFixedWidth(design.SIDEBAR_WIDTH_PX)
        self._busy = False
        self._mcp_buttons: dict[str, QPushButton] = {}
        self._mcp_states: dict[str, str] = {}
        self._mcp_labels: dict[str, str] = {}
        # Filas del todo list de la cola de prompts.
        self._queue_rows: list[QueueRow] = []
        self._build()

    # -- construcción --------------------------------------------------------
    def _build(self) -> None:
        layout = QVBoxLayout(self)
        layout.setContentsMargins(16, 16, 16, 16)
        layout.setSpacing(8)

        title = QLabel("PLUGINS")
        title.setObjectName("AppTitle")
        layout.addWidget(title)

        # -- MCP --
        mcp_title = QLabel("MCP")
        mcp_title.setObjectName("SectionTitle")
        layout.addWidget(mcp_title)

        self.mcp_list = QVBoxLayout()
        self.mcp_list.setSpacing(6)
        layout.addLayout(self.mcp_list)

        # -- COLA DE PROMPTS --
        layout.addSpacing(14)
        self.queue_title = QLabel("COLA DE PROMPTS")
        self.queue_title.setObjectName("SectionTitle")
        self.queue_title.setVisible(False)
        layout.addWidget(self.queue_title)

        self.queue_list = QVBoxLayout()
        self.queue_list.setSpacing(2)
        layout.addLayout(self.queue_list)

        # -- extensiones futuras --
        # Aquí irán las secciones de otros plugins (git, search, etc.)
        # cuando tengan UI propia. Por ahora queda vacío para no meter
        # ruido visual.
        layout.addStretch(1)

    # -- API pública ---------------------------------------------------------
    def set_mcp_servers(
        self,
        entries: list[dict],
        active: list[str],
        pending: list[str],
        dead: list[str],
    ) -> None:
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

        for sid in list(self._mcp_buttons):
            if sid not in seen_ids:
                button = self._mcp_buttons.pop(sid)
                self.mcp_list.removeWidget(button)
                button.deleteLater()
        self._render_mcp_buttons()

    def set_busy(self, busy: bool) -> None:
        self._busy = busy
        self._render_mcp_buttons()

    # -- cola de prompts -----------------------------------------------------
    def set_queue_list(self, prompts: list[str]) -> None:
        """Reemplaza las filas del todo list con la nueva cola.

        Si `prompts` está vacío, oculta la sección.
        """
        # Limpiar filas anteriores.
        for row in self._queue_rows:
            self.queue_list.removeWidget(row)
            row.deleteLater()
        self._queue_rows.clear()

        if not prompts:
            self.queue_title.setVisible(False)
            return

        self.queue_title.setVisible(True)
        total = len(prompts)
        for i in range(total):
            row = QueueRow(i + 1, total)
            self.queue_list.addWidget(row)
            self._queue_rows.append(row)

    def update_queue_item(self, index: int, status: str) -> None:
        """Actualiza la fila `index` (1-based) al nuevo estado."""
        if 1 <= index <= len(self._queue_rows):
            self._queue_rows[index - 1].set_status(status)

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
