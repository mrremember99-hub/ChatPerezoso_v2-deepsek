from __future__ import annotations

import shlex

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QWidget

from core.mcp_servers import MCPServerEntry, MCPServerStore
from core.workspace import Workspace
from plugins.mcp import (
    MCPClient,
    MCPError,
    MCPServerConfig,
    MCPToolBridge,
)

from ..views.dialogs import warn
from ..workers import MCPWorker


class MCPController(QObject):
    """Gestiona el ciclo de vida de los servidores MCP.

    Los servidores se declaran en ``mcp_servers.json``. La UI muestra un
    toggle por cada uno. La señal ``servers_changed`` emite las entradas
    completas + el estado para que la sidebar los renderice.
    """

    # entries(as dicts), active_ids, pending_ids, dead_ids
    servers_changed = Signal(list, list, list, list)
    status = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        parent: QObject,
        parent_widget: QWidget | None,
        bridge: MCPToolBridge,
        workspace: Workspace,
        store: MCPServerStore | None = None,
    ):
        super().__init__(parent)
        self._parent_widget = parent_widget
        self.bridge = bridge
        self.workspace = workspace
        self.store = store or MCPServerStore()
        self.entries: list[MCPServerEntry] = self.store.load(str(workspace.root))
        self._threads: dict[str, QThread] = {}
        self._workers: dict[str, MCPWorker] = {}
        self._configs: dict[str, MCPServerConfig] = {}
        self._dead: set[str] = set()

        # Autoactivar los marcados como enabled en el JSON.
        for entry in self.entries:
            if entry.enabled:
                self._connect(
                    entry.id, entry.command,
                    args=tuple(entry.args), env=entry.env,
                )

    # -- consulta ------------------------------------------------------------
    @property
    def pending_ids(self) -> list[str]:
        return list(self._threads)

    @property
    def dead_ids(self) -> list[str]:
        return sorted(self._dead)

    def entries_by_id(self) -> dict[str, MCPServerEntry]:
        return {e.id: e for e in self.entries}

    # -- API pública ---------------------------------------------------------
    def rebind(self, bridge: MCPToolBridge, workspace: Workspace) -> None:
        self._shutdown_connections()
        self.bridge = bridge
        self.workspace = workspace
        self.entries = self.store.load(str(workspace.root))
        self._emit_changed()

    def toggle(self, server_id: str, enabled: bool) -> None:
        entry = self.entries_by_id().get(server_id)
        if entry is None:
            return
        entry.enabled = enabled
        self.store.save(self.entries)
        if enabled:
            if server_id in self.bridge.active_servers or server_id in self._threads:
                return
            if server_id in self._dead:
                self.reconnect(server_id)
                return
            self._connect(
                server_id, entry.command,
                args=tuple(entry.args), env=entry.env,
            )
        else:
            self.deactivate(server_id)

    def deactivate(self, server_id: str) -> None:
        if server_id in self._threads:
            return
        self._dead.discard(server_id)
        self._configs.pop(server_id, None)
        self.bridge.deactivate(server_id)
        entry = self.entries_by_id().get(server_id)
        if entry is not None:
            entry.enabled = False
            self.store.save(self.entries)
        self.status.emit(f"MCP «{server_id}» desactivado")
        self._emit_changed()

    def report_failure(self, server_id: str) -> None:
        if server_id in self._dead:
            return
        if server_id not in self._configs:
            return
        if server_id in self._threads:
            return
        self._dead.add(server_id)
        self.bridge.deactivate(server_id)
        self.status.emit(f"MCP «{server_id}» sin respuesta")
        self._emit_changed()

    def reconnect(self, server_id: str) -> None:
        config = self._configs.get(server_id)
        if config is None or server_id in self._threads:
            return
        self._dead.discard(server_id)
        self.status.emit(f"Reconectando MCP «{server_id}»…")
        self._connect(server_id, config.command, args=config.args)

    def emit_current_state(self) -> None:
        """Fuerza el envío de servers_changed con el estado actual.

        Se llama desde AppController después del wiring, para que la
        sidebar reciba las entries aunque MCPController las emitiera
        antes de que la señal estuviera conectada.
        """
        self._emit_changed()

    def shutdown(self) -> None:
        self._shutdown_connections()

    # -- implementación ------------------------------------------------------
    def _shutdown_connections(self) -> None:
        self.bridge.deactivate()
        for worker in list(self._workers.values()):
            worker.client.close()
        for thread in list(self._threads.values()):
            if thread.isRunning():
                thread.quit()
                thread.wait(2000)
        self._workers.clear()
        self._threads.clear()
        self._configs.clear()
        self._dead.clear()

    def _connect(self, server_id, command, *, args=None, env=None):
        try:
            parts = shlex.split(command) if args is None else [command, *args]
            if not parts:
                raise ValueError("El comando MCP está vacío.")
            config = MCPServerConfig(
                command=parts[0],
                args=tuple(parts[1:]),
                env=dict(env or {}),
                cwd=str(self.workspace.root),
            )
            client = MCPClient(config)
        except (MCPError, ValueError) as exc:
            warn(self._parent_widget, "MCP", str(exc))
            self._emit_changed()
            return

        self._configs[server_id] = config
        self._dead.discard(server_id)
        self.status.emit(f"Consultando herramientas MCP de «{server_id}»…")

        thread = QThread(self)
        worker = MCPWorker(server_id, client)
        self._threads[server_id] = thread
        self._workers[server_id] = worker
        worker.moveToThread(thread)
        thread.started.connect(worker.run)
        worker.finished.connect(self._on_loaded)
        worker.error.connect(self._on_error)
        worker.finished.connect(thread.quit)
        worker.error.connect(thread.quit)
        thread.finished.connect(self._on_thread_finished)
        thread.start()
        self._emit_changed()

    def _on_loaded(self, server_id, client, tools):
        try:
            self.bridge.activate(server_id, client, tools)
            count = sum(
                1 for item in self.bridge.definitions()
                if item.get("function", {}).get("name", "")
                .startswith(f"mcp__{server_id}__")
            )
            self.status.emit(f"MCP «{server_id}» activo · {count} herramienta(s)")
        except Exception as exc:
            self.bridge.deactivate(server_id)
            self._dead.add(server_id)
            self.status.emit(f"MCP «{server_id}» no disponible")
            self.error.emit(f"«{server_id}»: {exc}")
        self._emit_changed()

    def _on_error(self, server_id, message):
        self.bridge.deactivate(server_id)
        self._dead.add(server_id)
        self.status.emit(f"MCP «{server_id}» no disponible")
        self.error.emit(f"«{server_id}»: {message}")
        self._emit_changed()

    def _on_thread_finished(self):
        finished_ids = [
            sid for sid, thread in self._threads.items() if not thread.isRunning()
        ]
        for server_id in finished_ids:
            worker = self._workers.pop(server_id, None)
            thread = self._threads.pop(server_id, None)
            if worker is not None:
                worker.deleteLater()
            if thread is not None:
                thread.deleteLater()
        self._emit_changed()

    def _emit_changed(self):
        self.servers_changed.emit(
            [e.to_dict() for e in self.entries],
            list(self.bridge.active_servers),
            list(self._threads),
            sorted(self._dead),
        )