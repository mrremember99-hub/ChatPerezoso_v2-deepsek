"""F2 (2026-09-27): detener el bucle tras N rondas con errores de tool.

Antes, un modelo que iteraba a ciegas sobre un tool que siempre
fallaba (p.ej. leer_archivo con start_line fuera de rango) consumia
las 15 rondas y moria con "Se alcanzo el limite de rondas de
herramientas" sin feedback util al usuario.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from core.ollama import (
    OllamaClient,
    _ChatContext,
    _LoopState,
    _RoundExecution,
    _MAX_CONSECUTIVE_TOOL_ERRORS,
    _TOOL_ERRORS_LOOP_MSG,
)


def _make_context() -> _ChatContext:
    return _ChatContext(
        model="m",
        options=None,
        on_text=lambda _t: None,
        on_tool=lambda _n, _a: "",
        on_metrics=None,
        cancel_event=None,
        context_window=None,
        strategy=MagicMock(),
        history=[],
        authorization_text="lee gui.py",
        last_assistant="",
        gate=MagicMock(),
        tool_names={"leer_archivo"},
        send_tools=None,
        buffer_only=False,
    )


def _exec_failure() -> _RoundExecution:
    return _RoundExecution(
        round_signature=None,
        had_block=False,
        had_execution=True,
        had_write=False,
        had_failure=True,
    )


def _exec_ok() -> _RoundExecution:
    return _RoundExecution(
        round_signature=None,
        had_block=False,
        had_execution=True,
        had_write=False,
        had_failure=False,
    )


def test_three_consecutive_failures_abort():
    """3 rondas seguidas con fallo → mensaje de stop."""
    ctx = _make_context()
    state = _LoopState()
    client = OllamaClient.__new__(OllamaClient)

    for i in range(_MAX_CONSECUTIVE_TOOL_ERRORS - 1):
        result = client._evaluate_round(ctx, _exec_failure(), state)
        assert result is None, f"no deberia abortar en ronda {i+1}"

    result = client._evaluate_round(ctx, _exec_failure(), state)
    assert result == _TOOL_ERRORS_LOOP_MSG
    assert state.consecutive_error_rounds == _MAX_CONSECUTIVE_TOOL_ERRORS


def test_success_resets_counter():
    """Un exito intermedio resetea el contador."""
    ctx = _make_context()
    state = _LoopState()
    client = OllamaClient.__new__(OllamaClient)

    client._evaluate_round(ctx, _exec_failure(), state)
    client._evaluate_round(ctx, _exec_failure(), state)
    assert state.consecutive_error_rounds == 2

    client._evaluate_round(ctx, _exec_ok(), state)
    assert state.consecutive_error_rounds == 0

    client._evaluate_round(ctx, _exec_failure(), state)
    client._evaluate_round(ctx, _exec_failure(), state)
    assert state.consecutive_error_rounds == 2


def test_first_success_leaves_counter_zero():
    ctx = _make_context()
    state = _LoopState()
    client = OllamaClient.__new__(OllamaClient)
    client._evaluate_round(ctx, _exec_ok(), state)
    assert state.consecutive_error_rounds == 0
