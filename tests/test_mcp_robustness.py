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

# ── X1.5b: MCPWorker cierra el cliente al error ─────────────────────
# Auditoria externa 2026-09-29, P2#3 (ALTO).
#
# Antes, el worker fallaba y emitia error, pero no llamaba a
# client.close(). El hilo del loop asyncio + el subprocess MCP
# sobrevivian al QThread. Reintentos acumulaban procesos.

def test_mcp_worker_cierra_cliente_en_mcp_error(qapp):
    from ui.workers import MCPWorker

    closed = []

    class FailingClient:
        def list_tools(self):
            raise MCPError("boom")
        def close(self):
            closed.append(True)

    worker = MCPWorker("demo", FailingClient())
    worker.error.connect(lambda *a: None)
    worker.run()

    assert closed == [True], "close() debe llamarse al fallar"


def test_mcp_worker_cierra_cliente_en_excepcion_generica(qapp):
    from ui.workers import MCPWorker

    closed = []

    class ExplodingClient:
        def list_tools(self):
            raise RuntimeError("boom")
        def close(self):
            closed.append(True)

    worker = MCPWorker("demo", ExplodingClient())
    worker.error.connect(lambda *a: None)
    worker.run()

    assert closed == [True]


def test_mcp_worker_no_cierra_en_happy_path(qapp):
    """En exito NO cerramos: el cliente sigue vivo para el bridge."""
    from ui.workers import MCPWorker

    closed = []

    class HappyClient:
        def list_tools(self):
            return []
        def close(self):
            closed.append(True)

    worker = MCPWorker("demo", HappyClient())
    worker.finished.connect(lambda *a: None)
    worker.run()

    assert closed == [], "no cerrar en exito"


def test_mcp_worker_close_que_peta_no_tapa_error_original(qapp):
    """Si close() lanza, el error original sigue llegando."""
    from ui.workers import MCPWorker

    class BrokenCloseClient:
        def list_tools(self):
            raise MCPError("error real")
        def close(self):
            raise RuntimeError("close tambien peta")

    worker = MCPWorker("demo", BrokenCloseClient())
    received = []
    worker.error.connect(lambda *a: received.append(a))
    worker.run()

    assert len(received) == 1
    assert "error real" in received[0][1]


# ── X1.5a: _ensure_connected cierra al timeout ──────────────────────
# Auditoria externa 2026-09-29, P2#3 (ALTO).
#
# Si _ready.wait expira, antes solo levantabamos MCPError. El hilo
# del loop seguia con _async_connect() pendiente y el subprocess
# podia quedar huerfano.

def test_ensure_connected_timeout_cierra_cliente(monkeypatch):
    client = MCPClient(MCPServerConfig("fake", ()))
    # Simulamos que ya hay un thread arrancado (si no, el metodo
    # intentaria lanzar realmente "fake" como subprocess).
    client._loop_thread = object()

    # Forzamos timeout sin esperar 30s.
    monkeypatch.setattr(
        client._ready, "wait", lambda timeout=None: False
    )

    # Capturamos close().
    closed = []
    monkeypatch.setattr(client, "close", lambda: closed.append(True))

    with pytest.raises(MCPError, match="Tiempo agotado"):
        client._ensure_connected()

    assert closed == [True], "close() debe llamarse al timeout"
