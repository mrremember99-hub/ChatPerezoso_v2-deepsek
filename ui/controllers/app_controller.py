"""Coordinador de la aplicación."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Slot
from PySide6.QtWidgets import QFileDialog

from core.config import AppConfig
from core.ollama import OllamaClient
from core.tools import ToolRegistry
from core.workspace import Workspace, WorkspaceError
from plugins.mcp import MCPToolBridge
from ..views.dialogs import warn
from ..views.main_window import MainWindow
from .chat_controller import ChatController
from .mcp_controller import MCPController
from .model_controller import ModelController


class AppController(QObject):
    def __init__(self, view: MainWindow) -> None:
        super().__init__()
        self.view = view

        self.config = AppConfig.load()
        self.ollama = OllamaClient(self.config.ollama_host)
        self.workspace = Workspace(self.config.workspace_path())
        self.tools = ToolRegistry(self.workspace)
        self.mcp = MCPToolBridge(self.tools)

        self.model_ctrl = ModelController(self, self.ollama)
        self.mcp_ctrl = MCPController(self, self.view, self.mcp, self.workspace)
        self.chat_ctrl = ChatController(
            self, self.view, self.ollama, self.mcp,
            self.view.chat_panel.renderer,
        )

        self._wire()
        self._apply_initial_state()
        self.model_ctrl.load()

    def _wire(self) -> None:
        s = self.view.sidebar
        cp = self.view.chat_panel

        s.model_refresh_requested.connect(self.model_ctrl.load)
        s.model_selected.connect(self._on_model_selected)
        self.model_ctrl.loading.connect(
            lambda: self.view.set_status("Consultando Ollama…")
        )
        self.model_ctrl.loaded.connect(self._on_models_loaded)
        self.model_ctrl.error.connect(self._on_models_error)

        s.mcp_add_requested.connect(self.mcp_ctrl.prompt_add)
        s.mcp_deactivate_requested.connect(self.mcp_ctrl.deactivate)
        self.mcp_ctrl.servers_changed.connect(s.set_mcp_servers)
        self.mcp_ctrl.status.connect(self.view.set_status)
        self.mcp_ctrl.error.connect(lambda msg: warn(self.view, "MCP", msg))

        s.workspace_change_requested.connect(self._choose_workspace)

        cp.message_submitted.connect(self._on_message_submitted)
        cp.cancel_requested.connect(self.chat_ctrl.cancel)
        self.chat_ctrl.streaming_changed.connect(self._on_streaming_changed)
        self.chat_ctrl.status.connect(self.view.set_status)

        s.clear_chat_requested.connect(self._clear_chat)

    def _apply_initial_state(self) -> None:
        self.view.resize(self.config.width, self.config.height)
        self.view.sidebar.set_workspace_name(self._workspace_name())
        self.view.sidebar.set_mcp_servers([], [])

    @Slot(list)
    def _on_models_loaded(self, models: list[str]) -> None:
        current = self.config.model if self.config.model in models else None
        self.view.sidebar.set_models(models, current)
        self.view.set_status(f"{len(models)} modelo(s) disponibles")

    @Slot(str)
    def _on_models_error(self, message: str) -> None:
        self.view.set_status("Ollama no disponible")
        warn(self.view, "Ollama", message)

    @Slot(str)
    def _on_model_selected(self, name: str) -> None:
        if not name:
            return
        self.config.model = name
        self.config.save()

    def _workspace_name(self) -> str:
        return self.workspace.root.name or str(self.workspace.root)

    def _choose_workspace(self) -> None:
        if self.chat_ctrl.is_streaming():
            return
        selected = QFileDialog.getExistingDirectory(
            self.view, "Seleccionar workspace", str(self.workspace.root)
        )
        if not selected:
            return
        try:
            self.workspace = Workspace(selected)
            self.tools = ToolRegistry(self.workspace)
            new_bridge = MCPToolBridge(self.tools)
            self.mcp_ctrl.rebind(new_bridge, self.workspace)
            self.chat_ctrl.rebind_bridge(new_bridge)
            self.mcp = new_bridge

            self.config.workspace = str(Path(selected).resolve())
            self.config.save()
            self.view.sidebar.set_workspace_name(self._workspace_name())
            self.view.set_status("Workspace cambiado")
        except WorkspaceError as exc:
            warn(self.view, "Workspace", str(exc))

    @Slot(str)
    def _on_message_submitted(self, text: str) -> None:
        text = self.view.chat_panel.take_input() or text
        model = self.view.sidebar.current_model()
        if not model:
            return
        self.chat_ctrl.send(text, model)

    @Slot(bool)
    def _on_streaming_changed(self, streaming: bool) -> None:
        self.view.chat_panel.set_streaming(streaming)
        self.view.sidebar.set_busy(streaming)

    def _clear_chat(self) -> None:
        if self.chat_ctrl.is_streaming():
            return
        self.chat_ctrl.clear()
        self.view.chat_panel.clear_chat()
        self.view.set_status("Nueva conversación")

    def shutdown(self) -> None:
        self.config.width = self.view.width()
        self.config.height = self.view.height()
        self.config.save()
        self.chat_ctrl.shutdown()
        self.mcp_ctrl.shutdown()
        self.model_ctrl.shutdown()
