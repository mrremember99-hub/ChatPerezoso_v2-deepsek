"""Tests de integración de la orquestación determinista."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance() or QApplication([])
    return app


class _FakeRenderer:
    def __init__(self):
        self.messages = []
    def insert_user_message(self, t): self.messages.append(("user", t))
    def reset(self): pass
    def on_text(self, t): pass
    def final_text(self, t): return t
    def insert_error(self, e): pass
    def insert_narration(self, n, active=False): pass
    def insert_tool_card(self, r): pass
    def reset_response_segment(self): pass
    def finalize(self): pass


class _FakeTools:
    def definitions(self): return []
    def requires_confirmation(self, name): return False
    def call(self, *a, **k): return ""


def _make_controller(qapp):
    from ui.controllers.chat_controller import ChatController
    return ChatController(
        parent=None,
        parent_widget=None,
        client=object(),
        tools=_FakeTools(),
        renderer=_FakeRenderer(),
    )


def test_sin_fases_delega_a_send(qapp, monkeypatch):
    """Un prompt sin FASE N debe llamar a send(), no a enqueue()."""
    ctrl = _make_controller(qapp)
    called = {"send": 0}
    def fake_send(*a, **k):
        called["send"] += 1
    monkeypatch.setattr(ctrl, "send", fake_send)
    assert ctrl.send_user_input("hola mundo", "test") is True
    assert called["send"] == 1
    assert ctrl._phase_plan is None


def test_con_fases_activa_plan(qapp, monkeypatch):
    """Un prompt con FASE 1+2 debe activar el plan de orquestación."""
    ctrl = _make_controller(qapp)
    called = {"send": 0}
    def fake_send(*a, **k):
        called["send"] += 1
    monkeypatch.setattr(ctrl, "send", fake_send)

    texto = (
        "REGLAS GLOBALES.\n\n"
        "FASE 1\nPrimera fase.\n\n"
        "FASE 2\nSegunda fase."
    )
    assert ctrl.send_user_input(texto, "test") is True
    assert ctrl._phase_plan is not None
    assert ctrl._phase_plan.count == 2
    assert len(ctrl._phase_bodies) == 2


def test_workspace_provider_se_usa_en_snapshot(qapp):
    ctrl = _make_controller(qapp)
    ctrl.set_workspace_provider(lambda: None)
    assert ctrl._current_workspace_snapshot() == ""
    