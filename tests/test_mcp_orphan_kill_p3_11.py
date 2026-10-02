"""P3#11: _kill_orphan_mcp_processes identifica el workspace correcto."""
from __future__ import annotations

from unittest.mock import patch


class _FakeProc:
    """Imita psutil.Process: .info es dict, .terminate/.kill/.wait."""

    def __init__(self, cmdline: list[str], name: str = "node") -> None:
        self.info = {"pid": 12345, "name": name, "cmdline": cmdline}
        self.killed: list[str] = []

    def terminate(self) -> None:
        self.killed.append("terminate")

    def wait(self, timeout=None) -> None:
        pass

    def kill(self) -> None:
        self.killed.append("kill")


def _make_client(tmp_path, args):
    from plugins.mcp._base import MCPServerConfig
    from plugins.mcp.client import MCPClient

    cfg = MCPServerConfig(command="npx", args=args, cwd=str(tmp_path))
    client = MCPClient.__new__(MCPClient)
    client.server = cfg
    return client


def test_usa_cwd_si_definido(tmp_path):
    """server.cwd dirige el filtro: mata procesos que contienen cwd."""
    client = _make_client(
        tmp_path,
        ("-y", "@modelcontextprotocol/server-filesystem", "/other/ws"),
    )
    proc = _FakeProc([
        "node",
        "/path/mcp-server-filesystem",
        str(tmp_path),
    ])

    with patch("psutil.process_iter", return_value=[proc]):
        client._kill_orphan_mcp_processes()

    assert proc.killed, "deberia haber matado el proceso que contiene cwd"


def test_no_mata_proceso_de_otro_workspace(tmp_path):
    client = _make_client(
        tmp_path,
        ("-y", "@modelcontextprotocol/server-filesystem", "/other/ws"),
    )
    proc = _FakeProc([
        "node",
        "/path/mcp-server-filesystem",
        "/otro/workspace",
    ])

    with patch("psutil.process_iter", return_value=[proc]):
        client._kill_orphan_mcp_processes()

    assert not proc.killed, "no debe matar procesos de otro workspace"


def test_sin_cwd_ni_args_validos_no_mata_nada():
    """Sin cwd ni ultimo arg de directorio existente, no mata."""
    from plugins.mcp._base import MCPServerConfig
    from plugins.mcp.client import MCPClient

    cfg = MCPServerConfig(
        command="npx",
        args=("-y", "@scope/pkg"),
        cwd=None,
    )
    client = MCPClient.__new__(MCPClient)
    client.server = cfg

    proc = _FakeProc(["node", "/x/mcp-server-foo", "/some/ws"])

    with patch("psutil.process_iter", return_value=[proc]):
        client._kill_orphan_mcp_processes()

    assert not proc.killed
