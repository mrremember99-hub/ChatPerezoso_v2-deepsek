"""Regresión del escenario de cancelación en vuelo (informe M2, 2026-09-26).

Antes del hardening, ``ShellClient._terminate()`` llamaba a
``proc.communicate(timeout=2)``, que compite con el ``communicate()``
del hilo llamante por los mismos pipes del ``Popen`` — uso no
soportado oficialmente por ``subprocess``. Ahora usa ``proc.wait()``,
que no toca los pipes.

Este test lanza N iteraciones de un comando lento y lo cancela a los
~150 ms. No reproduce la carrera de forma fiable (era rara de por sí),
pero deja el escenario en CI por si una regresión futura lo empeora.
"""
from __future__ import annotations

import threading
import time

import pytest

from plugins.shell import ShellClient


@pytest.mark.parametrize("iteration", range(10))
def test_cancel_in_flight_terminates_cleanly(tmp_path, iteration):
    client = ShellClient(tmp_path)
    cancel = threading.Event()

    def trigger_cancel():
        time.sleep(0.15)
        cancel.set()

    t = threading.Thread(target=trigger_cancel, daemon=True)
    t.start()

    started = time.monotonic()
    result = client.execute("sleep 2", timeout=10, cancel_event=cancel)
    elapsed = time.monotonic() - started
    t.join(timeout=1.0)

    assert "CANCELADA" in result, result
    assert not result.startswith("ERROR"), result
    # Si se cancela a los 150 ms, no debería esperar los 2 s completos.
    # Margen amplio para CI lento.
    assert elapsed < 1.5, f"tardó {elapsed:.2f}s — el kill no funcionó"
