from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject

from core.mcp_servers import MCPServerStore
from core.workspace import Workspace
from plugins.mcp import MCPToolBridge


class _FakeBridge(MCPToolBridge):
    """Bridge mínimo que registra las llamadas a activate/deactivate."""

    def __init__(self, workspace):
        from core.tools import ToolRegistry
        super().__init__(ToolRegistry(workspace))
        self.activated: list[str] = []
        self.deactivated: list[str] = []

    def activate(self, server_id, client, tools=None):
        self.activated.append(server_id)
        return super().activate(server_id, client, tools=tools or [])

    def deactivate(self, server_id=None):
        if server_id is None:
            self.deactivated.append("*")
        else:
            self.deactivated.append(server_id)
        return super().deactivate(server_id)


@pytest.fixture
def controller(tmp_path):
    """Crea un MCPController aislado del estado real del proyecto.

    Puntos clave:
      · MCPServerStore apunta a un archivo temporal que no existe, así
        no carga el mcp_servers.json real del usuario (que podría tener
        servidores enabled=True y arrancarían threads de npx durante
        los tests).
      · shutdown() garantiza que ningún QThread quede vivo al terminar,
        evitando el segfault de Qt al destruir objetos.
    """
    from ui.controllers.mcp_controller import MCPController

    owner = QObject()
    workspace = Workspace(tmp_path)
    bridge = _FakeBridge(workspace)

    # Store aislado: archivo que no existe → default_servers() sin enabled.
    isolated_store = MCPServerStore(path=tmp_path / "no_existe.json")

    ctrl = MCPController(
        parent=owner,
        parent_widget=None,
        bridge=bridge,
        workspace=workspace,
        store=isolated_store,
    )
    try:
        yield ctrl, bridge
    finally:
        try:
            ctrl.shutdown()
        except Exception:
            pass
        # No hace falta desconectar servers_changed: owner.deleteLater()
        # destruye el QObject y PySide6 limpia las conexiones asociadas.
        # Llamar a disconnect() cuando no hay nada conectado emite un
        # RuntimeWarning ruidoso sin aportar nada.
        owner.deleteLater()


def test_report_failure_without_config_is_noop(controller):
    ctrl, bridge = controller
    ctrl.report_failure("desconocido")
    assert ctrl.dead_ids == []


def test_report_failure_marks_dead_and_deactivates_bridge(controller):
    ctrl, bridge = controller
    from plugins.mcp import MCPServerConfig
    ctrl._configs["demo"] = MCPServerConfig("python3", ("fake.py",))

    changes: list[tuple] = []
    ctrl.servers_changed.connect(lambda *args: changes.append(args))

    ctrl.report_failure("demo")

    assert "demo" in ctrl.dead_ids
    assert "demo" in bridge.deactivated
    assert changes  # emitió señal


def test_report_failure_is_idempotent(controller):
    ctrl, bridge = controller
    from plugins.mcp import MCPServerConfig
    ctrl._configs["demo"] = MCPServerConfig("python3", ("fake.py",))

    ctrl.report_failure("demo")
    deactivated_before = len(bridge.deactivated)
    ctrl.report_failure("demo")
    assert len(bridge.deactivated) == deactivated_before


def test_report_failure_ignores_pending_server(controller):
    ctrl, _ = controller
    from plugins.mcp import MCPServerConfig
    ctrl._configs["demo"] = MCPServerConfig("python3", ("fake.py",))
    ctrl._threads["demo"] = object()  # type: ignore[assignment]

    ctrl.report_failure("demo")
    assert "demo" not in ctrl.dead_ids
    ctrl._threads.clear()


def test_deactivate_manual_clears_dead_and_config(controller):
    ctrl, _ = controller
    from plugins.mcp import MCPServerConfig
    ctrl._configs["demo"] = MCPServerConfig("python3", ("fake.py",))
    ctrl.report_failure("demo")
    assert "demo" in ctrl.dead_ids

    ctrl.deactivate("demo")
    assert "demo" not in ctrl.dead_ids
    assert "demo" not in ctrl._configs


def test_reconnect_without_config_is_noop(controller):
    ctrl, _ = controller
    ctrl.reconnect("desconocido")
    assert "desconocido" not in ctrl.dead_ids
    assert ctrl._threads == {}


def test_servers_changed_emits_four_lists(controller):
    ctrl, _ = controller
    captured: list[tuple] = []
    ctrl.servers_changed.connect(lambda *args: captured.append(args))

    from plugins.mcp import MCPServerConfig
    ctrl._configs["demo"] = MCPServerConfig("python3", ("fake.py",))
    ctrl.report_failure("demo")

    assert captured
    entries, active, pending, dead = captured[-1]
    assert isinstance(active, list)
    assert isinstance(pending, list)
    assert isinstance(dead, list)
    assert "demo" in dead
