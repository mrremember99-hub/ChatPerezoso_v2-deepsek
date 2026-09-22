"""Tests de regresión para rebind de MCP al cambiar de workspace.

Cubren dos hallazgos:
- H-MCP-4: `rebind` no reconectaba servidores enabled.
- H-MCP-5: los args del servidor fs seguían apuntando al workspace
  anterior tras el cambio.

Diseño: subclase de MCPController que reemplaza `_connect` ANTES de
que __init__ corra. Así ni la auto-conexión inicial ni los rebinds
posteriores arrancan QThreads, MCPClient ni subprocess. Sin esto, la
suite crashea con SIGSEGV al destruir threads con asyncio loops vivos.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject

from core.mcp_servers import MCPServerStore
from plugins.mcp import MCPServerConfig
from core.tools import ToolRegistry
from core.workspace import Workspace
from plugins.mcp import MCPToolBridge


@pytest.fixture
def isolated_store(tmp_path):
    path = tmp_path / "mcp_servers.json"
    path.write_text(
        '{"servers": ['
        '{"id": "fs", "label": "Archivos", "command": "npx", '
        '"args": ["-y", "@modelcontextprotocol/server-filesystem", '
        '"/ruta/vieja/workspace"], "env": {}, "enabled": true},'
        '{"id": "custom", "label": "Otro", "command": "python3", '
        '"args": ["/tmp/mi_servidor.py", "--flag", "/ruta/fija"], '
        '"env": {}, "enabled": true}'
        ']}',
        encoding="utf-8",
    )
    return MCPServerStore(path=path)


def _make_controller(tmp_path, store):
    """Subclase de MCPController con _connect fake desde el principio.

    El fake se define en la subclase, así que el bucle de auto-conexión
    del __init__ ya lo usa. No se arranca ningún thread real.
    """
    from ui.controllers.mcp_controller import MCPController

    captured: list[tuple] = []

    class _Controller(MCPController):
        def _connect(self, server_id, command, *, args=None, env=None):
            captured.append((server_id, command, list(args or [])))
            self._configs[server_id] = MCPServerConfig(
                command=command, args=tuple(args or []), env=env or {},
            )

    workspace = Workspace(tmp_path / "ws1")
    workspace.root.mkdir(parents=True, exist_ok=True)

    bridge = MCPToolBridge(ToolRegistry(workspace))

    owner = QObject()
    ctrl = _Controller(
        parent=owner,
        parent_widget=None,
        bridge=bridge,
        workspace=workspace,
        store=store,
    )
    ctrl._owner = owner
    return ctrl, captured


def test_rebind_reconnects_enabled_servers(tmp_path, isolated_store):
    """H-MCP-4: tras rebind, los servidores enabled se reconectan."""
    ctrl, captured = _make_controller(tmp_path, isolated_store)
    captured.clear()

    new_workspace = Workspace(tmp_path / "ws2")
    new_workspace.root.mkdir(parents=True, exist_ok=True)
    ctrl.rebind(ctrl.bridge, new_workspace)

    server_ids = [sid for sid, _, _ in captured]
    assert "fs" in server_ids, "el servidor fs debe reconectarse"
    assert "custom" in server_ids, "el servidor custom debe reconectarse"


def test_rebind_replaces_fs_workspace_path(tmp_path, isolated_store):
    """H-MCP-5: el ultimo arg del servidor fs apunta al workspace nuevo."""
    ctrl, captured = _make_controller(tmp_path, isolated_store)
    captured.clear()

    new_workspace = Workspace(tmp_path / "ws2")
    new_workspace.root.mkdir(parents=True, exist_ok=True)
    ctrl.rebind(ctrl.bridge, new_workspace)

    fs_args = next(args for sid, _, args in captured if sid == "fs")
    assert fs_args[-1] == str(new_workspace.root), (
        f"el ultimo arg del fs debe ser {new_workspace.root!r}; "
        f"recibido {fs_args[-1]!r}"
    )
    assert "/ruta/vieja/workspace" not in fs_args


def test_rebind_keeps_non_fs_args_unchanged(tmp_path, isolated_store):
    """H-MCP-5: los servidores no-fs conservan sus args."""
    ctrl, captured = _make_controller(tmp_path, isolated_store)
    captured.clear()

    new_workspace = Workspace(tmp_path / "ws2")
    new_workspace.root.mkdir(parents=True, exist_ok=True)
    ctrl.rebind(ctrl.bridge, new_workspace)

    custom_args = next(args for sid, _, args in captured if sid == "custom")
    assert custom_args == ["/tmp/mi_servidor.py", "--flag", "/ruta/fija"]


def test_rebind_with_no_enabled_servers_is_noop(tmp_path):
    """H-MCP-4: sin servidores enabled, no se reconecta nada."""
    path = tmp_path / "mcp_servers.json"
    path.write_text('{"servers": []}', encoding="utf-8")
    store = MCPServerStore(path=path)

    ctrl, captured = _make_controller(tmp_path, store)
    captured.clear()

    new_workspace = Workspace(tmp_path / "ws2")
    new_workspace.root.mkdir(parents=True, exist_ok=True)
    ctrl.rebind(ctrl.bridge, new_workspace)

    assert captured == []