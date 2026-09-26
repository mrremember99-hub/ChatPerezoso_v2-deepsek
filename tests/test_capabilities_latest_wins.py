"""Tests de la política latest-wins en la consulta de capabilities (P3).

Verifican que solo un QThread de probe vive a la vez, que los cambios
rápidos se acumulan como un único pendiente, y que al terminar el
actual se lanza el pendiente.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")

import ui.controllers.app_controller as ac


def _patch_threads(monkeypatch):
    created_threads: list = []
    created_workers: list = []

    def make_thread(parent=None):
        t = MagicMock()
        t.isRunning.return_value = False
        created_threads.append(t)
        return t

    def make_worker(host, model, generation=0):
        w = MagicMock()
        w.host = host
        w.model = model
        w.generation = generation
        created_workers.append(w)
        return w

    monkeypatch.setattr(ac, "QThread", make_thread)
    monkeypatch.setattr(ac, "CapabilitiesWorker", make_worker)
    return created_threads, created_workers


def _make_ctrl():
    ctrl = ac.AppController.__new__(ac.AppController)
    ctrl._caps_thread = None
    ctrl._caps_worker = None
    ctrl._caps_generation = 0
    ctrl._caps_pending = None
    ctrl.config = MagicMock(ollama_host="http://localhost:11434")
    return ctrl


def test_sin_worker_arranca_directo(monkeypatch):
    threads, workers = _patch_threads(monkeypatch)
    ctrl = _make_ctrl()

    ctrl._refresh_capabilities("llama3.1")

    assert len(threads) == 1
    assert len(workers) == 1
    assert workers[0].model == "llama3.1"
    assert workers[0].generation == 1
    assert ctrl._caps_pending is None


def test_con_worker_corriendo_guarda_pendiente(monkeypatch):
    threads, workers = _patch_threads(monkeypatch)
    ctrl = _make_ctrl()

    ctrl._refresh_capabilities("m1")
    ctrl._caps_thread.isRunning.return_value = True
    ctrl._refresh_capabilities("m2")

    assert len(workers) == 1  # no se creó segundo worker
    assert ctrl._caps_pending == ("m2", 2)


def test_cinco_cambios_rapidos_dos_workers(monkeypatch):
    threads, workers = _patch_threads(monkeypatch)
    ctrl = _make_ctrl()

    ctrl._refresh_capabilities("m1")
    ctrl._caps_thread.isRunning.return_value = True
    for m in ["m2", "m3", "m4", "m5"]:
        ctrl._refresh_capabilities(m)

    assert len(workers) == 1
    assert ctrl._caps_pending == ("m5", 5)

    # Simular fin del thread actual
    current = ctrl._caps_thread
    ctrl.sender = lambda: current
    current.isRunning.return_value = False
    ctrl._on_caps_thread_finished()

    assert len(workers) == 2
    assert workers[-1].model == "m5"
    assert workers[-1].generation == 5
    assert ctrl._caps_pending is None


def test_al_terminar_sin_pendiente_limpia_estado(monkeypatch):
    threads, workers = _patch_threads(monkeypatch)
    ctrl = _make_ctrl()

    ctrl._refresh_capabilities("m1")
    current = ctrl._caps_thread
    ctrl.sender = lambda: current

    ctrl._on_caps_thread_finished()

    assert ctrl._caps_thread is None
    assert ctrl._caps_worker is None
    assert ctrl._caps_pending is None
    assert len(workers) == 1  # no se lanzó otro


def test_thread_viejo_se_libera_sin_lanzar_pendiente(monkeypatch):
    """Si termina un thread que ya no es el actual, solo se libera."""
    threads, workers = _patch_threads(monkeypatch)
    ctrl = _make_ctrl()

    ctrl._refresh_capabilities("m1")  # thread A, es el actual
    old_thread = ctrl._caps_thread

    # Forzar arranque de un nuevo worker sustituyendo el actual
    ctrl._caps_thread = MagicMock()
    ctrl._caps_thread.isRunning.return_value = True
    new_current = ctrl._caps_thread

    # Simular que termina el viejo
    ctrl.sender = lambda: old_thread
    ctrl._on_caps_thread_finished()

    old_thread.deleteLater.assert_called_once()
    assert ctrl._caps_thread is new_current  # no se tocó el actual
    assert len(workers) == 1  # no se lanzó nada nuevo
