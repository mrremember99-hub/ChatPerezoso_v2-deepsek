from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject

from core.history import HistoryStore
from ui.controllers.chat_controller import ChatController


class _FakeRenderer:
    def __init__(self):
        self.response_text = ""
        self.response_start = None
        self.user_messages: list[str] = []
        self.remove_calls = 0

    def reset(self):
        self.response_text = ""
        self.response_start = None

    def reset_response_segment(self):
        self.response_start = None

    def insert_user_message(self, text):
        self.user_messages.append(text)

    def on_text(self, text):
        self.response_text += text

    def insert_tool_event(self, *args, **kwargs): pass
    def insert_tool_result(self, *args, **kwargs): pass
    def insert_narration(self, text, active=False): pass
    def insert_tool_card(self, result): pass
    def insert_error(self, *args, **kwargs): pass
    def restore_assistant_message(self, text): pass
    def final_text(self, fallback): return self.response_text or fallback

    def remove_from_last_user(self):
        self.remove_calls += 1
        self.user_messages.clear()


class _FakeTools:
    def definitions(self): return []
    def call(self, *a, **k): return ""
    def requires_confirmation(self, *a): return False


class _FakeWorker(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
    def cancel(self): pass


class _FakeSignal:
    def __init__(self): self._cbs = []
    def connect(self, cb): self._cbs.append(cb)
    def emit(self, *a):
        for cb in list(self._cbs):
            cb(*a)


class _FakeThread:
    def __init__(self):
        self.started = _FakeSignal()
        self.finished = _FakeSignal()
    def quit(self): pass
    def isRunning(self): return False
    def wait(self, *a): return True
    def deleteLater(self): pass


@pytest.fixture
def ctrl(qapp, monkeypatch, tmp_path):
    renderer = _FakeRenderer()
    owner = QObject()
    store = HistoryStore(tmp_path / "h.json")
    c = ChatController(
        parent=owner,
        parent_widget=None,
        client=object(),
        tools=_FakeTools(),
        renderer=renderer,
        store=store,
        initial_messages=[],
    )
    c._owner = owner
    worker = _FakeWorker(c)
    thread = _FakeThread()
    spawned: list = []

    def spawn(model, options=None, *_args, **_kwargs):
        c._worker = worker
        c._thread = thread
        spawned.append((model, options))

    monkeypatch.setattr(c, "_spawn_worker", spawn)
    return c, renderer, spawned


def test_regenerate_without_messages_is_noop(ctrl):
    c, renderer, spawned = ctrl
    c.regenerate("modelo")
    assert spawned == []
    assert renderer.remove_calls == 0


def test_regenerate_resends_last_user_message(ctrl):
    c, renderer, spawned = ctrl
    c.send("hola", "modelo")
    assert renderer.user_messages == ["hola"]

    # Simula una respuesta completada.
    c._on_done("respuesta")
    assert [m["role"] for m in c.messages] == ["user", "assistant"]

    # Regenera.
    c.regenerate("modelo")
    # Se removió visualmente y se volvió a insertar.
    assert renderer.remove_calls == 1
    assert renderer.user_messages == ["hola"]
    # Y se spawnearon dos workers: el original y el de regenerate.
    assert len(spawned) == 2


def test_regenerate_truncates_previous_assistant(ctrl):
    c, _, _ = ctrl
    c.send("hola", "modelo")
    c._on_done("respuesta")
    assert [m["role"] for m in c.messages] == ["user", "assistant"]

    c.regenerate("modelo")
    # Solo queda el mensaje del usuario (el asistente se ha truncado).
    assert [m["role"] for m in c.messages] == ["user"]


def test_regenerate_after_cancel_resends(ctrl):
    c, _, spawned = ctrl
    c.send("hola", "modelo")
    c._on_cancelled()
    # Sin respuesta: solo user en el historial.
    assert [m["role"] for m in c.messages] == ["user"]

    c.regenerate("modelo")
    assert len(spawned) == 2


def test_last_assistant_text_returns_last(ctrl):
    c, _, _ = ctrl
    assert c.last_assistant_text() == ""
    c.send("a", "modelo")
    c._on_done("primera")
    c.send("b", "modelo")
    c._on_done("segunda")
    assert c.last_assistant_text() == "segunda"


def test_options_passed_to_worker(ctrl):
    c, _, spawned = ctrl
    c.send("hola", "modelo", {"temperature": 0.1})
    assert spawned[-1][1] == {"temperature": 0.1}

    c._on_done("ok")
    # Regenerar reutiliza las opciones previas.
    c.regenerate("modelo")
    assert spawned[-1][1] == {"temperature": 0.1}
