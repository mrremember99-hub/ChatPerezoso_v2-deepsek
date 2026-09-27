"""Regresion: @Slot en MCPController (M6 parcial, 2026-09-27).

M1 del informe de mejoras (2026-09-26): los metodos _on_loaded y
_on_error se conectan a senales de MCPWorker, que corre en QThread.
Sin @Slot, la conexion encolada cross-thread no marshalea bien los
tipos (especialmente `client` como object).

D4 del mismo informe ya corrigio este patron en app_controller.py.
M1 lo extendio a mcp_controller.py en el commit 95407a2.

Test textual: PySide6 no expone un atributo inspeccionable fiable
para @Slot, asi que se inspecciona el codigo fuente. Mismo enfoque
que tests/test_chat_worker_signals.py.
"""
from __future__ import annotations

import inspect
import re

from ui.controllers import mcp_controller as mod


def _source() -> str:
    return inspect.getsource(mod)


def test_on_loaded_has_slot_decorator():
    """_on_loaded debe llevar @Slot para marshaling cross-thread."""
    src = _source()
    assert re.search(
        r"@Slot\([^)]*\)\s*\n\s*def _on_loaded\b",
        src,
    ), (
        "M1 regresion: _on_loaded no tiene @Slot. Es un slot "
        "cross-thread (MCPWorker en QThread); sin el decorador, "
        "PySide6 no marshalea bien los tipos, especialmente "
        "`client` como object."
    )


def test_on_error_has_slot_decorator():
    """_on_error debe llevar @Slot para marshaling cross-thread."""
    src = _source()
    assert re.search(
        r"@Slot\([^)]*\)\s*\n\s*def _on_error\b",
        src,
    ), (
        "M1 regresion: _on_error no tiene @Slot. Misma razon que "
        "_on_loaded: conexion encolada desde QThread."
    )


def test_slot_signal_types_correct():
    """_on_loaded acepta (str, object, list); _on_error (str, str).

    Coincide con las firmas de MCPWorker.finished = Signal(str,
    object, list) y MCPWorker.error = Signal(str, str).
    """
    src = _source()
    assert re.search(
        r"@Slot\(str,\s*object,\s*list\)\s*\n\s*def _on_loaded\b",
        src,
    ), "_on_loaded debe llevar @Slot(str, object, list)"
    assert re.search(
        r"@Slot\(str,\s*str\)\s*\n\s*def _on_error\b",
        src,
    ), "_on_error debe llevar @Slot(str, str)"
