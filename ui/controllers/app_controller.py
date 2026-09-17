"""Coordinador de la aplicación."""
from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QObject, Slot
from PySide6.QtWidgets import QApplication, QFileDialog

from core.agents import Agent, AgentStore
from core.composite_tools import CompositeToolProvider, FilteredToolProvider
from core.config import AppConfig
from core.history import HistoryStore
from core.mcp_servers import MCPServerStore
from core.ollama import OllamaClient
from core.plugins_registry import (
    discover_plugin_factories,
    instantiate_plugins,
)
from core.tools import ToolRegistry
from core.workspace import Workspace, WorkspaceError
from plugins.mcp import MCPToolBridge

from ..views.dialogs import warn
from ..views.main_window import MainWindow
from .agent_controller import AgentController
from .chat_controller import ChatController
from .diagnostics_controller import DiagnosticsController
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

        # Descubrimiento dinámico de plugins vía entry points.
        self._plugin_factories = discover_plugin_factories()
        self._rebuild_composite()

        self.history_store = HistoryStore()
        saved = self.history_store.load()
        initial_messages = saved.messages if saved else []

        self.agent_ctrl = AgentController(
            self,
            self.view,
            available_tools=self._all_tool_names(),
            store=AgentStore(),
            initial_name=self.config.current_agent,
        )

        self.model_ctrl = ModelController(self, self.ollama)
        self.mcp_ctrl = MCPController(
            self, self.view, self.mcp, self.workspace,
            store=MCPServerStore(),
        )
        self.chat_ctrl = ChatController(
            self,
            self.view,
            self.ollama,
            self.composite,
            self.view.chat_panel.renderer,
            store=self.history_store,
            initial_messages=initial_messages,
        )
        self.diagnostics_ctrl = DiagnosticsController(
            self,
            self.chat_ctrl,
            self.view.sidebar.diagnostics,
        )

        self._wire()
        self._apply_initial_state()
        self._apply_agent(self.agent_ctrl.active_agent())
        self._restore_conversation(initial_messages)
        self.model_ctrl.load()

    # -- construcción del composite -----------------------------------------

    def _rebuild_composite(self) -> None:
        """Reconstruye el composite con los plugins disponibles.

        El orden importa: el primero que declara una herramienta es su
        dueño. Los plugins van antes que MCP para que las herramientas
        locales (shell, git, search) ganen si colisionan.
        """
        plugins = instantiate_plugins(self._plugin_factories, self.workspace)
        self.composite = CompositeToolProvider([*plugins, self.mcp])

    # -- wiring --------------------------------------------------------------

    def _wire(self) -> None:
        s = self.view.sidebar
        cp = self.view.chat_panel

        s.agent_changed.connect(self._on_agent_name_selected)
        s.agent_edit_requested.connect(self.agent_ctrl.edit_active)
        s.agent_create_requested.connect(self.agent_ctrl.create_new)
        self.agent_ctrl.agent_changed.connect(self._on_agent_changed)

        s.model_refresh_requested.connect(self.model_ctrl.load)
        s.model_selected.connect(self._on_model_selected)
        self.model_ctrl.loading.connect(
            lambda: self.view.set_status("Consultando Ollama…")
        )
        self.model_ctrl.loaded.connect(self._on_models_loaded)
        self.model_ctrl.error.connect(self._on_models_error)

        s.mcp_toggle_requested.connect(self.mcp_ctrl.toggle)
        self.mcp_ctrl.servers_changed.connect(s.set_mcp_servers)
        self.mcp_ctrl.servers_changed.connect(self._on_mcp_servers_changed)
        self.mcp_ctrl.status.connect(self.view.set_status)
        self.mcp_ctrl.error.connect(lambda msg: warn(self.view, "MCP", msg))

        s.workspace_change_requested.connect(self._choose_workspace)

        cp.message_submitted.connect(self._on_message_submitted)
        cp.cancel_requested.connect(self.chat_ctrl.cancel)
        cp.regenerate_requested.connect(self._on_regenerate)
        cp.copy_requested.connect(self._on_copy_last_response)
        cp.clear_requested.connect(self._clear_chat)
        self.chat_ctrl.streaming_changed.connect(self._on_streaming_changed)
        self.chat_ctrl.status.connect(self.view.set_status)
        self.chat_ctrl.mcp_error.connect(self.mcp_ctrl.report_failure)

        s.clear_chat_requested.connect(self._clear_chat)

        # Emitir el estado MCP inicial AHORA que las señales ya están
        # conectadas. Antes, MCPController._emit_changed() en __init__
        # se llamaba antes del wiring y el evento se perdía.
        self.mcp_ctrl.emit_current_state()

    def _apply_initial_state(self) -> None:
        self.view.resize(self.config.width, self.config.height)
        self.view.sidebar.set_workspace_name(self._workspace_name())
        # No llamar a set_mcp_servers aquí: MCPController.emit_current_state()
        # (en _wire) ya emite el estado real. Llamarlo con listas vacías
        # borraba el botón MCP que se acababa de crear.
        self.view.sidebar.set_agents(
            self.agent_ctrl.names(),
            self.agent_ctrl.active_name,
        )

    # -- agente --------------------------------------------------------------

    def _all_tool_names(self) -> list[str]:
        return [
            str(d.get("function", {}).get("name", ""))
            for d in self.composite.definitions()
            if d.get("function", {}).get("name")
        ]

    @Slot(str)
    def _on_agent_name_selected(self, name: str) -> None:
        if not name:
            return
        self.agent_ctrl.set_active(name)

    @Slot(object)
    def _on_agent_changed(self, agent: Agent) -> None:
        self._apply_agent(agent)
        self.view.sidebar.set_agents(self.agent_ctrl.names(), agent.name)
        self.config.current_agent = agent.name
        self.config.save()

    def _apply_agent(self, agent: Agent) -> None:
        if agent.allowed_tools is None:
            self.chat_ctrl.rebind_tools(self.composite)
        else:
            allowed = set(agent.allowed_tools)
            self.chat_ctrl.rebind_tools(FilteredToolProvider(self.composite, allowed))

        self.chat_ctrl.set_current_options(agent.options())
        self.chat_ctrl.set_current_system_prompt(agent.system_prompt)

        self.diagnostics_ctrl.set_model(
            self.view.sidebar.current_model() or self.config.model,
            agent.temperature,
            agent.num_ctx,
        )

    # -- modelo --------------------------------------------------------------

    @Slot(list)
    def _on_models_loaded(self, models: list[str]) -> None:
        current = self.config.model if self.config.model in models else None
        self.view.sidebar.set_models(models, current)
        self.view.set_status(f"{len(models)} modelo(s) disponibles")
        agent = self.agent_ctrl.active_agent()
        self.diagnostics_ctrl.set_model(
            self.view.sidebar.current_model() or self.config.model,
            agent.temperature,
            agent.num_ctx,
        )

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
        self.chat_ctrl.set_current_model(name)
        agent = self.agent_ctrl.active_agent()
        self.diagnostics_ctrl.set_model(name, agent.temperature, agent.num_ctx)

    # -- MCP -----------------------------------------------------------------

    @Slot(list, list, list, list)
    def _on_mcp_servers_changed(
        self,
        _entries: list,
        _active: list,
        _pending: list,
        _dead: list,
    ) -> None:
        self.agent_ctrl.set_available_tools(self._all_tool_names())
        self._apply_agent(self.agent_ctrl.active_agent())

    # -- workspace -----------------------------------------------------------

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
            self.mcp = new_bridge
            self._rebuild_composite()

            self.agent_ctrl.set_available_tools(self._all_tool_names())
            self._apply_agent(self.agent_ctrl.active_agent())

            self.config.workspace = str(Path(selected).resolve())
            self.config.save()
            self.view.sidebar.set_workspace_name(self._workspace_name())
            self.view.set_status("Workspace cambiado")
        except WorkspaceError as exc:
            warn(self.view, "Workspace", str(exc))

    # -- chat ----------------------------------------------------------------

    @Slot()
    def _on_message_submitted(self) -> None:
        text = self.view.chat_panel.take_input()
        if not text:
            return
        model = self.view.sidebar.current_model()
        if not model:
            return
        agent = self.agent_ctrl.active_agent()
        self.chat_ctrl.send(
            text,
            model,
            agent.options(),
            agent.system_prompt,
        )

    @Slot()
    def _on_regenerate(self) -> None:
        model = self.view.sidebar.current_model()
        if not model:
            return
        self.chat_ctrl.regenerate(model)

    @Slot()
    def _on_copy_last_response(self) -> None:
        text = self.chat_ctrl.last_assistant_text()
        if not text:
            self.view.set_status("Nada que copiar")
            return
        clipboard = QApplication.clipboard()
        clipboard.setText(text)
        self.view.set_status("Respuesta copiada")

    @Slot(bool)
    def _on_streaming_changed(self, streaming: bool) -> None:
        self.view.chat_panel.set_streaming(streaming)
        self.view.sidebar.set_busy(streaming)

    def _clear_chat(self) -> None:
        if self.chat_ctrl.is_streaming():
            return
        self.chat_ctrl.clear()
        self.view.chat_panel.clear_chat()
        self.diagnostics_ctrl.reset()
        self.view.set_status("Nueva conversación")

    # -- ciclo de vida -------------------------------------------------------

    def _restore_conversation(self, messages: list[dict]) -> None:
        if not messages:
            return
        self.view.chat_panel.restore_conversation(messages)
        self.diagnostics_ctrl.refresh_context()
        self.view.set_status(f"Conversación restaurada ({len(messages)} mensajes)")

    def shutdown(self) -> None:
        self.config.width = self.view.width()
        self.config.height = self.view.height()
        self.config.save()
        self.chat_ctrl.shutdown()
        self.mcp_ctrl.shutdown()
        self.model_ctrl.shutdown()