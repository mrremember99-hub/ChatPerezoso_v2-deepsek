from __future__ import annotations

from core.agents import Agent
from ui.controllers.agent_controller import AgentController


class _FakeStore:
    def __init__(self, agents: list[Agent]):
        self._agents = agents
        self.saved: list[Agent] | None = None

    def load(self) -> list[Agent]:
        return list(self._agents)

    def save(self, agents: list[Agent]) -> None:
        self.saved = list(agents)


def _controller(qapp, agents: list[Agent]) -> AgentController:
    return AgentController(
        parent=None,
        parent_widget=None,
        available_tools=[],
        store=_FakeStore(agents),
        initial_name=agents[0].name,
    )


def test_edit_active_without_rename_preserves_order(qapp, monkeypatch):
    agents = [Agent(name="A"), Agent(name="B"), Agent(name="C")]
    ctrl = _controller(qapp, agents)
    ctrl.set_active("B")

    edited = Agent(name="B", system_prompt="nuevo prompt")
    monkeypatch.setattr(
        "ui.controllers.agent_controller.edit_agent",
        lambda *a, **k: edited,
    )
    ctrl.edit_active()

    # Editar "B" sin renombrarlo no debe mover su posición en la lista.
    assert [a.name for a in ctrl.agents] == ["A", "B", "C"]
    assert ctrl.active_agent().system_prompt == "nuevo prompt"


def test_edit_active_rename_to_existing_name_drops_the_other(qapp, monkeypatch):
    agents = [Agent(name="A"), Agent(name="B"), Agent(name="C")]
    ctrl = _controller(qapp, agents)
    ctrl.set_active("B")

    # Renombra "B" a "C", que ya existe.
    edited = Agent(name="C", system_prompt="soy el nuevo C")
    monkeypatch.setattr(
        "ui.controllers.agent_controller.edit_agent",
        lambda *a, **k: edited,
    )
    ctrl.edit_active()

    names = [a.name for a in ctrl.agents]
    assert names == ["A", "C"]
    assert ctrl.active_agent().system_prompt == "soy el nuevo C"


def test_create_new_appends_and_activates(qapp, monkeypatch):
    agents = [Agent(name="A"), Agent(name="B")]
    ctrl = _controller(qapp, agents)
    ctrl.set_active("A")

    created = Agent(name="Nuevo agente", system_prompt="hola")
    monkeypatch.setattr(
        "ui.controllers.agent_controller.edit_agent",
        lambda *a, **k: created,
    )
    ctrl.create_new()

    assert [a.name for a in ctrl.agents] == ["A", "B", "Nuevo agente"]
    assert ctrl.active_name == "Nuevo agente"
    assert ctrl.active_agent().system_prompt == "hola"
    # getattr porque `saved` es un atributo test-only de _FakeStore, no
    # parte de la interfaz pública de AgentStore.
    assert getattr(ctrl.store, "saved", None) is not None


def test_create_new_cancelled_dialog_does_nothing(qapp, monkeypatch):
    agents = [Agent(name="A"), Agent(name="B")]
    ctrl = _controller(qapp, agents)
    ctrl.set_active("A")

    monkeypatch.setattr(
        "ui.controllers.agent_controller.edit_agent",
        lambda *a, **k: None,
    )
    ctrl.create_new()

    assert [a.name for a in ctrl.agents] == ["A", "B"]
    assert ctrl.active_name == "A"


def test_create_new_rejects_duplicate_name(qapp, monkeypatch):
    agents = [Agent(name="A"), Agent(name="B")]
    ctrl = _controller(qapp, agents)
    ctrl.set_active("A")

    # El usuario escribe un nombre que ya existe.
    duplicate = Agent(name="B", system_prompt="suplantador")
    monkeypatch.setattr(
        "ui.controllers.agent_controller.edit_agent",
        lambda *a, **k: duplicate,
    )
    warnings: list[tuple] = []
    monkeypatch.setattr(
        "ui.controllers.agent_controller.warn",
        lambda *a, **k: warnings.append(a),
    )
    ctrl.create_new()

    # No se añade ni se activa: sigue habiendo solo A y B, sin cambios.
    assert [a.name for a in ctrl.agents] == ["A", "B"]
    assert ctrl.active_name == "A"
    assert warnings  # se avisó al usuario del nombre duplicado
