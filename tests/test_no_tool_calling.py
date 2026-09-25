"""Tests de deteccion temprana de modelos sin tool calling."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")

from core.models_config import VERIFIED_TOOL_MODELS


def _make_ctrl():
    from ui.controllers.chat_controller import ChatController
    ctrl = ChatController.__new__(ChatController)
    ctrl._consecutive_textual_failures = 0
    ctrl._last_model = ""
    ctrl._current_actions = []
    ctrl.textual_tool_attempt = MagicMock()
    ctrl.no_tool_calling_detected = MagicMock()
    return ctrl


def _simulate_failure(ctrl):
    ctrl._consecutive_textual_failures += 1
    if (
        ctrl._consecutive_textual_failures >= 2
        and ctrl._last_model not in VERIFIED_TOOL_MODELS
    ):
        ctrl.no_tool_calling_detected.emit(ctrl._last_model)
        ctrl._consecutive_textual_failures = 0


def test_verificados_incluye_los_esperados():
    assert "gpt-oss:20b" in VERIFIED_TOOL_MODELS
    assert "ministral-3" in VERIFIED_TOOL_MODELS


def test_reset_pone_contador_a_cero():
    ctrl = _make_ctrl()
    ctrl._consecutive_textual_failures = 5
    ctrl.reset_textual_failures()
    assert ctrl._consecutive_textual_failures == 0


def test_dos_fallos_con_modelo_no_verificado_emite():
    ctrl = _make_ctrl()
    ctrl._last_model = "granite"
    _simulate_failure(ctrl)
    assert ctrl.no_tool_calling_detected.emit.call_count == 0
    _simulate_failure(ctrl)
    ctrl.no_tool_calling_detected.emit.assert_called_once_with("granite")
    assert ctrl._consecutive_textual_failures == 0


def test_dos_fallos_con_modelo_verificado_no_emite():
    ctrl = _make_ctrl()
    ctrl._last_model = "gpt-oss:20b"
    _simulate_failure(ctrl)
    _simulate_failure(ctrl)
    assert ctrl.no_tool_calling_detected.emit.call_count == 0


def test_un_solo_fallo_no_emite():
    ctrl = _make_ctrl()
    ctrl._last_model = "granite"
    _simulate_failure(ctrl)
    assert ctrl.no_tool_calling_detected.emit.call_count == 0
    assert ctrl._consecutive_textual_failures == 1


def test_ministral_con_tag_es_verificado():
    from core.models_config import is_verified_tool_model
    assert is_verified_tool_model("ministral-3:latest")
    assert is_verified_tool_model("ministral-3")


def test_gpt_oss_con_y_sin_tag_es_verificado():
    from core.models_config import is_verified_tool_model
    assert is_verified_tool_model("gpt-oss:20b")
    assert is_verified_tool_model("gpt-oss")


def test_modelo_desconocido_no_es_verificado():
    from core.models_config import is_verified_tool_model
    assert not is_verified_tool_model("granite")
    assert not is_verified_tool_model("granite:latest")
    assert not is_verified_tool_model("ornith-1.5:9b")
    assert not is_verified_tool_model("")


# -- Flujo completo: _on_done con fallo textual forzado --------------------


def test_on_done_emite_tras_dos_fallos_textuales(monkeypatch):
    ctrl = _make_ctrl()
    ctrl._last_model = "ornith-1.5:9b"
    ctrl._current_actions = []
    ctrl.renderer = MagicMock()
    ctrl._drain_stream = MagicMock()
    ctrl._summarize_actions = MagicMock(return_value="")
    ctrl._append_message = MagicMock()
    ctrl.assistant_message = MagicMock()
    ctrl._finish = MagicMock()
    monkeypatch.setattr(
        "ui.controllers.chat_controller.is_textual_tool_failure",
        lambda _: True,
    )
    ctrl._on_done("")
    ctrl._on_done("")
    assert ctrl.no_tool_calling_detected.emit.call_count == 1
    assert ctrl._consecutive_textual_failures == 0


def test_on_done_con_modelo_verificado_no_emite(monkeypatch):
    ctrl = _make_ctrl()
    ctrl._last_model = "gpt-oss:20b"
    ctrl._current_actions = []
    ctrl.renderer = MagicMock()
    ctrl._drain_stream = MagicMock()
    ctrl._summarize_actions = MagicMock(return_value="")
    ctrl._append_message = MagicMock()
    ctrl.assistant_message = MagicMock()
    ctrl._finish = MagicMock()
    monkeypatch.setattr(
        "ui.controllers.chat_controller.is_textual_tool_failure",
        lambda _: True,
    )
    ctrl._on_done("")
    ctrl._on_done("")
    ctrl._on_done("")
    assert ctrl.no_tool_calling_detected.emit.call_count == 0
