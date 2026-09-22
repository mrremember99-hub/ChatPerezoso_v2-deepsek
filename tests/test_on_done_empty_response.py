"""Regresión CC-2: _on_done no añade assistant vacío al historial.

Antes, un modelo que devolvía respuesta vacía dejaba
{"role": "assistant", "content": ""} en el historial. El prompt del
siguiente turno podía quedar confundido por ese mensaje fantasma.
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
        self.narrations: list[tuple[str, bool]] = []
        self.remove_calls = 0

    @property
    def response_text(self):
        return self._response_text

    def reset(self):
        self._response_text = ""
        self.response_start = None

    def reset_response_segment(self):
        self.response_start = None

    def insert_user_message(self, text):
        self.user_messages.append(text)

    def on_text(self, text):
        self._response_text += text

    def insert_narration(self, text, active=False):
        self.narrations.append((text, active))

    def insert_tool_card(self, *a, **k): pass
    def insert_error(self, *a, **k): pass
    def restore_assistant_message(self, *a, **k): pass
    def final_text(self, fallback): return self._response_text or fallback

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


def test_on_done_with_empty_response_does_not_add_message(ctrl):
    """Sin texto, no debe quedar assistant vacío en el historial."""
    c, renderer = ctrl
    c.send("hola", "modelo")
    c._on_done("")  # respuesta vacía

    # Solo debe quedar el user. El assistant vacío no se añade.
    roles = [m["role"] for m in c.messages]
    assert roles == ["user"], f"roles inesperados: {roles}"


def test_on_done_with_empty_response_shows_narration(ctrl):
    """Cuando el modelo no genera nada, se avisa al usuario."""
    c, renderer = ctrl
    c.send("hola", "modelo")
    c._on_done("")

    textos = [t for t, _ in renderer.narrations]
    assert any("no genero respuesta" in t.lower() for t in textos), (
        f"falta la narración de aviso; narraciones: {textos}"
    )


def test_on_done_with_content_still_adds_message(ctrl):
    """El caso normal sigue funcionando."""
    c, renderer = ctrl
    c.send("hola", "modelo")
    c._on_done("respuesta real")

    roles = [m["role"] for m in c.messages]
    assert roles == ["user", "assistant"]
    assistant = [m for m in c.messages if m["role"] == "assistant"][0]
    assert assistant["content"] == "respuesta real"


def test_on_done_with_whitespace_only_does_not_add(ctrl):
    """Un assistant con solo espacios/tabs tampoco se añade."""
    c, renderer = ctrl
    c.send("hola", "modelo")
    c._on_done("   \n\t  ")

    roles = [m["role"] for m in c.messages]
    assert roles == ["user"]
    