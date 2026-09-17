from __future__ import annotations

import shlex

from PySide6.QtCore import QObject, QThread, Signal
from PySide6.QtWidgets import QWidget

from core.workspace import Workspace
from plugins.mcp import (
    MCPClient,
    MCPError,
    MCPServerConfig,
    MCPToolBridge,
)

from ..views.dialogs import warn
from ..workers import MCPWorker

# Único servidor MCP soportado desde la UI: acceso a archivos del workspace
# actual. El interruptor de la sidebar activa/desactiva justo este servidor,
# sin pedir nombre ni comando.
FILESYSTEM_SERVER_ID = "fs"


class MCPController(QObject):
    """Gestiona el ciclo de vida de los servidores MCP.

    Guarda la ``MCPServerConfig`` de cada servidor activo para poder
    reconectar cuando el proceso hijo muere sin que el usuario tenga que
    reintroducir el comando. Cuando una llamada MCP falla, el
    ``ChatController`` emite ``mcp_error(server_id)`` y el
    ``AppController`` lo reenvía a ``report_failure``. A partir de ese
    momento el servidor queda marcado como "sin respuesta" y la sidebar
    ofrece reconectarlo.
    """

    # active_ids, pending_ids, dead_ids
    servers_changed = Signal(list, list, list)
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
        # Config completa por servidor, para poder reconectar.
        self._configs: dict[str, MCPServerConfig] = {}
        # Servidores cuyo proceso hijo ha dejado de responder.
        self._dead: set[str] = set()

    # -- consulta ------------------------------------------------------------

    @property
    def pending_ids(self) -> list[str]:
        return list(self._threads)

    @property
    def dead_ids(self) -> list[str]:
        return sorted(self._dead)

    # -- API pública ---------------------------------------------------------

    def rebind(self, bridge: MCPToolBridge, workspace: Workspace) -> None:
        """Sustituye el bridge y el workspace en caliente.

        Cierra las conexiones del workspace anterior. No recrea el objeto.
        """
        self._shutdown_connections()
        self.bridge = bridge
        self.workspace = workspace
        self._emit_changed()

    def toggle(self, enabled: bool) -> None:
        """Activa o desactiva el servidor MCP de archivos del workspace actual."""
        server_id = FILESYSTEM_SERVER_ID
        if enabled:
            if server_id in self.bridge.active_servers or server_id in self._threads:
                return
            if server_id in self._dead:
                self.reconnect(server_id)
                return
            command = (
                f"npx -y @modelcontextprotocol/server-filesystem "
                f"{shlex.quote(str(self.workspace.root))}"
            )
            self._connect(server_id, command)
        else:
            self.deactivate(server_id)

    def deactivate(self, server_id: str) -> None:
        """Cierra el servidor y olvida su configuración."""
        if server_id in self._threads:
            return
        self._dead.discard(server_id)
        self._configs.pop(server_id, None)
        self.bridge.deactivate(server_id)
        self.status.emit(f"MCP «{server_id}» desactivado")
        self._emit_changed()

    def report_failure(self, server_id: str) -> None:
        """Marca un servidor como caído. Se llama cuando una llamada MCP
        devuelve un error que sugiere que el proceso hijo ya no responde.

        La configuración se conserva para poder reconectar. Las herramientas
        se ocultan del bridge hasta que la reconexión se complete.
        """
        if server_id in self._dead:
            return
        if server_id not in self._configs:
            return
        if server_id in self._threads:
            # Todavía se está conectando por primera vez; no hacer nada.
            return

        self._dead.add(server_id)
        self.bridge.deactivate(server_id)
        self.status.emit(f"MCP «{server_id}» sin respuesta")
        self._emit_changed()

    def reconnect(self, server_id: str) -> None:
        """Reconecta un servidor caído usando la config guardada."""
        config = self._configs.get(server_id)
        if config is None:
            return
        if server_id in self._threads:
            return
        self._dead.discard(server_id)
        self.status.emit(f"Reconectando MCP «{server_id}»…")
        self._connect(server_id, config.command, args=config.args)

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

    def _connect(
        self,
        server_id: str,
        command: str,
        *,
        args: tuple[str, ...] | None = None,
    ) -> None:
        try:
            if args is None:
                parts = shlex.split(command)
            else:
                parts = [command, *args]
            if not parts:
                raise ValueError("El comando MCP está vacío.")
            config = MCPServerConfig(
                command=parts[0],
                args=tuple(parts[1:]),
                cwd=str(self.workspace.root),
            )
            client = MCPClient(config)
        except (MCPError, ValueError) as exc:
            warn(self._parent_widget, "MCP", str(exc))
            self._emit_changed()
            return

        # Solo guardamos la config al ir a conectar. Así `deactivate`
        # manual olvida el comando, pero `report_failure` lo conserva.
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

    def _on_loaded(self, server_id: str, client: MCPClient, tools: list) -> None:
        try:
            self.bridge.activate(server_id, client, tools)
            count = sum(
                1
                for item in self.bridge.definitions()
                if item.get("function", {}).get("name", "").startswith(
                    f"mcp__{server_id}__"
                )
            )
            self.status.emit(f"MCP «{server_id}» activo · {count} herramienta(s)")
        except Exception as exc:
            self.bridge.deactivate(server_id)
            self._dead.add(server_id)
            self.status.emit(f"MCP «{server_id}» no disponible")
            self.error.emit(f"«{server_id}»: {exc}")
        self._emit_changed()

    def _on_error(self, server_id: str, message: str) -> None:
        self.bridge.deactivate(server_id)
        self._dead.add(server_id)
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
            sorted(self._dead),
        )
