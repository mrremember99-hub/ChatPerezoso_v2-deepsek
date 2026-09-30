"""S7-a: ChatController usa HarnessWorker (único camino)."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject

from ui.harness_worker import HarnessWorker


class _FakeSignal:
    def __init__(self):
        self._cbs = []

    def connect(self, cb):
        self._cbs.append(cb)

    def emit(self, *a):
        for cb in list(self._cbs):
            cb(*a)


class _FakeTools:
    def definitions(self):
        return []

    def call(self, name, arguments, **kwargs):
        return ""

    def requires_confirmation(self, name):
        return False

    def intent_rules(self):
        return {}


class _FakeWorker:
    def __init__(self, *a, **kw):
        pass

    def moveToThread(self, t):
        pass

    def run(self):
        pass

    def cancel(self):
        pass

    def __getattr__(self, name):
        s = _FakeSignal()
        object.__setattr__(self, name, s)
        return s


class _FakeThread:
    def __init__(self, *a, **kw):
        self.started = _FakeSignal()
        self.finished = _FakeSignal()

    def start(self):
        pass

    def quit(self):
        pass

    def wait(self, timeout=0):
        return True


def _make_ctrl():
    from ui.controllers.chat_controller import ChatController

    ctrl = ChatController(
        parent=QObject(),
        parent_widget=None,
        client=object(),
        tools=_FakeTools(),
        renderer=object(),
    )
    return ctrl


# ── tests ──────────────────────────────────────────────────────


def test_spawn_worker_usa_build_harness(monkeypatch):
    """_spawn_worker delega siempre en _build_harness_worker."""
    ctrl = _make_ctrl()

    llamadas = []

    def _spy_build(model, options, system_prompt):
        llamadas.append((model, options, system_prompt))
        return _FakeWorker()

    monkeypatch.setattr(ctrl, "_build_harness_worker", _spy_build)
    monkeypatch.setattr(
        "ui.controllers.chat_controller.QThread", _FakeThread,
    )

    ctrl._spawn_worker("m1", None, "sys")

    assert len(llamadas) == 1
    assert llamadas[0][0] == "m1"


def test_build_harness_worker_inyecta_handler():
    """_build_harness_worker construye worker con session lista."""
    ctrl = _make_ctrl()
    ctrl.messages = [{"role": "user", "content": "hola"}]
    ctrl._loop_detection_enabled = False

    worker = ctrl._build_harness_worker("m1", None, "sys")

    assert isinstance(worker, HarnessWorker)
    session = worker._session
    assert session.confirmation_handler == worker.handle_confirmation
    assert session.tool_registry is ctrl.tools
    # El historico va sin el ultimo user (que se pasa por step).
    assert session._messages == []
    assert worker._user_message == "hola"

