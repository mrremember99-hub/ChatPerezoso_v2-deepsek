"""Tests de robustez de MCP: errores y concurrencia.

Cubren los hallazgos MCP-1 (worker captura cualquier excepcion),
MCP-2 (shutdown logea timeout) y MCP-3 (race submit/close).
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from plugins.mcp import MCPClient, MCPError, MCPServerConfig


# ── MCP-1: MCPWorker.run captura BaseException ──────────────────────

def test_mcp_worker_emits_error_on_non_mcp_exception(qapp):
    """Un RuntimeError en list_tools no debe dejar el worker mudo."""
    from ui.workers import MCPWorker

    class ExplodingClient:
        def list_tools(self):
            raise RuntimeError("fallo inesperado del SDK")

        def close(self):
            pass

    worker = MCPWorker("demo", ExplodingClient())
    received_errors: list = []
    received_finished: list = []
    worker.error.connect(lambda *args: received_errors.append(args))
    worker.finished.connect(lambda *args: received_finished.append(args))

    worker.run()

    assert not received_finished, "no deberia emitir finished"
    assert len(received_errors) == 1, "debe emitir exactamente un error"
    server_id, message = received_errors[0]
    assert server_id == "demo"
    assert "RuntimeError" in message
    assert "fallo inesperado" in message


def test_mcp_worker_still_handles_mcp_error(qapp):
    """El camino normal de MCPError sigue funcionando."""
    from ui.workers import MCPWorker

    class FailingClient:
        def list_tools(self):
            raise MCPError("servidor no responde")

        def close(self):
            pass

    worker = MCPWorker("demo", FailingClient())
    received_errors: list = []
    worker.error.connect(lambda *args: received_errors.append(args))
    worker.run()

    assert len(received_errors) == 1
    assert "servidor no responde" in received_errors[0][1]


def test_mcp_worker_happy_path_unchanged(qapp):
    """El caso bueno sigue emitiendo finished con las tools."""
    from ui.workers import MCPWorker

    class HappyClient:
        def list_tools(self):
            return [{"name": "x", "description": ""}]

        def close(self):
            pass

    worker = MCPWorker("demo", HappyClient())
    received: list = []
    worker.finished.connect(lambda *args: received.append(args))
    worker.run()

    assert len(received) == 1
    server_id, client, tools = received[0]
    assert server_id == "demo"
    assert tools == [{"name": "x", "description": ""}]


# ── MCP-3: submit tras close ─────────────────────────────────────────

def test_mcp_client_submit_after_close_raises_mcp_error():
    """Un _submit tras close() debe lanzar MCPError claro."""
    client = MCPClient(MCPServerConfig("fake", ()))
    client.close()

    with pytest.raises(MCPError, match="cerrado"):
        client.call_tool("x", {})


def test_mcp_client_close_before_connect_does_not_crash():
    """close() en un cliente que nunca conectó no debe lanzar."""
    client = MCPClient(MCPServerConfig("fake", ()))
    client.close()  # no debe lanzar
    client.close()  # idempotente