"""Coordinador de la aplicación."""
from __future__ import annotations

import logging
import re
import time
from pathlib import Path

from PySide6.QtCore import QObject, QThread, Slot
from PySide6.QtWidgets import QApplication, QFileDialog

from core.agents import Agent, AgentStore
from core.shutdown import (
    SHUTDOWN_BUDGET_SECONDS,
    remaining,
)

from ..chat_state import ChatState
from ..workers import CapabilitiesWorker
from core.composite_tools import (
    CachedToolProvider,
    CompositeToolProvider,
    FilteredToolProvider,
)
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


# Herramientas cuyos resultados se cachean durante unos segundos.
# Son de solo lectura: ejecutarlas dos veces con los mismos argumentos
# no cambia el resultado en un margen de segundos.
_CACHEABLE_TOOLS = {
    # núcleo
    "listar_carpeta",
    "leer_archivo",
    # plugins
    "buscar_en_workspace",
    "git_status",
    "git_diff",
    "git_log",
    "git_show",
    # MCP de solo lectura (server-filesystem)
    "mcp__fs__read_file",
    "mcp__fs__read_text_file",
    "mcp__fs__read_media_file",
    "mcp__fs__read_multiple_files",
    "mcp__fs__list_directory",
    "mcp__fs__list_directory_with_sizes",
    "mcp__fs__directory_tree",
    "mcp__fs__search_files",
    "mcp__fs__get_file_info",
    "mcp__fs__list_allowed_directories",
}

# Herramientas que, al ejecutarse, invalidan TODO el caché: el
# workspace puede haber cambiado y los resultados previos son basura.
_INVALIDATING_TOOLS = {
    # núcleo
    "crear_archivo",
    "crear_carpeta",
    "escribir_archivo",
    "borrar_archivo",
    # plugins
    "ejecutar_comando",
    # MCP de escritura
    "mcp__fs__write_file",
    "mcp__fs__edit_file",
    "mcp__fs__create_directory",
    "mcp__fs__move_file",
}


logger = logging.getLogger(__name__)


def split_prompts(text: str) -> list[str]:
    """Divide el texto en prompts por separadores ``---`` o ``===``.

    Un separador es una línea que contiene SOLO tres o más guiones o
    iguales (con espacios opcionales alrededor). Devuelve solo las
    partes no vacías, con whitespace recortado. Si no hay separadores,
    devuelve el texto completo como una única entrada.
    """
    if not text:
        return []
    parts = re.split(
        r"^\s*(?:-{3,}|={3,})\s*$",
        text,
        flags=re.MULTILINE,
    )
    return [p.strip() for p in parts if p.strip()]


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
        # Referencia al QThread de consulta de capacidades del modelo.
        # Solo puede haber uno activo a la vez; si el usuario cambia
        # de modelo rapidamente, el anterior se descarta.
        self._caps_thread: QThread | None = None
        self._caps_worker: CapabilitiesWorker | None = None
        # Generación de la última consulta de capacidades lanzada.
        # Sirve para descartar resultados obsoletos cuando el usuario
        # cambia de modelo varias veces seguidas.
        self._caps_generation: int = 0
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

        # Propagar el modo piloto automático persistido al controller
        # y al checkbox de la sidebar antes del wiring. El orden
        # importa: primero el principal, luego la extensión (que
        # depende del principal para tener efecto).
        self.chat_ctrl.set_auto_approve(self.config.auto_approve_tools)
        self.view.sidebar.set_auto_approve(self.config.auto_approve_tools)
        self.chat_ctrl.set_auto_approve_shell(self.config.auto_approve_shell)
        self.view.sidebar.set_auto_approve_shell(
            self.config.auto_approve_shell
        )
        # Verificador: el checkbox y el hook se derivan del mismo flag.
        self.view.sidebar.set_verificador(self.config.verificador_enabled)
        self._apply_verificador(self.config.verificador_enabled)

        self._wire()
        self._apply_initial_state()
        self._apply_agent(self.agent_ctrl.active_agent())
        self._restore_conversation(initial_messages)
        self.model_ctrl.load()

    # -- construcción del composite -----------------------------------------

    def _rebuild_composite(self) -> None:
        """Reconstruye el composite con plugins, caché y MCP.

        El orden importa: el primero que declara una herramienta es su
        dueño. Los plugins van antes que MCP para que las herramientas
        locales (shell, git, search) ganen si colisionan.

        Envolvemos el composite en CachedToolProvider para absorber
        ráfagas del modelo que pide el mismo listado o lectura dos veces
        seguidas. Las operaciones de escritura y shell invalidan el caché.
        """
        plugins = instantiate_plugins(self._plugin_factories, self.workspace)
        self._plugins = plugins
        inner = CompositeToolProvider([*plugins, self.mcp])
        self.composite = CachedToolProvider(
            inner,
            cacheable=_CACHEABLE_TOOLS,
            invalidating=_INVALIDATING_TOOLS,
        )

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

        rp = self.view.right_panel
        rp.mcp_toggle_requested.connect(self.mcp_ctrl.toggle)
        self.mcp_ctrl.servers_changed.connect(rp.set_mcp_servers)
        self.mcp_ctrl.servers_changed.connect(self._on_mcp_servers_changed)
        self.mcp_ctrl.status.connect(self.view.set_status)
        self.mcp_ctrl.error.connect(lambda msg: warn(self.view, "MCP", msg))

        s.workspace_change_requested.connect(self._choose_workspace)

        cp.message_submitted.connect(self._on_message_submitted)
        cp.send_all_requested.connect(self._on_send_all_requested)
        cp.cancel_requested.connect(self.chat_ctrl.cancel)
        cp.regenerate_requested.connect(self._on_regenerate)
        cp.copy_requested.connect(self._on_copy_last_response)
        cp.clear_requested.connect(self._clear_chat)
        self.chat_ctrl.state_changed.connect(self._on_state_changed)
        self.chat_ctrl.status.connect(self.view.set_status)
        self.chat_ctrl.mcp_error.connect(self.mcp_ctrl.report_failure)
        self.chat_ctrl.queue_progress.connect(self._on_queue_progress)
        self.chat_ctrl.queue_list_set.connect(
            self.view.right_panel.set_queue_list
        )
        self.chat_ctrl.queue_item_status_changed.connect(
            self.view.right_panel.update_queue_item
        )
        self.chat_ctrl.queue_finished.connect(self._on_queue_finished)
        self.chat_ctrl.queue_paused.connect(
            self.view.right_panel.show_queue_paused
        )
        self.view.right_panel.queue_retry_requested.connect(
            self.chat_ctrl.resume_queue_retry
        )
        self.view.right_panel.queue_skip_requested.connect(
            self.chat_ctrl.resume_queue_skip
        )
        self.view.right_panel.queue_cancel_requested.connect(
            self.chat_ctrl.cancel_paused_queue
        )
        self.chat_ctrl.metrics_updated.connect(self.diagnostics_ctrl.set_metrics)

        s.clear_chat_requested.connect(self._clear_chat)
        s.auto_approve_changed.connect(self._on_auto_approve_changed)
        s.auto_approve_shell_changed.connect(
            self._on_auto_approve_shell_changed
        )
        s.verificador_changed.connect(self._on_verificador_changed)

        # Emitir el estado MCP inicial AHORA que las señales ya están
        # conectadas. Antes, MCPController._emit_changed() en __init__
        # se llamaba antes del wiring y el evento se perdía.
        self.mcp_ctrl.emit_current_state()

    def _apply_initial_state(self) -> None:
        self.view.resize(self.config.width, self.config.height)
        self.view.sidebar.set_workspace_name(self._workspace_name())
        self.view.right_panel.set_workspace(self.workspace.root)
        # No llamar a set_mcp_servers aquí: MCPController.emit_current_state()
        # (en _wire) ya emite el estado real. Llamarlo con listas vacías
        # borraba el botón MCP que se acababa de crear.
        self.view.sidebar.set_agents(
            self.agent_ctrl.categories(),
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
        # apply_model=True: si el agente tiene un modelo asociado, se
        # selecciona en la sidebar. Si no, se mantiene el actual.
        self._apply_agent(agent, apply_model=True)
        self.view.sidebar.set_agents(
            self.agent_ctrl.categories(), agent.name
        )
        self.config.current_agent = agent.name
        self.config.save()

    def _apply_agent(self, agent: Agent, *, apply_model: bool = False) -> None:
        if agent.allowed_tools is None:
            self.chat_ctrl.rebind_tools(self.composite)
        else:
            allowed = set(agent.allowed_tools)
            self.chat_ctrl.rebind_tools(FilteredToolProvider(self.composite, allowed))

        self.chat_ctrl.set_current_options(agent.options())
        self.chat_ctrl.set_current_system_prompt(agent.system_prompt)

        # Solo cambiar el modelo si:
        #   · el llamante lo pide (cambio real de agente), y
        #   · el agente tiene modelo definido, y
        #   · ese modelo está disponible en la sidebar.
        # Si el agente apunta a un modelo desinstalado, se mantiene el
        # actual y no se avisa (la app sigue siendo usable).
        if apply_model and agent.model:
            if self.view.sidebar.select_model(agent.model):
                self.config.model = agent.model
                self.config.save()
                self.chat_ctrl.set_current_model(agent.model)
                self._refresh_capabilities(agent.model)
            else:
                self.view.set_status(
                    f"El modelo «{agent.model}» del agente no está disponible"
                )

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
        # Compartir la lista con el AgentController para que el diálogo
        # de edición de agente pueda mostrar los modelos disponibles.
        self.agent_ctrl.set_available_models(models)
        # Consultar el modo del modelo activo para mostrar el badge.
        active_model = self.view.sidebar.current_model() or self.config.model
        if active_model:
            self._refresh_capabilities(active_model)
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
        self._refresh_capabilities(name)

    def _refresh_capabilities(self, model: str) -> None:
        """Consulta /api/show en un hilo aparte y actualiza el badge.

        Si ya hay una consulta en curso, se descarta y se lanza la
        nueva. La consulta antigua termina en su propio hilo; su
        resultado se descarta al llegar por comparación de generación.
        No se llama a `wait()`: bloquearía el hilo UI hasta 500 ms.
        """
        # Incrementamos la generación: cualquier resultado que llegue
        # con una generación anterior se descarta.
        self._caps_generation += 1
        generation = self._caps_generation

        if self._caps_thread is not None and self._caps_thread.isRunning():
            # Sin wait(): el hilo antiguo termina cuando su
            # get_capabilities() retorne (timeout 5s). Su resultado
            # se descarta por generación obsoleta.
            self._caps_thread.quit()

        self._caps_thread = QThread(self)
        self._caps_worker = CapabilitiesWorker(
            self.config.ollama_host, model, generation=generation
        )
        self._caps_worker.moveToThread(self._caps_thread)
        self._caps_thread.started.connect(self._caps_worker.run)
        self._caps_worker.finished.connect(self._on_capabilities_ready)
        self._caps_worker.error.connect(self._on_capabilities_error)
        self._caps_worker.finished.connect(self._caps_thread.quit)
        self._caps_worker.error.connect(self._caps_thread.quit)
        self._caps_thread.finished.connect(self._cleanup_caps_thread)
        self._caps_thread.start()

    @Slot(str, object, int)
    def _on_capabilities_ready(
        self, model: str, caps, generation: int
    ) -> None:
        # Descartar si la generación es obsoleta (el usuario ya pidió
        # otra consulta) o si el modelo activo ha cambiado.
        if generation != self._caps_generation:
            return
        if model != self.view.sidebar.current_model():
            return
        self.view.sidebar.set_capabilities(caps.tool_mode)
        self.view.sidebar.set_recommendation(
            getattr(caps, "recommendation", "") or ""
        )
        # Propagar el limite de contexto al ChatController para que la
        # compactacion del historial se adapte al modelo activo.
        self.chat_ctrl.set_context_limit(caps.context_length)
        # Si el modo viene de un override manual, lo indicamos en el
        # status para que el usuario sepa que su config está activa.
        if getattr(caps, "source", "") == "override":
            self.view.set_status(
                f"Modo forzado manualmente para {model}: {caps.tool_mode}"
            )

    @Slot(str, str, int)
    def _on_capabilities_error(
        self, model: str, message: str, generation: int
    ) -> None:
        if generation != self._caps_generation:
            return
        if model == self.view.sidebar.current_model():
            self.view.sidebar.set_capabilities("unknown")

    def _cleanup_caps_thread(self) -> None:
        # `sender()` es el QThread que acaba de terminar. Si no es el
        # actual (porque ya lanzamos uno nuevo), solo liberamos el
        # antiguo.
        thread = self.sender()
        if thread is not None and thread is not self._caps_thread:
            thread.deleteLater()
            return
        if self._caps_thread is not None:
            self._caps_thread.deleteLater()
        self._caps_thread = None
        self._caps_worker = None

    # -- MCP -----------------------------------------------------------------

    @Slot(list, list, list, list)
    def _on_mcp_servers_changed(
        self,
        _entries: list,
        _active: list,
        _pending: list,
        _dead: list,
    ) -> None:
        # El catálogo de tools cambió (MCP acaba de activar/desactivar
        # un servidor). Invalidar la caché del composite para que las
        # próximas llamadas a definitions() recalculen.
        invalidate = getattr(self.composite, "invalidate", None)
        if callable(invalidate):
            invalidate()
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

            # El workspace ha cambiado: cualquier resultado cacheado
            # (listados, lecturas, git status) apunta al workspace viejo.
            self.composite.cache.invalidate_all()

            self.config.workspace = str(Path(selected).resolve())
            self.config.save()
            self.view.sidebar.set_workspace_name(self._workspace_name())
            self.view.right_panel.set_workspace(self.workspace.root)
            self.view.set_status("Workspace cambiado")
        except WorkspaceError as exc:
            warn(self.view, "Workspace", str(exc))

    # -- chat ----------------------------------------------------------------

    @Slot()
    def _on_message_submitted(self) -> None:
        # Verificar el modelo ANTES de consumir el input. Si Ollama
        # no responde o el usuario no ha elegido modelo, el texto
        # se perderia en el clear() de take_input().
        model = self.view.sidebar.current_model()
        if not model:
            self.view.set_status("Selecciona un modelo antes de enviar")
            return
        text = self.view.chat_panel.take_input()
        if not text:
            return
        agent = self.agent_ctrl.active_agent()
        self.chat_ctrl.send(
            text,
            model,
            agent.options(),
            agent.system_prompt,
        )

    @Slot()
    def _on_send_all_requested(self) -> None:
        # Mismo orden que _on_message_submitted: verificar modelo
        # antes de consumir el input.
        model = self.view.sidebar.current_model()
        if not model:
            self.view.set_status("Selecciona un modelo antes de enviar")
            return
        text = self.view.chat_panel.take_input()
        if not text:
            return
        prompts = split_prompts(text)
        if not prompts:
            # No hay nada que enviar. Restaurar el texto al input
            # para que el usuario no lo pierda por escribir solo un
            # separador (--- o ===).
            self.view.chat_panel.input.setPlainText(text)
            self.view.set_status("No hay prompts válidos en el texto")
            return
        if len(prompts) == 1:
            # Sin separadores: tratar como un mensaje normal, sin cola.
            agent = self.agent_ctrl.active_agent()
            self.chat_ctrl.send(
                prompts[0], model, agent.options(), agent.system_prompt,
            )
            return
        agent = self.agent_ctrl.active_agent()
        ok = self.chat_ctrl.enqueue(
            prompts, model, agent.options(), agent.system_prompt,
        )
        if not ok:
            self.view.set_status("Ya hay un turno o una cola en curso")

    @Slot(int, int)
    def _on_queue_progress(self, current: int, total: int) -> None:
        self.view.set_status(f"Cola: {current}/{total}")

    @Slot()
    def _on_queue_finished(self) -> None:
        # Limpiar el todo list del panel derecho. Sin esta conexion,
        # los items quedaban visibles tras terminar la cola hasta
        # que el usuario enviara algo nuevo.
        self.view.right_panel.set_queue_list([])

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

    @Slot(object)
    def _on_state_changed(self, state: ChatState) -> None:
        """Refleja el estado del chat en la UI.

        El panel recibe el ChatState completo (sabe diferenciar
        STREAMING de CANCELLING). La sidebar solo necesita saber si
        hay trabajo en curso, asi que recibe is_active como bool.
        """
        self.view.chat_panel.set_state(state)
        self.view.sidebar.set_busy(state.is_active)
        self.view.right_panel.set_busy(state.is_active)

    @Slot(bool)
    def _on_auto_approve_changed(self, enabled: bool) -> None:
        self.config.auto_approve_tools = bool(enabled)
        # Si se apaga el piloto, la cascada del sidebar apagará también
        # la extensión y emitirá `auto_approve_shell_changed(False)`,
        # que persistirá el cambio. No lo hacemos aquí para evitar
        # doble save.
        self.config.save()
        self.chat_ctrl.set_auto_approve(enabled)
        if enabled:
            msg = (
                "Piloto automático ON · las tools se ejecutan sin diálogo"
            )
            if self.config.auto_approve_shell:
                msg += " · shell incluido"
            else:
                msg += " (excepto shell)"
            self.view.set_status(msg)

    @Slot(bool)
    def _on_auto_approve_shell_changed(self, enabled: bool) -> None:
        self.config.auto_approve_shell = bool(enabled)
        self.config.save()
        self.chat_ctrl.set_auto_approve_shell(enabled)
        if enabled:
            self.view.set_status(
                "⚠ Piloto automático: shell también auto-aprobado"
            )

    @Slot(bool)
    def _on_verificador_changed(self, enabled: bool) -> None:
        self.config.verificador_enabled = bool(enabled)
        self.config.save()
        self._apply_verificador(enabled)
        if enabled:
            self.view.set_status(
                "Verificador ON · sintaxis comprobada tras cada escritura"
            )
        else:
            self.view.set_status("Verificador OFF")

    def _apply_verificador(self, enabled: bool) -> None:
        """Construye o retira el hook de verificación.

        El hook es un callable que recibe la ruta relativa del archivo
        y devuelve texto de errores. Si el plugin no está disponible,
        se registra un hook vacío y el usuario no nota nada.
        """
        if not enabled:
            self.chat_ctrl.set_verificador_hook(None)
            return
        plugin = self._find_verificador()
        if plugin is None:
            # Config activa pero plugin no cargado: hook no-op.
            self.chat_ctrl.set_verificador_hook(None)
            return
        self.chat_ctrl.set_verificador_hook(plugin.verificar_archivo)

    def _find_verificador(self):
        """Devuelve el VerificadorProvider si está cargado, o None."""
        for plugin in getattr(self, "_plugins", []):
            if hasattr(plugin, "verificar_archivo"):
                return plugin
        return None

    def _clear_chat(self) -> None:
        if self.chat_ctrl.is_streaming():
            return
        self.chat_ctrl.clear()
        self.view.chat_panel.clear_chat()
        self.diagnostics_ctrl.reset()
        # Nueva conversación: descartar resultados cacheados.
        self.composite.cache.invalidate_all()
        self.view.set_status("Nueva conversación")

    # -- ciclo de vida -------------------------------------------------------

    def _restore_conversation(self, messages: list[dict]) -> None:
        if not messages:
            return
        self.view.chat_panel.restore_conversation(messages)
        self.diagnostics_ctrl.refresh_context()
        self.view.set_status(f"Conversación restaurada ({len(messages)} mensajes)")

    def shutdown(self) -> None:
        """Cierra el arbol de la app con un deadline global.

        El presupuesto total lo marca `SHUTDOWN_BUDGET_SECONDS` (menor
        que el watchdog de main.py). Cada sub-shutdown recibe el mismo
        `deadline` y reparte lo que queda. Antes los timeouts se
        sumaban y podian sobrepasar el watchdog, dejando cierres
        truncados.
        """
        self.config.width = self.view.width()
        self.config.height = self.view.height()
        self.config.save()

        deadline = time.monotonic() + SHUTDOWN_BUDGET_SECONDS

        # Orden importante: primero los controllers que usan ollama
        # (para que su worker termine), despues el propio ollama.
        results = {
            "chat": self.chat_ctrl.shutdown(deadline),
            "mcp": self.mcp_ctrl.shutdown(deadline),
            "model": self.model_ctrl.shutdown(deadline),
            "ollama": self.ollama.shutdown(
                remaining(deadline, default=3.0)
            ),
        }

        if not all(results.values()):
            logger.warning("Shutdown con avisos: %r", results)
