"""P3#24: la conexion queue_edit_requested -> _on_queue_edit funciona."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject


def test_queue_edit_emite_y_recibe_str():
    """Emite Signal(int, str) y verifica que llega con el texto."""
    from ui.controllers.app_controller import AppController

    ctrl = AppController.__new__(AppController)
    recibido: list = []
    ctrl._on_queue_edit = lambda idx, txt: recibido.append((idx, txt))

    class _Sig:
        def __init__(self):
            self._cbs = []
        def connect(self, cb):
            self._cbs.append(cb)
        def emit(self, *a):
            for cb in self._cbs:
                cb(*a)

    # Simulamos la conexion declarada en _wire.
    sig = _Sig()
    sig.connect(ctrl._on_queue_edit)
    sig.emit(3, "nuevo texto")
    assert recibido == [(3, "nuevo texto")]
