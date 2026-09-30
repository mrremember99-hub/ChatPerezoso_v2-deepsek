"""S6-b-2: flag harness_enabled en ChatController."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject

from ui.harness_worker import HarnessWorker
from ui.workers import ChatWorker


_SEÑALES = [
    "stream_ready", "tool", "tool_result",
    "confirmation_requested", "tool_auto_approved",
    "metrics_updated", "summary_ready",
    "loop_warning", "loop_corrective", "loop_aborted",
    "finished", "cancelled", "error",
]


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
        # Se�ales lazy: cualquier Signal pedida se crea al vuelo.
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


def test_flag_off_por_defecto():
    ctrl = _make_ctrl()
    assert ctrl._harness_enabled is False


def test_setter_harness_enabled():
    ctrl = _make_ctrl()
    ctrl.set_harness_enabled(True)
    assert ctrl._harness_enabled is True
    ctrl.set_harness_enabled(False)
    assert ctrl._harness_enabled is False


def test_spawn_worker_flag_on_usa_build_harness(monkeypatch):
    """Flag ON -> _spawn_worker delega en _build_harness_worker."""
    ctrl = _make_ctrl()
    ctrl._harness_enabled = True

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


def test_spawn_worker_flag_off_usa_chat_worker(monkeypatch):
    """Flag OFF -> _spawn_worker usa ChatWorker."""
    ctrl = _make_ctrl()
    ctrl._harness_enabled = False

    llamadas = []

    def _fake_chat_worker(*a, **kw):
        llamadas.append((a, kw))
        return _FakeWorker()

    monkeypatch.setattr(
        "ui.controllers.chat_controller.ChatWorker",
        _fake_chat_worker,
    )
    monkeypatch.setattr(
        "ui.controllers.chat_controller.QThread", _FakeThread,
    )

    ctrl._spawn_worker("m1", None, "sys")

    assert len(llamadas) == 1


def test_build_harness_worker_inyecta_handler():
    """_build_harness_worker construye worker con session lista."""
    ctrl = _make_ctrl()
    ctrl._harness_enabled = True
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


def test_señales_identicas():
    """Las 13 se�ales existen en ambos workers."""
    for name in _SEÑALES:
        assert hasattr(ChatWorker, name), f"ChatWorker sin {name}"
        assert hasattr(HarnessWorker, name), f"HarnessWorker sin {name}"
