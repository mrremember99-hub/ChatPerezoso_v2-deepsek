from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject

from core.workspace import Workspace
from plugins.mcp import MCPToolBridge


class _FakeBridge(MCPToolBridge):
    """Bridge mínimo que registra las llamadas a activate/deactivate."""

    def __init__(self, workspace):
        super().__init__(__import__("core.tools", fromlist=["ToolRegistry"]).ToolRegistry(workspace))
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


def _controller(tmp_path):
    from ui.controllers.mcp_controller import MCPController

    owner = QObject()
    workspace = Workspace(tmp_path)
    bridge = _FakeBridge(workspace)
    ctrl = MCPController(parent=owner, parent_widget=None, bridge=bridge, workspace=workspace)
    ctrl._owner = owner
    return ctrl, bridge


def test_report_failure_without_config_is_noop(tmp_path):
    ctrl, bridge = _controller(tmp_path)
    ctrl.report_failure("desconocido")
    assert ctrl.dead_ids == []


def test_report_failure_marks_dead_and_deactivates_bridge(tmp_path, monkeypatch):
    ctrl, bridge = _controller(tmp_path)
    # Simula un servidor ya conectado y con config registrada.
    from plugins.mcp import MCPServerConfig
    ctrl._configs["demo"] = MCPServerConfig("python3", ("fake.py",))

    changes: list[tuple] = []
    ctrl.servers_changed.connect(lambda *args: changes.append(args))

    ctrl.report_failure("demo")

    assert "demo" in ctrl.dead_ids
    assert "demo" in bridge.deactivated
    assert changes  # emitió señal


def test_report_failure_is_idempotent(tmp_path):
    ctrl, bridge = _controller(tmp_path)
    from plugins.mcp import MCPServerConfig
    ctrl._configs["demo"] = MCPServerConfig("python3", ("fake.py",))

    ctrl.report_failure("demo")
    deactivated_before = len(bridge.deactivated)
    ctrl.report_failure("demo")
    assert len(bridge.deactivated) == deactivated_before


def test_report_failure_ignores_pending_server(tmp_path):
    ctrl, _ = _controller(tmp_path)
    from plugins.mcp import MCPServerConfig
    ctrl._configs["demo"] = MCPServerConfig("python3", ("fake.py",))
    # Simula un thread pendiente.
    ctrl._threads["demo"] = object()  # type: ignore[assignment]

    ctrl.report_failure("demo")
    assert "demo" not in ctrl.dead_ids
    ctrl._threads.clear()


def test_deactivate_manual_clears_dead_and_config(tmp_path):
    ctrl, _ = _controller(tmp_path)
    from plugins.mcp import MCPServerConfig
    ctrl._configs["demo"] = MCPServerConfig("python3", ("fake.py",))
    ctrl.report_failure("demo")
    assert "demo" in ctrl.dead_ids

    ctrl.deactivate("demo")
    assert "demo" not in ctrl.dead_ids
    assert "demo" not in ctrl._configs


def test_reconnect_without_config_is_noop(tmp_path):
    ctrl, _ = _controller(tmp_path)
    ctrl.reconnect("desconocido")
    assert "desconocido" not in ctrl.dead_ids
    assert ctrl._threads == {}


def test_servers_changed_emits_three_lists(tmp_path):
    ctrl, _ = _controller(tmp_path)
    captured: list[tuple] = []
    ctrl.servers_changed.connect(lambda *args: captured.append(args))

    from plugins.mcp import MCPServerConfig
    ctrl._configs["demo"] = MCPServerConfig("python3", ("fake.py",))
    ctrl.report_failure("demo")

    assert captured
    active, pending, dead = captured[-1]
    assert isinstance(active, list)
    assert isinstance(pending, list)
    assert isinstance(dead, list)
    assert "demo" in dead
