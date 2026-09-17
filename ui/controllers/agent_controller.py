"""Gestiona los agentes: carga, edición y activación."""
from __future__ import annotations

import logging

from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QWidget

from core.agents import Agent, AgentStore, default_agents

from ..views.dialogs import edit_agent, warn

logger = logging.getLogger(__name__)


class AgentController(QObject):
    agent_changed = Signal(object)   # Agent

    def __init__(
        self,
        parent: QObject,
        parent_widget: QWidget,
        available_tools: list[str],
        store: AgentStore | None = None,
        initial_name: str = "",
    ):
        super().__init__(parent)
        self._parent_widget = parent_widget
        self._available_tools = list(available_tools)
        self.store = store or AgentStore()
        self.agents: list[Agent] = self.store.load()
        # Si el usuario borró todos los agentes a mano, la app necesita al
        # menos uno para funcionar. Recreamos los defaults y los guardamos.
        # El usuario siempre puede volver a borrarlos editando agents.json.
        if not self.agents:
            self.agents = default_agents()
            self.store.save(self.agents)
        self._active_name = self._resolve_active_name(initial_name)

    # -- API pública ---------------------------------------------------------

    @property
    def active_name(self) -> str:
        return self._active_name

    def names(self) -> list[str]:
        return [agent.name for agent in self.agents]

    def active_agent(self) -> Agent:
        for agent in self.agents:
            if agent.name == self._active_name:
                return agent
        return self.agents[0]

    def set_available_tools(self, names: list[str]) -> None:
        self._available_tools = list(names)

    def set_active(self, name: str) -> None:
        if not name:
            return
        if not any(agent.name == name for agent in self.agents):
            return
        if name == self._active_name:
            return
        self._active_name = name
        self.agent_changed.emit(self.active_agent())

    def create_new(self) -> None:
        """Abre el diálogo de edición sobre una plantilla en blanco. Si el
        usuario confirma, el resultado se AÑADE a la lista (no reemplaza al
        agente activo) y pasa a ser el agente activo."""
        existing_names = {agent.name for agent in self.agents}
        base = "Nuevo agente"
        name = base
        counter = 2
        while name in existing_names:
            name = f"{base} {counter}"
            counter += 1
        template = Agent(name=name)

        created = edit_agent(
            self._parent_widget,
            agent=template,
            available_tools=self._available_tools,
        )
        if created is None:
            return
        if created.name in existing_names:
            warn(
                self._parent_widget,
                "Agente",
                f"Ya existe un agente llamado «{created.name}».",
            )
            return

        self.agents.append(created)
        self._active_name = created.name
        self.store.save(self.agents)
        self.agent_changed.emit(created)

    def edit_active(self) -> None:
        current = self.active_agent()
        edited = edit_agent(
            self._parent_widget,
            agent=current,
            available_tools=self._available_tools,
        )
        if edited is None:
            return

        new_list: list[Agent] = []
        replaced = False
        for agent in self.agents:
            if agent.name == current.name:
                new_list.append(edited)
                replaced = True
            else:
                new_list.append(agent)
        if not replaced:
            new_list.append(edited)

        # Si el nuevo nombre coincide con el de OTRO agente ya existente
        # (p.ej. al renombrar a un nombre ya usado), ese otro se descarta
        # para no tener dos agentes con el mismo nombre. El resto conserva
        # su posición original: editar un agente no debe reordenar la lista.
        deduped = [a for a in new_list if a is edited or a.name != edited.name]

        self.agents = deduped
        self._active_name = edited.name
        self.store.save(self.agents)
        self.agent_changed.emit(edited)

    # -- interno -------------------------------------------------------------

    def _resolve_active_name(self, initial: str) -> str:
        if initial and any(a.name == initial for a in self.agents):
            return initial
        # El agente guardado en config.json ya no existe (renombrado o
        # borrado). Caemos al primero, pero lo registramos para que sea
        # diagnosticable. Antes esto era silencioso: el usuario creía
        # seguir con su agente cuando en realidad estaba con otro.
        if initial:
            logger.warning(
                "Agente guardado %r no existe; activando %r",
                initial,
                self.agents[0].name,
            )
        return self.agents[0].name
