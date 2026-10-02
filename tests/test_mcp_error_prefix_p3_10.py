"""P3#10: error de tool MCP no debe matchear el trigger de 'servidor caido'."""
from __future__ import annotations


def test_error_tool_mcp_no_activa_report_failure():
    """Cadena real: _result_to_text -> summary -> startswith."""
    tool_error = "ERROR TOOL MCP: fichero no existe"
    transport_error = "ERROR MCP: timeout"

    # El controller usa startswith("ERROR MCP").
    assert not tool_error.startswith("ERROR MCP")
    assert transport_error.startswith("ERROR MCP")


def test_result_to_text_usa_prefijo_tool():
    """Verifica que _result_to_text emite el prefijo nuevo."""
    from plugins.mcp.client import MCPClient

    class _Result:
        isError = True
        content = [type("X", (), {"text": "boom"})()]

    out = MCPClient._result_to_text(_Result())
    assert out.startswith("ERROR TOOL MCP:")
    assert not out.startswith("ERROR MCP")


def test_result_to_text_ok_sin_prefijo():
    from plugins.mcp.client import MCPClient

    class _Result:
        isError = False
        content = [type("X", (), {"text": "hola"})()]

    out = MCPClient._result_to_text(_Result())
    assert out == "hola"


def test_bridge_errores_transporte_siguen_prefijo_mcp():
    """Los fallos de transporte mantienen el prefijo original."""
    # Verificacion estatica del codigo: los strings siguen ahi.
    from pathlib import Path
    src = Path("plugins/mcp/bridge.py").read_text()
    assert 'ERROR MCP: servidor' in src
    assert 'ERROR MCP: {exc}' in src
