"""Tests de RequestSnapshot."""
from __future__ import annotations

import logging
from unittest.mock import MagicMock, patch

import pytest

pytest.importorskip("PySide6")

from core.request_snapshot import RequestSnapshot


def _make_snap(**overrides) -> RequestSnapshot:
    defaults = dict(
        model="gpt-oss:20b",
        system_prompt_chars=100,
        system_prompt_preview="Eres un asistente...",
        history_messages=3,
        active_tools=["leer_archivo", "escribir_archivo"],
        options={"temperature": 0.7},
        thinking_override=None,
    )
    defaults.update(overrides)
    return RequestSnapshot(**defaults)


def test_to_log_incluye_campos_clave():
    snap = _make_snap()
    log = snap.to_log()
    assert "gpt-oss:20b" in log
    assert "leer_archivo" in log
    assert "escribir_archivo" in log
    assert "history:  3" in log


def test_to_log_sin_tools():
    snap = _make_snap(active_tools=[])
    log = snap.to_log()
    assert "(ninguna)" in log


def test_to_log_con_think_override():
    snap = _make_snap(thinking_override=False)
    log = snap.to_log()
    assert "think=False" in log


def test_to_log_sin_think_override():
    snap = _make_snap(thinking_override=None)
    log = snap.to_log()
    assert "think=(auto)" in log


def test_snapshot_es_none_sin_debug(monkeypatch):
    """Sin DEBUG activo, _prepare_context no construye snapshot."""
    from core.ollama import OllamaClient

    # Nos aseguramos de que isEnabledFor(DEBUG) es False.
    real = logging.Logger.isEnabledFor

    def fake_is_enabled(self, level):
        if level == logging.DEBUG:
            return False
        return real(self, level)

    monkeypatch.setattr(logging.Logger, "isEnabledFor", fake_is_enabled)

    client = OllamaClient()
    # _prepare_context llama a get_capabilities: lo parcheamos.
    with patch("core.ollama.get_capabilities") as gc:
        caps = MagicMock()
        caps.tool_mode = "native"
        caps.probed = True
        gc.return_value = caps
        with patch.object(client, "_choose_strategy") as cs:
            strategy = MagicMock()
            strategy.should_send_tools_param.return_value = False
            strategy.needs_full_buffer.return_value = False
            strategy.prepare_system_prompt.return_value = ""
            cs.return_value = strategy
            ctx = client._prepare_context(
                "test-model",
                [{"role": "user", "content": "hola"}],
                None,
                on_text=lambda _: None,
                on_tool=lambda *_: "",
                on_metrics=None,
                cancel_event=None,
                options=None,
                system_prompt="sys",
                context_window=None,
            )
    assert ctx.snapshot is None
