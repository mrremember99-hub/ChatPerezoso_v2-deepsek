"""Tests para ModelController (M4, 2026-09-27).

Antes no tenia cobertura (0%) — era el unico controller de
ui/controllers/ con threading real sin tests.

Estrategia: sustituir QThread y ModelWorker por stubs que
registran llamadas, pero exponen senales Qt reales para poder
verificar la propagacion sin arrancar un event loop.
"""
from __future__ import annotations

import time

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal

from ui.controllers.model_controller import ModelController


class _StubThread(QObject):
    """QThread falso. No arranca event loop; solo registra llamadas."""
    started = Signal()
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._running = False
        self.start_called = False
        self.quit_called = False
        self.deleted = False

    def start(self) -> None:
        self.start_called = True
        self._running = True

    def isRunning(self) -> bool:
        return self._running

    def quit(self) -> None:
        self.quit_called = True
        self._running = False
        # No emitir finished: en Qt real, finished se emite cuando
        # el event loop del thread termina, no cuando se llama quit().

    def wait(self, ms: int) -> bool:
        return True

    def deleteLater(self) -> None:
        self.deleted = True


class _StubWorker(QObject):
    """ModelWorker falso. No hace I/O; expone senales reales."""
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, client):
        super().__init__()
        self.client = client
        self.moved_to = None
        self.deleted = False

    def moveToThread(self, thread) -> None:
        self.moved_to = thread

    def run(self) -> None:
        pass  # no-op: el test emite las senales manualmente

    def deleteLater(self) -> None:
        self.deleted = True


@pytest.fixture
def controller(qapp, monkeypatch):
    from ui.controllers import model_controller as mc_mod

    monkeypatch.setattr(mc_mod, "QThread", _StubThread)
    monkeypatch.setattr(mc_mod, "ModelWorker", _StubWorker)

    parent = QObject()
    ctrl = ModelController(parent, client=object())
    ctrl._owner = parent  # mantener vivo el QObject padre
    return ctrl


# -- load --------------------------------------------------------------

def test_load_starts_thread_and_emits_loading(controller):
    loading_events: list = []
    controller.loading.connect(lambda: loading_events.append(1))

    controller.load()

    assert isinstance(controller._thread, _StubThread)
    assert controller._thread.start_called
    assert isinstance(controller._worker, _StubWorker)
    assert controller._worker.moved_to is controller._thread
    assert loading_events == [1]


def test_load_is_idempotent_while_thread_present(controller):
    controller.load()
    first_thread = controller._thread
    first_worker = controller._worker

    controller.load()

    assert controller._thread is first_thread
    assert controller._worker is first_worker


# -- cleanup -----------------------------------------------------------

def test_cleanup_resets_references(controller):
    controller.load()
    thread = controller._thread
    worker = controller._worker
    assert thread is not None
    assert worker is not None

    controller._cleanup()

    assert controller._thread is None
    assert controller._worker is None
    assert thread.deleted
    assert worker.deleted


def test_cleanup_without_thread_is_noop(controller):
    controller._cleanup()  # no debe petar
    assert controller._thread is None
    assert controller._worker is None


# -- shutdown ----------------------------------------------------------

def test_shutdown_without_thread_returns_true(controller):
    assert controller.shutdown() is True


def test_shutdown_with_thread_not_running_returns_true(controller):
    controller.load()
    controller._thread._running = False
    assert controller.shutdown() is True


def test_shutdown_with_expired_deadline_returns_false(controller):
    controller.load()
    past = time.monotonic() - 10.0
    assert controller.shutdown(deadline=past) is False


def test_shutdown_quits_thread(controller):
    controller.load()
    thread = controller._thread
    controller.shutdown()
    assert thread.quit_called


# -- propagacion de senales -------------------------------------------

def test_worker_finished_propagates_to_loaded(controller):
    controller.load()
    received: list = []
    controller.loaded.connect(lambda models: received.append(models))

    controller._worker.finished.emit(["m1", "m2"])

    assert received == [["m1", "m2"]]


def test_worker_error_propagates_to_error(controller):
    controller.load()
    received: list = []
    controller.error.connect(lambda msg: received.append(msg))

    controller._worker.error.emit("boom")

    assert received == ["boom"]


def test_worker_finished_triggers_thread_quit(controller):
    controller.load()
    thread = controller._thread

    controller._worker.finished.emit(["m"])

    assert thread.quit_called


def test_worker_error_triggers_thread_quit(controller):
    controller.load()
    thread = controller._thread

    controller._worker.error.emit("boom")

    assert thread.quit_called
