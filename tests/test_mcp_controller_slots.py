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


# ── X2.2: shutdown conserva threads vivos ────────────────────────
# Auditoria externa 2026-09-29, P2#7.
#
# Antes _shutdown_connections limpiaba todos los dicts aunque
# quedaran threads vivos. Ahora solo se descartan los muertos.

def test_shutdown_conserva_threads_vivos():
    from ui.controllers.mcp_controller import MCPController

    ctrl = MCPController.__new__(MCPController)

    class FakeBridge:
        def deactivate(self):
            pass

    class FakeClient:
        def close(self):
            pass

    class FakeWorker:
        def __init__(self):
            self.client = FakeClient()

    class StubbornThread:
        def isRunning(self):
            return True
        def quit(self):
            pass  # no responde: sigue vivo
        def wait(self, ms):
            return False  # timeout

    class DeadThread:
        def isRunning(self):
            return False
        def quit(self):
            pass
        def wait(self, ms):
            return True

    ctrl.bridge = FakeBridge()
    ctrl._threads = {"vivo": StubbornThread(), "muerto": DeadThread()}
    ctrl._workers = {"vivo": FakeWorker(), "muerto": FakeWorker()}
    ctrl._configs = {"vivo": {}, "muerto": {}}
    ctrl._dead = {"vivo", "muerto"}

    ok = ctrl._shutdown_connections()
    assert ok is False, "stubborn thread -> fallo reportado"

    assert "vivo" in ctrl._threads
    assert "muerto" not in ctrl._threads
    assert "vivo" in ctrl._workers
    assert "muerto" not in ctrl._workers
    assert "vivo" in ctrl._configs
    assert "muerto" not in ctrl._configs
    assert "vivo" in ctrl._dead
    assert "muerto" not in ctrl._dead


def test_shutdown_limpio_libera_todo():
    from ui.controllers.mcp_controller import MCPController

    ctrl = MCPController.__new__(MCPController)

    class FakeBridge:
        def deactivate(self):
            pass

    class FakeWorker:
        def __init__(self):
            class C:
                def close(self):
                    pass
            self.client = C()

    class DeadThread:
        def isRunning(self):
            return False
        def quit(self):
            pass
        def wait(self, ms):
            return True

    ctrl.bridge = FakeBridge()
    ctrl._threads = {"a": DeadThread(), "b": DeadThread()}
    ctrl._workers = {"a": FakeWorker(), "b": FakeWorker()}
    ctrl._configs = {"a": {}, "b": {}}
    ctrl._dead = {"a", "b"}

    ok = ctrl._shutdown_connections()
    assert ok is True
    assert ctrl._threads == {}
    assert ctrl._workers == {}
    assert ctrl._configs == {}
    assert ctrl._dead == set()
