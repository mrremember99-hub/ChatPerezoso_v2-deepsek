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
    

# -- P2: reset historial entre fases ------------------------------------

def test_reset_phase_history_limpia_mensajes(qapp):
    """El helper limpia self.messages y self._current_actions."""
    from core.prompt_phases import DetectedPhases
    ctrl = _make_controller(qapp)
    ctrl.messages = [
        {"role": "user", "content": "fase 1"},
        {"role": "assistant", "content": "ok"},
        {"role": "user", "content": "fase 2"},
    ]
    ctrl._current_actions = [object()]
    ctrl._reset_phase_history()
    assert ctrl.messages == []
    assert ctrl._current_actions == []


def test_advance_queue_resetea_historial_con_fases(qapp, monkeypatch):
    """Al avanzar la cola con _phase_plan, el historial previo se limpia."""
    from core.prompt_phases import DetectedPhases
    ctrl = _make_controller(qapp)
    # Simular estado intermedio: fase 1 termino, quedan mensajes.
    ctrl.messages = [
        {"role": "user", "content": "fase 1"},
        {"role": "assistant", "content": "respuesta"},
    ]
    ctrl._current_actions = [object()]
    # Cola con una fase pendiente.
    ctrl._phase_plan = DetectedPhases(
        preamble="reglas",
        phases=["FASE 2\ncuerpo de la fase 2"],
    )
    ctrl._phase_bodies = ["FASE 2\ncuerpo de la fase 2"]
    ctrl._queue = ["prompt-fase-2"]
    ctrl._queue_total = 2
    ctrl._queue_active = True
    ctrl._last_model = "test-model"
    ctrl._last_options = {}
    ctrl._last_system_prompt = ""
    ctrl.set_workspace_provider(lambda: None)

    sent = {"messages_antes": None}
    def fake_send(*a, **k):
        # Capturar el estado de messages en el momento del send.
        sent["messages_antes"] = list(ctrl.messages)
    monkeypatch.setattr(ctrl, "send", fake_send)

    ctrl._advance_queue()

    # Antes del send, el historial debe estar limpio.
    assert sent["messages_antes"] == [], (
        f"Historial no reseteado: {sent['messages_antes']}"
    )


def test_advance_queue_no_resetea_sin_fases(qapp, monkeypatch):
    """Sin _phase_plan, la cola no toca el historial (compatibilidad)."""
    ctrl = _make_controller(qapp)
    ctrl.messages = [
        {"role": "user", "content": "algo previo"},
    ]
    ctrl._phase_plan = None
    ctrl._queue = ["prompt-suelto"]
    ctrl._queue_total = 1
    ctrl._queue_active = True
    ctrl._last_model = "test-model"
    ctrl._last_options = {}
    ctrl._last_system_prompt = ""

    sent = {"messages_antes": None}
    def fake_send(*a, **k):
        sent["messages_antes"] = list(ctrl.messages)
    monkeypatch.setattr(ctrl, "send", fake_send)

    ctrl._advance_queue()

    # Sin plan de fases, el historial se preserva.
    assert sent["messages_antes"] == [
        {"role": "user", "content": "algo previo"},
    ]
