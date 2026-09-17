from __future__ import annotations

import pytest

from ui.diagnostics import SessionStats, estimate_tokens


# -- SessionStats ------------------------------------------------------------

def test_empty_stats():
    stats = SessionStats()
    assert stats.responses == 0
    assert stats.average_response_seconds == 0.0
    assert stats.context_tokens == 0


def test_add_response_updates_average():
    stats = SessionStats()
    stats.add_response(2.0)
    stats.add_response(4.0)
    assert stats.responses == 2
    assert stats.average_response_seconds == 3.0


def test_add_response_ignores_negative():
    stats = SessionStats()
    stats.add_response(-5.0)
    assert stats.total_response_seconds == 0.0


def test_reset_metrics_keeps_model_config():
    stats = SessionStats(model="llama3", temperature=0.3, num_ctx=8192)
    stats.add_response(2.0)
    stats.reset_metrics()
    assert stats.responses == 0
    assert stats.total_response_seconds == 0.0
    assert stats.model == "llama3"
    assert stats.temperature == 0.3
    assert stats.num_ctx == 8192


def test_update_context_counts_chars():
    stats = SessionStats()
    stats.update_context([
        {"role": "user", "content": "a" * 400},
        {"role": "assistant", "content": "b" * 400},
    ])
    assert stats.context_tokens == 200  # 800 chars / 4


def test_update_context_ignores_non_string_content():
    stats = SessionStats()
    stats.update_context([
        {"role": "user", "content": "abcd"},
        {"role": "assistant", "content": None},
        {"role": "tool", "content": 12345},
    ])
    assert stats.context_tokens == 1


def test_estimate_tokens_helper():
    assert estimate_tokens("a" * 400) == 100
    assert estimate_tokens("") == 0


# -- Diagnóstico end-to-end --------------------------------------------------

def test_diagnostics_controller_updates_panel(qapp):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QObject

    from ui.controllers.diagnostics_controller import DiagnosticsController
    from ui.views.diagnostics_panel import DiagnosticsPanel

    class _FakeChat(QObject):
        from PySide6.QtCore import Signal as _S
        streaming_changed = _S(bool)
        conversation_changed = _S()

        def __init__(self):
            super().__init__()
            self.messages: list[dict] = []

    chat = _FakeChat()
    panel = DiagnosticsPanel()
    ctrl = DiagnosticsController(parent=None, chat=chat, panel=panel)
    ctrl._owner = chat  # mantiene viva la referencia

    ctrl.set_model("llama3", 0.5, 8192)
    assert "llama3" in panel.model_label.text()
    assert "0.5" in panel.model_label.text()
    # 8192 no es múltiplo de 1000, así que el formateador deja el número
    # exacto. Si fuera 8000, mostraría "8k".
    assert "8192" in panel.model_label.text()

    # Comprobación complementaria del formateador abreviado.
    ctrl.set_model("llama3", 0.5, 8000)
    assert "8k" in panel.model_label.text()


def test_diagnostics_controller_averages_elapsed(qapp):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QObject

    from ui.controllers.diagnostics_controller import DiagnosticsController
    from ui.views.diagnostics_panel import DiagnosticsPanel

    class _FakeChat(QObject):
        from PySide6.QtCore import Signal as _S
        streaming_changed = _S(bool)
        conversation_changed = _S()

        def __init__(self):
            super().__init__()
            self.messages: list[dict] = []

    chat = _FakeChat()
    panel = DiagnosticsPanel()
    ctrl = DiagnosticsController(parent=None, chat=chat, panel=panel)
    ctrl._owner = chat

    ctrl.stats.add_response(1.5)
    ctrl.stats.add_response(2.5)
    ctrl._refresh_responses()
    assert "2" in panel.response_label.text()


def test_diagnostics_controller_refreshes_context(qapp):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QObject

    from ui.controllers.diagnostics_controller import DiagnosticsController
    from ui.views.diagnostics_panel import DiagnosticsPanel

    class _FakeChat(QObject):
        from PySide6.QtCore import Signal as _S
        streaming_changed = _S(bool)
        conversation_changed = _S()

        def __init__(self):
            super().__init__()
            self.messages: list[dict] = []

    chat = _FakeChat()
    panel = DiagnosticsPanel()
    ctrl = DiagnosticsController(parent=None, chat=chat, panel=panel)
    ctrl._owner = chat

    chat.messages.append({"role": "user", "content": "a" * 4000})
    ctrl._refresh_context()
    assert "1.0k" in panel.context_label.text()
