"""F7-quater (2026-09-28): gate + autopilot coherentes.

Verifica que:
  - is_read_only() distingue reads de writes.
  - authorize_and_execute con auto_approve=True salta el gate
    para reads pero no para writes.
  - auto_approve=False mantiene el comportamiento previo (regresion).
"""
from __future__ import annotations

from core.intent import READ_ONLY_TOOLS, is_read_only
from core.tool_strategies import authorize_and_execute
from core.intent import ToolIntentGate


# -- is_read_only -----------------------------------------------------------

def test_is_read_only_true_for_reads():
    for name in (
        "leer_archivo",
        "listar_carpeta",
        "buscar_en_workspace",
        "git_status",
        "git_diff",
        "git_log",
        "git_show",
        "mcp__fs__read_file",
        "mcp__fs__list_directory",
        "mcp__fs__search_files",
    ):
        assert is_read_only(name), name


def test_is_read_only_false_for_writes():
    for name in (
        "escribir_archivo",
        "crear_archivo",
        "editar_archivo",
        "insertar_en_archivo",
        "crear_carpeta",
        "borrar_archivo",
        "ejecutar_comando",
        "mcp__fs__write_file",
        "mcp__fs__move_file",
        "mcp__fs__create_directory",
    ):
        assert not is_read_only(name), name


def test_is_read_only_false_for_unknown():
    assert not is_read_only("tool_que_no_existe")
    assert not is_read_only("")


def test_read_only_tools_is_frozenset():
    assert isinstance(READ_ONLY_TOOLS, frozenset)


# -- authorize_and_execute: bypass con auto_approve -------------------------

def _make_gate_without_rules():
    """Gate sin reglas: bloquea cualquier tool que no sea MCP."""
    return ToolIntentGate({})


def test_bypass_when_auto_approve_and_read_only():
    """Con auto_approve=True, un read-only salta el gate (sin rules)."""
    gate = _make_gate_without_rules()
    calls = []

    def on_tool(name, args):
        calls.append((name, args))
        return "RESULT_OK"

    result = authorize_and_execute(
        "leer_archivo", {"path": "x.py"},
        gate, "cualquier texto irrelevante",
        on_tool,
        auto_approve=True,
    )
    assert result == "RESULT_OK"
    assert calls == [("leer_archivo", {"path": "x.py"})]


def test_no_bypass_for_write_with_auto_approve():
    """Con auto_approve=True, un write NO salta el gate."""
    gate = _make_gate_without_rules()
    calls = []

    def on_tool(name, args):
        calls.append((name, args))
        return "RESULT_OK"

    result = authorize_and_execute(
        "escribir_archivo", {"path": "x.py", "content": "y"},
        gate, "cualquier texto irrelevante",
        on_tool,
        auto_approve=True,
    )
    assert result.startswith("OPERACIÓN NO AUTORIZADA")
    assert calls == []


def test_no_bypass_when_auto_approve_false():
    """Con auto_approve=False, read-only sigue pasando por el gate."""
    gate = _make_gate_without_rules()
    calls = []

    def on_tool(name, args):
        calls.append((name, args))
        return "RESULT_OK"

    result = authorize_and_execute(
        "leer_archivo", {"path": "x.py"},
        gate, "texto sin verbo de lectura",
        on_tool,
        auto_approve=False,
    )
    assert result.startswith("OPERACIÓN NO AUTORIZADA")
    assert calls == []


def test_read_only_mcp_bypasses_gate():
    """Los MCP read-only tambien saltan el gate con auto_approve."""
    gate = _make_gate_without_rules()
    calls = []

    def on_tool(name, args):
        calls.append((name, args))
        return "RESULT_OK"

    result = authorize_and_execute(
        "mcp__fs__read_file", {"path": "x.py"},
        gate, "texto sin verbo",
        on_tool,
        auto_approve=True,
    )
    assert result == "RESULT_OK"
    assert calls == [("mcp__fs__read_file", {"path": "x.py"})]
