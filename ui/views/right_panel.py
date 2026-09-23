"""Panel derecho: plugins, cola de prompts, explorador de archivos.

Estructura paralela a la sidebar izquierda: ancho fijo, secciones
con titulo, widgets autocontenidos. Secciones actuales:
  · MCP: toggle por servidor.
  · COLA DE PROMPTS: todo list del envio en lote.
  · ARCHIVOS: arbol del workspace (QFileSystemModel + filtro).
"""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import (
    QDir,
    QModelIndex,
    QSortFilterProxyModel,
    Qt,
    Signal,
)
from PySide6.QtWidgets import (
    QFileSystemModel,
    QLabel,
    QPushButton,
    QTreeView,
    QVBoxLayout,
    QWidget,
)

from core.workspace import _SKIP_DIRS

from .. import design


class _WorkspaceFilterProxy(QSortFilterProxyModel):
    """Filtra directorios de sistema del arbol del workspace.

    Sin esto, el arbol mostraria node_modules/, __pycache__/,
    venv/, dist/, etc. — el mismo ruido que el listado de
    herramientas excluye via _SKIP_DIRS en core/workspace.py.
    Los archivos nunca se filtran; solo los directorios.
    """

    def __init__(
        self, skip_names: frozenset[str], parent=None
    ) -> None:
        super().__init__(parent)
        self._skip = skip_names

    def filterAcceptsRow(
        self, source_row: int, source_parent: QModelIndex
    ) -> bool:
        model = self.sourceModel()
        if model is None:
            return True
        index = model.index(source_row, 0, source_parent)
        if not index.isValid():
            return False
        # Los archivos siempre pasan.
        if not model.isDir(index):
            return True
        # Los directorios de sistema, fuera.
        name = model.fileName(index)
        return name not in self._skip


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
        # Modelo del sistema de archivos del workspace. Se crea
        # aqui para que sobreviva mientras el panel viva (los
        # modelos sin padre pueden ser recogidos por el GC).
        self._workspace_model = QFileSystemModel(self)
        self._workspace_model.setFilter(
            QDir.Filter.AllDirs
            | QDir.Filter.Files
            | QDir.Filter.NoDotAndDotDot
        )
        self._workspace_proxy = _WorkspaceFilterProxy(
            _SKIP_DIRS, self
        )
        self._workspace_proxy.setSourceModel(self._workspace_model)
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

        # -- ARCHIVOS --
        layout.addSpacing(14)
        self.workspace_title = QLabel("ARCHIVOS")
        self.workspace_title.setObjectName("SectionTitle")
        layout.addWidget(self.workspace_title)

        self.workspace_tree = QTreeView()
        self.workspace_tree.setObjectName("WorkspaceTree")
        self.workspace_tree.setHeaderHidden(True)
        self.workspace_tree.setUniformRowHeights(True)
        self.workspace_tree.setIndentation(12)
        self.workspace_tree.setMinimumHeight(150)
        self.workspace_tree.setEditTriggers(
            QTreeView.EditTrigger.NoEditTriggers
        )
        self.workspace_tree.setModel(self._workspace_proxy)
        # Los 3 ultimos (tamano, tipo, fecha) no caben en 260 px.
        for col in (1, 2, 3):
            self.workspace_tree.setColumnHidden(col, True)
        layout.addWidget(self.workspace_tree, 1)

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

    def set_workspace(self, path: str | Path | None) -> None:
        """Fija la raiz del arbol de archivos al workspace activo.

        El QFileSystemModel usa su propio QFileSystemWatcher: el
        arbol se refresca solo cuando el workspace cambia
        (escritura del modelo, edicion externa, etc.). No hay
        que hacer polling.
        """
        if not path:
            self.workspace_tree.setRootIndex(QModelIndex())
            return
        root_str = str(path)
        self._workspace_model.setRootPath(root_str)
        source_root = self._workspace_model.index(root_str)
        if source_root.isValid():
            proxy_root = self._workspace_proxy.mapFromSource(
                source_root
            )
            if proxy_root.isValid():
                self.workspace_tree.setRootIndex(proxy_root)
                return
        # Si algo falla, dejar el arbol vacio en vez de mostrar
        # el sistema de archivos completo.
        self.workspace_tree.setRootIndex(QModelIndex())

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
