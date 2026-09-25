"""Tests del tool trace persistente."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")

from core.tool_result import ToolResult


def _make_ctrl():
    from ui.controllers.chat_controller import ChatController
    ctrl = ChatController.__new__(ChatController)
    ctrl._current_actions = []
    return ctrl


# -- _build_tool_trace ------------------------------------------------------


def test_trace_vacio_sin_acciones():
    ctrl = _make_ctrl()
    assert ctrl._build_tool_trace() == ""


def test_trace_con_accion_ok():
    ctrl = _make_ctrl()
    ctrl._current_actions = [
        ToolResult(
            tool_name="escribir_archivo",
            summary="ok",
            metadata={"arguments": {"path": "gui.py"}},
        ),
    ]
    t = ctrl._build_tool_trace()
    assert "escribir_archivo(gui.py) -> ok" in t
    assert "YA se ejecutaron" in t


def test_trace_con_error():
    ctrl = _make_ctrl()
    ctrl._current_actions = [
        ToolResult(
            tool_name="leer_archivo",
            is_error=True,
            summary="no existe",
            metadata={"arguments": {"path": "x.py"}},
        ),
    ]
    assert "leer_archivo(x.py) -> error" in ctrl._build_tool_trace()


def test_trace_comando():
    ctrl = _make_ctrl()
    ctrl._current_actions = [
        ToolResult(
            tool_name="ejecutar_comando",
            metadata={"arguments": {"command": "python -m py_compile gui.py"}},
        ),
    ]
    t = ctrl._build_tool_trace()
    assert "python -m py_compile gui.py" in t


def test_trace_cap_items():
    ctrl = _make_ctrl()
    ctrl._current_actions = [
        ToolResult(
            tool_name=f"t{i}",
            metadata={"arguments": {"path": f"f{i}.py"}},
        )
        for i in range(25)
    ]
    t = ctrl._build_tool_trace()
    assert t.count("-> ok") == 15
    assert "omitidas" in t


def test_trace_sin_arguments_no_revienta():
    ctrl = _make_ctrl()
    ctrl._current_actions = [ToolResult(tool_name="noop")]
    assert "noop -> ok" in ctrl._build_tool_trace()


# -- send() inyecta la traza ------------------------------------------------


def test_send_inyecta_traza_en_system_prompt():
    ctrl = _make_ctrl()
    ctrl._current_actions = [
        ToolResult(
            tool_name="leer_archivo",
            metadata={"arguments": {"path": "a.py"}},
        ),
    ]
    ctrl._state = MagicMock(is_active=False)
    ctrl.renderer = MagicMock()
    ctrl._append_message = MagicMock()
    ctrl._last_model = ""
    ctrl._last_options = {}
    ctrl._last_system_prompt = "BASE"
    ctrl.status = MagicMock()
    ctrl._set_state = MagicMock()
    ctrl._spawn_worker = MagicMock()

    ctrl.send("hola", "m", None, "BASE")

    assert ctrl._spawn_worker.called
    prompt = ctrl._spawn_worker.call_args.args[2]
    assert "BASE" in prompt
    assert "leer_archivo(a.py)" in prompt
    # Base intacto y acciones limpias.
    assert ctrl._last_system_prompt == "BASE"
    assert ctrl._current_actions == []


def test_send_sin_acciones_no_anade_bloque():
    ctrl = _make_ctrl()
    ctrl._state = MagicMock(is_active=False)
    ctrl.renderer = MagicMock()
    ctrl._append_message = MagicMock()
    ctrl._last_model = ""
    ctrl._last_options = {}
    ctrl._last_system_prompt = "BASE"
    ctrl.status = MagicMock()
    ctrl._set_state = MagicMock()
    ctrl._spawn_worker = MagicMock()

    ctrl.send("hola", "m", None, "BASE")

    prompt = ctrl._spawn_worker.call_args.args[2]
    assert prompt == "BASE"
    assert "ACCIONES" not in prompt


# -- Output real en el trace (H3 de out(3/4)) ---------------------------

def test_trace_error_incluye_detalle():
    ctrl = _make_ctrl()
    ctrl._current_actions = [
        ToolResult(
            tool_name="ejecutar_comando",
            is_error=True,
            summary="SyntaxError",
            detail="SyntaxError: line 83, unexpected indent",
            metadata={"arguments": {"command": "python -m py_compile gui.py"}},
        ),
    ]
    t = ctrl._build_tool_trace()
    assert "python -m py_compile gui.py" in t
    assert "-> error" in t
    assert "SyntaxError: line 83" in t


def test_trace_error_se_trunca_si_largo():
    ctrl = _make_ctrl()
    ctrl._current_actions = [
        ToolResult(
            tool_name="ejecutar_comando",
            is_error=True,
            summary="Error",
            detail="x" * 2000,
            metadata={"arguments": {"command": "cmd"}},
        ),
    ]
    t = ctrl._build_tool_trace()
    assert "[... truncado]" in t
    assert len(t) < 1000


def test_trace_lectura_corta_incluye_contenido():
    ctrl = _make_ctrl()
    ctrl._current_actions = [
        ToolResult(
            tool_name="leer_archivo",
            summary="print(1)",
            detail="",
            metadata={"arguments": {"path": "a.py"}},
        ),
    ]
    t = ctrl._build_tool_trace()
    assert "leer_archivo(a.py) -> ok" in t
    assert "print(1)" in t


def test_trace_lectura_larga_omite_detalle():
    ctrl = _make_ctrl()
    ctrl._current_actions = [
        ToolResult(
            tool_name="leer_archivo",
            summary="import os",
            detail="x" * 5000,
            metadata={"arguments": {"path": "big.py"}},
        ),
    ]
    t = ctrl._build_tool_trace()
    assert "leer_archivo(big.py) -> ok" in t
    assert "x" * 500 not in t


def test_trace_exec_incluye_output():
    ctrl = _make_ctrl()
    ctrl._current_actions = [
        ToolResult(
            tool_name="ejecutar_comando",
            summary="Hola mundo",
            detail="",
            metadata={"arguments": {"command": "echo hola"}},
        ),
    ]
    t = ctrl._build_tool_trace()
    assert "echo hola" in t
    assert "Hola mundo" in t


def test_trace_write_ok_sin_detalle():
    """Regresion: una escritura OK no mete detail (el path ya esta)."""
    ctrl = _make_ctrl()
    ctrl._current_actions = [
        ToolResult(
            tool_name="escribir_archivo",
            summary="Archivo creado",
            detail="contenido del archivo",
            metadata={"arguments": {"path": "gui.py"}},
        ),
    ]
    t = ctrl._build_tool_trace()
    assert "escribir_archivo(gui.py) -> ok" in t
    assert "contenido del archivo" not in t


def test_trace_respeta_cap_total_chars():
    """Con muchos items largos, el bloque total se limita."""
    ctrl = _make_ctrl()
    ctrl._current_actions = [
        ToolResult(
            tool_name="ejecutar_comando",
            summary="X" * 500,
            metadata={"arguments": {"command": f"cmd{i}"}},
        )
        for i in range(15)
    ]
    t = ctrl._build_tool_trace()
    assert len(t) <= 3400
    assert t.count("-> ok") >= 5
