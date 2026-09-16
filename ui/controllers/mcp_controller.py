from __future__ import annotations

import shlex

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QWidget

from core.workspace import Workspace
from plugins.mcp import MCPClient, MCPError, MCPServerConfig, MCPToolBridge
from ..views.dialogs import ask_mcp_server, warn
from ..workers import MCPWorker


class MCPController(QObject):
    servers_changed = Signal(list, list)
    status = Signal(str)
    error = Signal(str)

    def __init__(
        self,
        parent: QObject,
        parent_widget: QWidget,
        bridge: MCPToolBridge,
        workspace: Workspace,
    ):
        super().__init__(parent)
        self._parent_widget = parent_widget
        self.bridge = bridge
        self.workspace = workspace
        self._threads: dict[str, QThread] = {}
        self._workers: dict[str, MCPWorker] = {}

    @property
    def pending_ids(self) -> list[str]:
        return list(self._threads)

    def rebind(self, bridge: MCPToolBridge, workspace: Workspace) -> None:
        self._shutdown_connections()
        self.bridge = bridge
        self.workspace = workspace
        self._emit_changed()

    def prompt_add(self) -> None:
        result = ask_mcp_server(self._parent_widget, str(self.workspace.root))
        if result is None:
            return
        server_id, command = result
        if server_id in self.bridge.active_servers or server_id in self._threads:
            warn(
                self._parent_widget, "MCP",
                f"Ya existe un servidor llamado «{server_id}».",
            )
            return
        self._connect(server_id, command)

    def deactivate(self, server_id: str) -> None:
        if server_id in self._threads:
            return
        self.bridge.deactivate(server_id)
        self.status.emit(f"MCP «{server_id}» desactivado")
        self._emit_changed()

    def shutdown(self) -> None:
        self._shutdown_connections()

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

    def _connect(self, server_id: str, command: str) -> None:
        try:
            parts = shlex.split(command)
            if not parts:
                raise ValueError("El comando MCP está vacío.")
            client = MCPClient(
                MCPServerConfig(
                    command=parts[0],
                    args=tuple(parts[1:]),
                    cwd=str(self.workspace.root),
                )
            )
        except (MCPError, ValueError) as exc:
            warn(self._parent_widget, "MCP", str(exc))
            return

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

    def _on_loaded(self, server_id: str, client: MCPClient, tools: list) -> None:
        try:
            self.bridge.activate(server_id, client, tools)
            count = sum(
                1 for item in self.bridge.definitions()
                if item.get("function", {}).get("name", "").startswith(
                    f"mcp__{server_id}__"
                )
            )
            self.status.emit(f"MCP «{server_id}» activo · {count} herramienta(s)")
        except Exception as exc:
            self.bridge.deactivate(server_id)
            self.status.emit(f"MCP «{server_id}» no disponible")
            self.error.emit(f"«{server_id}»: {exc}")
        self._emit_changed()

    def _on_error(self, server_id: str, message: str) -> None:
        self.bridge.deactivate(server_id)
        self.status.emit(f"MCP «{server_id}» no disponible")
        self.error.emit(f"«{server_id}»: {message}")
        self._emit_changed()

    def _on_thread_finished(self) -> None:
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

    def _emit_changed(self) -> None:
        self.servers_changed.emit(
            list(self.bridge.active_servers),
            list(self._threads),
        )
