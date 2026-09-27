"""Tests de wiring de DiagnosticsController (M5, 2026-09-27).

Cubre lo que NO estaba probado:
- Rama CANCELLING no cierra el cronometro.
- set_metrics: formateo de prompt/gen/tok-s.
- set_model: propagacion al panel.
- _on_conversation_changed -> refresh_context.
- _on_textual_tool_attempt -> stats + panel.
- reset() -> stats reset + refresh.
- Fallback a streaming_changed cuando no hay state_changed.
- Senal textual ausente no peta.

Lo cubierto por test_senior_fixes.py (ciclo con/sin texto) y
test_diagnostics.py (SessionStats dataclass) no se duplica.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject, Signal

from ui.chat_state import ChatState
from ui.controllers.diagnostics_controller import DiagnosticsController
from ui.views.diagnostics_panel import DiagnosticsPanel


class _FakeChatFull(QObject):
    """Fake con las 3 senales que usa el controller en produccion."""
    state_changed = Signal(object)
    conversation_changed = Signal()
    textual_tool_attempt = Signal()

    def __init__(self):
        super().__init__()
        self.messages: list[dict] = []
        self._last = ""

    def last_assistant_text(self) -> str:
        return self._last


class _FakeChatMin(QObject):
    """Fake sin state_changed ni textual_tool_attempt (fallback)."""
    streaming_changed = Signal(bool)
    conversation_changed = Signal()

    def __init__(self):
        super().__init__()
        self.messages: list[dict] = []

    def last_assistant_text(self) -> str:
        return ""


@pytest.fixture
def full(qapp):
    chat = _FakeChatFull()
    panel = DiagnosticsPanel()
    ctrl = DiagnosticsController(parent=None, chat=chat, panel=panel)
    ctrl._owner = chat
    yield ctrl, chat, panel


@pytest.fixture
def minimal(qapp):
    chat = _FakeChatMin()
    panel = DiagnosticsPanel()
    ctrl = DiagnosticsController(parent=None, chat=chat, panel=panel)
    ctrl._owner = chat
    yield ctrl, chat, panel


# -- CANCELLING no cierra el cronometro ----------------------------------

def test_cancelling_keeps_timer_running(full):
    """Entre STREAMING y CANCELLING el reloj sigue corriendo."""
    ctrl, chat, _ = full
    chat.state_changed.emit(ChatState.STREAMING)
    assert ctrl._in_flight is True

    chat.state_changed.emit(ChatState.CANCELLING)
    # No cerro el ciclo.
    assert ctrl._in_flight is True
    assert ctrl.stats.responses == 0


def test_idle_after_cancelling_closes_cycle(full):
    """Tras CANCELLING, IDLE si cierra el ciclo."""
    ctrl, chat, _ = full
    chat._last = "algo"
    chat.state_changed.emit(ChatState.STREAMING)
    chat.state_changed.emit(ChatState.CANCELLING)
    chat.state_changed.emit(ChatState.IDLE)

    assert ctrl._in_flight is False


def test_idle_without_streaming_is_noop(full):
    """IDLE sin STREAMING previo no debe contar."""
    ctrl, chat, _ = full
    chat.state_changed.emit(ChatState.IDLE)
    assert ctrl.stats.responses == 0


# -- set_metrics ---------------------------------------------------------

def test_set_metrics_empty_hides(full):
    ctrl, _, panel = full
    ctrl.set_metrics({})
    assert panel.metrics_label.text() == ""


def test_set_metrics_full(full):
    ctrl, _, panel = full
    ctrl.set_metrics({
        "prompt_eval_count": 100,
        "eval_count": 200,
        "eval_duration": 2_000_000_000,  # 2s -> 100 tok/s
    })
    text = panel.metrics_label.text()
    assert "100 prompt" in text
    assert "200 gen" in text
    assert "100.0 tok/s" in text


def test_set_metrics_only_prompt(full):
    ctrl, _, panel = full
    ctrl.set_metrics({"prompt_eval_count": 50})
    assert "50 prompt" in panel.metrics_label.text()
    assert "gen" not in panel.metrics_label.text()


def test_set_metrics_no_tok_s_when_duration_zero(full):
    ctrl, _, panel = full
    ctrl.set_metrics({
        "prompt_eval_count": 10,
        "eval_count": 20,
        "eval_duration": 0,
    })
    text = panel.metrics_label.text()
    assert "tok/s" not in text


# -- set_model -----------------------------------------------------------

def test_set_model_propagates_to_stats_and_panel(full):
    ctrl, _, panel = full
    ctrl.set_model("qwen3:8b", 0.5, 8192)
    assert ctrl.stats.model == "qwen3:8b"
    assert ctrl.stats.temperature == 0.5
    assert ctrl.stats.num_ctx == 8192
    # Panel formatea con "/" y "T"
    text = panel.model_label.text()
    assert "qwen3:8b" in text
    assert "0.5" in text
    assert "8192" in text


# -- conversation_changed ------------------------------------------------

def test_conversation_changed_refreshes_context(full):
    ctrl, chat, panel = full
    chat.messages = [
        {"role": "user", "content": "x" * 400},
        {"role": "assistant", "content": "y" * 400},
    ]
    chat.conversation_changed.emit()
    # 800 chars / 4 = 200 tokens aprox
    assert ctrl.stats.context_tokens > 0


# -- textual_tool_attempt ------------------------------------------------

def test_textual_tool_attempt_increments(full):
    ctrl, chat, _ = full
    chat.textual_tool_attempt.emit()
    chat.textual_tool_attempt.emit()
    assert ctrl.stats.textual_tool_attempts == 2


# -- reset ---------------------------------------------------------------

def test_reset_clears_metrics_keeps_model(full):
    ctrl, _, _ = full
    ctrl.set_model("m", 0.7, 4096)
    ctrl.stats.add_response(1.5)
    ctrl.stats.note_textual_tool()
    ctrl.reset()
    assert ctrl.stats.responses == 0
    assert ctrl.stats.textual_tool_attempts == 0
    # Modelo se mantiene
    assert ctrl.stats.model == "m"


# -- fallback sin state_changed ------------------------------------------

def test_fallback_to_streaming_changed(minimal):
    """Sin state_changed, usa streaming_changed (fakes de tests)."""
    ctrl, chat, _ = minimal
    chat.streaming_changed.emit(True)
    assert ctrl._in_flight is True
    chat.streaming_changed.emit(False)
    assert ctrl._in_flight is False


def test_no_textual_signal_does_not_crash(minimal):
    """Sin textual_tool_attempt, el controller arranca igual."""
    # Si llega aqui, no peto al conectar.
    assert minimal[0] is not None


# -- refresh_context publico --------------------------------------------

def test_refresh_context_public(full):
    ctrl, chat, panel = full
    chat.messages = [{"role": "user", "content": "x" * 100}]
    ctrl.refresh_context()
    assert ctrl.stats.context_tokens > 0
