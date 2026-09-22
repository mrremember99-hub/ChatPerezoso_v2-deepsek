"""Regresión: regenerate tras cancelación no duplica el user.

Bug CC-3: `regenerate` tenía un guard `if last_user_idx < len - 1:`
que fallaba cuando el último mensaje era un user sin respuesta
(turno cancelado). En ese caso, no truncaba y `send()` volvía a
añadir el mismo user. El historial quedaba con el mensaje duplicado.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject

from ui.controllers.chat_controller import ChatController


class _FakeRenderer:
    def __init__(self):
        self._response_text = ""
        self.response_start = None
        self.user_messages: list[str] = []
        self.remove_calls = 0

    @property
    def response_text(self):
        return self._response_text

    def reset(self):
        self._response_text = ""
        self.response_start = None

    def reset_response_segment(self):
        pass

    def insert_user_message(self, text):
        self.user_messages.append(text)

    def on_text(self, text):
        self._response_text += text

    def insert_narration(self, *a, **k): pass
    def insert_tool_card(self, *a, **k): pass
    def insert_error(self, *a, **k): pass
    def restore_assistant_message(self, *a, **k): pass
    def final_text(self, fallback): return self.response_text or fallback

    def remove_from_last_user(self):
        self.remove_calls += 1
        self.user_messages.clear()


class _FakeTools:
    def definitions(self): return []
    def intent_rules(self): return {}
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
    c = ChatController(
        parent=owner,
        parent_widget=None,
        client=object(),
        tools=_FakeTools(),
        renderer=renderer,
        initial_messages=[],
    )
    c._owner = owner
    worker = _FakeWorker(c)
    thread = _FakeThread()

    def spawn_stub(*_a, **_k):
        c._worker = worker
        c._thread = thread

    monkeypatch.setattr(c, "_spawn_worker", spawn_stub)
    return c, renderer


def test_regenerate_after_cancel_does_not_duplicate_user(ctrl):
    """Regenerar tras cancelar NO debe dejar dos user iguales."""
    c, _ = ctrl
    c.send("hola", "modelo")
    c._on_cancelled()  # simula cancelación
    # Tras cancelar: solo queda un user en el historial.
    assert [m["role"] for m in c.messages] == ["user"]
    assert c.messages[0]["content"] == "hola"

    c.regenerate("modelo")
    # Tras regenerar: sigue habiendo solo UN user, con el mismo texto.
    users = [m for m in c.messages if m["role"] == "user"]
    assert len(users) == 1, (
        f"el user se duplico: {[m['content'] for m in users]}"
    )
    assert users[0]["content"] == "hola"


def test_regenerate_after_error_does_not_duplicate_user(ctrl):
    """El mismo bug aplica tras un error de turno."""
    c, _ = ctrl
    c.send("pregunta", "modelo")
    c._on_error("algo se rompió")
    assert [m["role"] for m in c.messages] == ["user"]

    c.regenerate("modelo")
    users = [m for m in c.messages if m["role"] == "user"]
    assert len(users) == 1
    assert users[0]["content"] == "pregunta"


def test_regenerate_after_done_truncates_assistant(ctrl):
    """El caso normal sigue funcionando: descarta el assistant previo."""
    c, _ = ctrl
    c.send("hola", "modelo")
    c._on_done("respuesta anterior")
    assert [m["role"] for m in c.messages] == ["user", "assistant"]

    c.regenerate("modelo")
    users = [m for m in c.messages if m["role"] == "user"]
    assert len(users) == 1
    assert users[0]["content"] == "hola"