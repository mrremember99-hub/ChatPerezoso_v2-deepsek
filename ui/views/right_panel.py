"""Panel derecho: plugins, cola de prompts, explorador de archivos.

Estructura paralela a la sidebar izquierda: ancho fijo, secciones
con titulo, widgets autocontenidos. Secciones actuales:
  · MCP: toggle por servidor.
  · COLA DE PROMPTS: todo list del envio en lote.
  · ARCHIVOS: arbol del workspace (QFileSystemModel + filtro).
"""
from __future__ import annotations

from pathlib import Path
from typing import ClassVar

from PySide6.QtCore import (
    QDir,
    QModelIndex,
    QPersistentModelIndex,
    QSortFilterProxyModel,
    Qt,
    QUrl,
    Signal,
)
from PySide6.QtGui import QDesktopServices, QGuiApplication
from PySide6.QtWidgets import (
    QFileSystemModel,
    QHBoxLayout,
    QLabel,
    QMenu,
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
        self,
        source_row: int,
        source_parent: QModelIndex | QPersistentModelIndex,
    ) -> bool:
        model = self.sourceModel()
        # El contrato de QSortFilterProxyModel.sourceModel() devuelve
        # QAbstractItemModel, pero aqui siempre envolvemos un
        # QFileSystemModel. isinstance es la forma segura de acceder
        # a isDir() / fileName() sin que Pylance se queje.
        if not isinstance(model, QFileSystemModel):
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
    """Fila del todo list: símbolo + N/M. Cambia de color por estado.

    Feature editar cola (2026-09-28): clic derecho abre menú con
    Editar / Eliminar / Subir / Bajar. Solo aplicable a filas
    pendientes (ni enviadas ni corriendo). El panel llama a
    set_editable(False) en las filas ya procesadas.
    """

    _SYMBOLS: ClassVar[dict[str, str]] = {
        "pending": "▢",
        "running": "◐",
        "done": "✓",
        "error": "✗",
        "cancelled": "⊘",
    }

    # idx 1-based (mismo formato que queue_item_status_changed).
    edit_requested = Signal(int, str)    # idx, nuevo_texto
    remove_requested = Signal(int)       # idx
    move_requested = Signal(int, int)    # idx, delta (-1 subir / +1 bajar)

    def __init__(self, index: int, total: int, text: str = "") -> None:
        super().__init__()
        self.setObjectName("QueueRow")
        self._index = index
        self._total = total
        self._text = text
        self._status = "pending"
        self._editable = True
        self.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.customContextMenuRequested.connect(self._on_context_menu)
        self._render()

    def set_status(self, status: str) -> None:
        if status not in self._SYMBOLS:
            return
        self._status = status
        self._render()
        # Forzar repintado del QSS con property selector.
        self.style().unpolish(self)
        self.style().polish(self)

    def set_editable(self, editable: bool) -> None:
        """Marca la fila como editable o no (ya enviada/corriendo)."""
        self._editable = bool(editable)

    def set_index(self, index: int, total: int | None = None) -> None:
        """Renumera la fila tras un move/remove."""
        self._index = index
        if total is not None:
            self._total = total
        self._render()

    def set_text(self, text: str) -> None:
        """Cambia el texto del prompt (tras Editar)."""
        self._text = text

    def _render(self) -> None:
        symbol = self._SYMBOLS[self._status]
        self.setText(f"  {symbol}   {self._index}/{self._total}")
        self.setProperty("status", self._status)

    def _on_context_menu(self, pos) -> None:
        menu = QMenu(self)
        act_edit = menu.addAction("Editar prompt")
        act_remove = menu.addAction("Eliminar de la cola")
        menu.addSeparator()
        act_up = menu.addAction("Subir")
        act_down = menu.addAction("Bajar")

        # Subir/Bajar segun posicion dentro del bloque pendiente.
        act_up.setEnabled(self._editable and self._index > 1)
        act_down.setEnabled(self._editable)

        if not self._editable:
            act_edit.setEnabled(False)
            act_remove.setEnabled(False)
            act_up.setEnabled(False)
            act_down.setEnabled(False)

        act_edit.triggered.connect(self._on_edit)
        act_remove.triggered.connect(self._on_remove)
        act_up.triggered.connect(lambda: self.move_requested.emit(self._index, -1))
        act_down.triggered.connect(lambda: self.move_requested.emit(self._index, +1))

        menu.exec(self.mapToGlobal(pos))

    def _on_edit(self) -> None:
        from PySide6.QtWidgets import QInputDialog
        new_text, ok = QInputDialog.getText(
            self, "Editar prompt", "Texto nuevo:", text=self._text
        )
        if not ok:
            return
        new_text = new_text.strip()
        if not new_text or new_text == self._text:
            return
        self.edit_requested.emit(self._index, new_text)

    def _on_remove(self) -> None:
        self.remove_requested.emit(self._index)


class RightPanel(QWidget):

    queue_retry_requested = Signal()
    queue_skip_requested = Signal()
    queue_cancel_requested = Signal()
    # Feature editar cola (2026-09-28): idx 1-based, delta -1/+1.
    queue_edit_requested = Signal(int, str)
    queue_remove_requested = Signal(int)
    queue_move_requested = Signal(int, int)
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
        self._build_mcp_section(layout)

        # -- COLA DE PROMPTS --
        layout.addSpacing(14)
        self._build_queue_section(layout)

        # -- ARCHIVOS --
        layout.addSpacing(14)
        self._build_files_section(layout)

    # -- secciones (extraidas para permitir subclases v2 con cards) ---------

    def _build_mcp_section(self, parent_layout: QVBoxLayout) -> None:
        mcp_title = QLabel("MCP")
        mcp_title.setObjectName("SectionTitle")
        parent_layout.addWidget(mcp_title)

        self.mcp_list = QVBoxLayout()
        self.mcp_list.setSpacing(6)
        parent_layout.addLayout(self.mcp_list)

    def _build_queue_section(self, parent_layout: QVBoxLayout) -> None:
        self.queue_title = QLabel("COLA DE PROMPTS")
        self.queue_title.setObjectName("SectionTitle")
        self.queue_title.setVisible(False)
        parent_layout.addWidget(self.queue_title)

        self.queue_list = QVBoxLayout()
        self.queue_list.setSpacing(2)
        parent_layout.addLayout(self.queue_list)

        # Barra de pausa: aparece cuando un prompt falla y la
        # cola queda detenida esperando decisión del usuario.
        self.queue_paused_bar = QWidget()
        self.queue_paused_bar.setObjectName("QueuePausedBar")
        paused_lay = QHBoxLayout(self.queue_paused_bar)
        paused_lay.setContentsMargins(0, 6, 0, 0)
        paused_lay.setSpacing(6)
        _btn_style = "padding: 1px 4px; font-size: 9px; min-height: 0;"
        self.queue_retry_btn = QPushButton("↻")
        self.queue_retry_btn.setObjectName("QueuePausedBtn")
        self.queue_retry_btn.setToolTip("Reintentar el prompt que falló")
        self.queue_retry_btn.setStyleSheet(_btn_style)
        self.queue_retry_btn.setFixedHeight(20)
        self.queue_retry_btn.clicked.connect(self._on_retry_clicked)
        paused_lay.addWidget(self.queue_retry_btn)
        self.queue_skip_btn = QPushButton("→")
        self.queue_skip_btn.setObjectName("QueuePausedBtn")
        self.queue_skip_btn.setToolTip("Saltar al siguiente prompt")
        self.queue_skip_btn.setStyleSheet(_btn_style)
        self.queue_skip_btn.setFixedHeight(20)
        self.queue_skip_btn.clicked.connect(self._on_skip_clicked)
        paused_lay.addWidget(self.queue_skip_btn)
        self.queue_cancel_btn = QPushButton("✕")
        self.queue_cancel_btn.setObjectName("QueuePausedBtn")
        self.queue_cancel_btn.setToolTip("Cancelar toda la cola")
        self.queue_cancel_btn.setStyleSheet(_btn_style)
        self.queue_cancel_btn.setFixedHeight(20)
        self.queue_cancel_btn.clicked.connect(self._on_cancel_clicked)
        paused_lay.addWidget(self.queue_cancel_btn)
        paused_lay.addStretch(1)
        self.queue_paused_bar.setVisible(False)
        parent_layout.addWidget(self.queue_paused_bar)

    def _build_files_section(self, parent_layout: QVBoxLayout) -> None:
        self.workspace_title = QLabel("ARCHIVOS")
        self.workspace_title.setObjectName("SectionTitle")
        parent_layout.addWidget(self.workspace_title)

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
        self.workspace_tree.doubleClicked.connect(
            self._on_tree_double_click
        )
        self.workspace_tree.setContextMenuPolicy(
            Qt.ContextMenuPolicy.CustomContextMenu
        )
        self.workspace_tree.customContextMenuRequested.connect(
            self._on_tree_context_menu
        )
        parent_layout.addWidget(self.workspace_tree, 1)

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

    # El usuario pidio borrar un archivo desde el arbol. El
    # controller decide que hacer (pasa por el gate de
    # confirmacion de borrar_archivo).
    delete_requested = Signal(str)

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

    # -- interaccion con el arbol -------------------------------------------

    def _path_for_index(self, proxy_index: QModelIndex) -> Path | None:
        """Convierte un indice del proxy a la ruta real del FS."""
        if not proxy_index.isValid():
            return None
        source_index = self._workspace_proxy.mapToSource(proxy_index)
        path_str = self._workspace_model.filePath(source_index)
        if not path_str:
            return None
        return Path(path_str)

    def _on_tree_double_click(self, proxy_index: QModelIndex) -> None:
        """Abrir el archivo con la app externa del sistema.

        Los directorios ya se expanden/colapsan con el doble clic
        por defecto de Qt, asi que solo interceptamos archivos.
        """
        path = self._path_for_index(proxy_index)
        if path is None or not path.is_file():
            return
        QDesktopServices.openUrl(QUrl.fromLocalFile(str(path)))

    def _on_tree_context_menu(self, pos) -> None:
        index = self.workspace_tree.indexAt(pos)
        if not index.isValid():
            return
        path = self._path_for_index(index)
        if path is None:
            return
        menu = self._build_file_menu(path)
        menu.exec(self.workspace_tree.viewport().mapToGlobal(pos))

    def _build_file_menu(self, path: Path) -> QMenu:
        """Construye el menu contextual para una ruta.

        Extraido de _on_tree_context_menu para poder testearlo sin
        simular clicks (QTest con viewport pos es fragil).
        """
        menu = QMenu(self.workspace_tree)
        if path.is_file():
            act_open = menu.addAction("Abrir con app externa")
            act_open.triggered.connect(
                lambda _=False, p=path: QDesktopServices.openUrl(
                    QUrl.fromLocalFile(str(p))
                )
            )
        act_reveal = menu.addAction("Mostrar en el Finder")
        act_reveal.triggered.connect(
            lambda _=False, p=path: self._reveal_in_finder(p)
        )
        act_copy = menu.addAction("Copiar ruta")
        act_copy.triggered.connect(
            lambda _=False, p=path: self._copy_path(p)
        )
        # UI backlog: borrar archivos desde el arbol. La senal la
        # consume el controller, que pasa por el gate de
        # confirmacion de borrar_archivo (nunca auto-aprobado).
        act_delete = menu.addAction("Borrar")
        act_delete.triggered.connect(
            lambda _=False, p=path: self.delete_requested.emit(str(p))
        )
        return menu

    @staticmethod
    def _reveal_in_finder(path: Path) -> None:
        """Abre la carpeta contenedora y selecciona el archivo.

        Usa el esquema file:// con un fragmento, que en macOS
        abre Finder con el elemento seleccionado. En otros SO
        abre la carpeta contenedora.
        """
        if path.is_file():
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(path.parent))
            )
        elif path.is_dir():
            QDesktopServices.openUrl(
                QUrl.fromLocalFile(str(path))
            )

    @staticmethod
    def _copy_path(path: Path) -> None:
        clipboard = QGuiApplication.clipboard()
        if clipboard is not None:
            clipboard.setText(str(path))

    # -- cola de prompts -----------------------------------------------------
    def show_queue_paused(self) -> None:
        self.queue_paused_bar.setVisible(True)

    def _on_retry_clicked(self) -> None:
        self.queue_paused_bar.setVisible(False)
        self.queue_retry_requested.emit()

    def _on_skip_clicked(self) -> None:
        self.queue_paused_bar.setVisible(False)
        self.queue_skip_requested.emit()

    def _on_cancel_clicked(self) -> None:
        self.queue_paused_bar.setVisible(False)
        self.queue_cancel_requested.emit()

    def set_queue_list(self, prompts: list[str]) -> None:
        """Reemplaza las filas del todo list con la nueva cola.

        Si `prompts` está vacío, oculta la sección.
        """
        # Nueva cola: ocultar barra de pausa si estaba visible.
        self.queue_paused_bar.setVisible(False)
        # Limpiar filas anteriores.
        for row in self._queue_rows:
            self.queue_list.removeWidget(row)
            # Desvincular del padre INMEDIATAMENTE. Sin esto el
            # widget sigue visible hasta que el event loop procese
            # deleteLater(), y al reemplazar la cola se ven las
            # filas viejas superpuestas con las nuevas.
            row.setParent(None)
            row.deleteLater()
        self._queue_rows.clear()

        if not prompts:
            self.queue_title.setVisible(False)
            return

        self.queue_title.setVisible(True)
        total = len(prompts)
        for i, prompt in enumerate(prompts):
            row = QueueRow(i + 1, total, text=prompt)
            row.edit_requested.connect(self._forward_edit)
            row.remove_requested.connect(self._forward_remove)
            row.move_requested.connect(self._forward_move)
            self.queue_list.addWidget(row)
            self._queue_rows.append(row)

    def update_queue_item(self, index: int, status: str) -> None:
        """Actualiza la fila `index` (1-based) al nuevo estado.

        Feature editar cola (2026-09-28): cuando llega "running",
        se recalcula la editabilidad: filas anteriores y la actual
        no son editables (ya enviadas); las posteriores si.
        """
        if 1 <= index <= len(self._queue_rows):
            self._queue_rows[index - 1].set_status(status)
        if status == "running":
            for i, row in enumerate(self._queue_rows):
                row.set_editable((i + 1) > index)

    # -- editar cola (2026-09-28) -------------------------------------------

    def _forward_edit(self, idx: int, new_text: str) -> None:
        self.queue_edit_requested.emit(idx, new_text)

    def _forward_remove(self, idx: int) -> None:
        self.queue_remove_requested.emit(idx)

    def _forward_move(self, idx: int, delta: int) -> None:
        self.queue_move_requested.emit(idx, delta)

    def queue_row_edit(self, idx: int, new_text: str) -> None:
        """Actualiza el texto de la fila `idx` (1-based)."""
        if 1 <= idx <= len(self._queue_rows):
            self._queue_rows[idx - 1].set_text(new_text)

    def queue_row_remove(self, idx: int) -> None:
        """Elimina la fila `idx` (1-based) y renumera las posteriores.

        NO borra la fila de la cola del controller; ese es trabajo del
        ChatController. Aqui solo se actualiza la vista.
        """
        if not (1 <= idx <= len(self._queue_rows)):
            return
        row = self._queue_rows.pop(idx - 1)
        self.queue_list.removeWidget(row)
        row.setParent(None)
        row.deleteLater()
        total = len(self._queue_rows)
        for i, r in enumerate(self._queue_rows):
            r.set_index(i + 1, total)

    def queue_row_move(self, idx: int, delta: int) -> None:
        """Mueve la fila `idx` (1-based) delta posiciones (-1 subir, +1 bajar).

        Solo dentro del bloque pendiente: las filas ya enviadas
        (idx por debajo del current) tienen set_editable(False) y no
        emiten move_requested, pero se valida igual aqui.
        """
        if delta not in (-1, +1):
            return
        target = idx + delta
        if not (1 <= idx <= len(self._queue_rows)):
            return
        if not (1 <= target <= len(self._queue_rows)):
            return
        rows = self._queue_rows
        rows[idx - 1], rows[target - 1] = rows[target - 1], rows[idx - 1]
        # Reordenar en el layout.
        for r in rows:
            self.queue_list.removeWidget(r)
        for r in rows:
            self.queue_list.addWidget(r)
        for i, r in enumerate(rows):
            r.set_index(i + 1, len(rows))

    # -- helpers -------------------------------------------------------------
    def _ensure_mcp_button(self, server_id: str) -> None:
        if server_id in self._mcp_buttons:
            return
        button = QPushButton("")
        button.setObjectName("McpToggle")
        button.setCheckable(True)
        button.setMinimumHeight(34)
        # Aviso: con modelos pequenos (<8B) activar MCP suele
        # empeorar el tool calling. Los nombres largos con prefijo
        # (mcp__fs__...) y la semantica distinta del
        # server-filesystem confunden al modelo. Analisis completo
        # en docs/mcp-notas.md.
        button.setToolTip(
            "Con modelos pequeños (<8B), activar MCP puede\n"
            "confundir al modelo y empeorar el tool calling.\n"
            "Ver docs/mcp-notas.md para detalles."
        )
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
