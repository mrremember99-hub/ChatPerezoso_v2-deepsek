"""P3#11: _kill_orphan_mcp_processes identifica el workspace correcto."""
from __future__ import annotations

from pathlib import Path
from unittest.mock import patch


def test_usa_cwd_si_definido(tmp_path):
    """Si server.cwd existe, se usa como hint."""
    from plugins.mcp._base import MCPServerConfig
    from plugins.mcp.client import MCPClient

    cfg = MCPServerConfig(
        command="npx",
        args=("-y", "@modelcontextprotocol/server-filesystem", "/other/ws"),
        cwd=str(tmp_path),
    )
    client = MCPClient.__new__(MCPClient)
    client.server = cfg

    # Capturamos los proceso vistos.
    killed: list = []

    class _P:
        class info:
            name = "node"
            cmdline = ["node", "/path/mcp-server-filesystem", str(tmp_path)]
        def terminate(self):
            killed.append("term")
        def wait(self, timeout=None):
            pass
        def kill(self):
            killed.append("kill")

    with patch("psutil.process_iter", return_value=[_P()]):
        client._kill_orphan_mcp_processes()

    assert killed, "deberia haber matado el proceso que contiene cwd"


def test_no_mata_proceso_de_otro_workspace(tmp_path):
    from plugins.mcp._base import MCPServerConfig
    from plugins.mcp.client import MCPClient

    cfg = MCPServerConfig(
        command="npx",
        args=("-y", "@modelcontextprotocol/server-filesystem", "/other/ws"),
        cwd=str(tmp_path),
    )
    client = MCPClient.__new__(MCPClient)
    client.server = cfg

    killed: list = []

    class _P:
        class info:
            name = "node"
            cmdline = ["node", "/path/mcp-server-filesystem", "/other/ws"]
        def terminate(self):
            killed.append("term")
        def wait(self, timeout=None):
            pass
        def kill(self):
            killed.append("kill")

    with patch("psutil.process_iter", return_value=[_P()]):
        client._kill_orphan_mcp_processes()

    assert not killed, "no debe matar procesos de otro workspace"
