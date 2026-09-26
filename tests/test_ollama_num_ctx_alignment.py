"""Tests: num_ctx se alinea con context_window.limit_tokens."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.context_window import ContextWindow
from core.ollama import OllamaClient


def _client_with_stream_capture(captured):
    client = OllamaClient()
    client._prepare_context = MagicMock()
    ctx = MagicMock()
    ctx.history = []
    ctx.strategy = MagicMock()
    rr = MagicMock()
    rr.is_final = True
    rr.final_text = "ok"
    rr.tool_calls = []
    rr.completed = True
    rr.done_reason = None
    rr.visible_text = ""
    rr.assistant_content = ""
    rr.assistant_thinking = ""
    rr.retry_requested = False
    ctx.strategy.process_round.return_value = rr
    ctx.send_tools = None
    ctx.buffer_only = False
    ctx.tool_names = set()
    ctx.model = "test-model"
    ctx.snapshot = None
    client._prepare_context.return_value = ctx

    def fake_stream(model, messages, tools, on_text, **kw):
        captured.append({"options": kw.get("options")})
        return {
            "role": "assistant",
            "content": "ok",
            "_stream_completed": True,
        }
    client._stream = fake_stream
    return client


def test_num_ctx_se_inyecta_cuando_agente_no_lo_fija():
    captured = []
    client = _client_with_stream_capture(captured)
    cw = ContextWindow(limit_tokens=16384, output_reserve=1024)
    client.chat(
        "test-model",
        [{"role": "user", "content": "hola"}],
        None,
        lambda _: None,
        lambda *_: "",
        options={"temperature": 0.5},
        context_window=cw,
    )
    assert captured
    opts = captured[0]["options"]
    assert opts["num_ctx"] == 16384
    assert opts["temperature"] == 0.5


def test_num_ctx_no_pisa_el_del_agente():
    captured = []
    client = _client_with_stream_capture(captured)
    cw = ContextWindow(limit_tokens=16384, output_reserve=1024)
    client.chat(
        "test-model",
        [{"role": "user", "content": "hola"}],
        None,
        lambda _: None,
        lambda *_: "",
        options={"temperature": 0.5, "num_ctx": 4096},
        context_window=cw,
    )
    opts = captured[0]["options"]
    assert opts["num_ctx"] == 4096


def test_sin_context_window_no_se_anade_num_ctx():
    captured = []
    client = _client_with_stream_capture(captured)
    client.chat(
        "test-model",
        [{"role": "user", "content": "hola"}],
        None,
        lambda _: None,
        lambda *_: "",
        options={"temperature": 0.5},
        context_window=None,
    )
    opts = captured[0]["options"]
    assert "num_ctx" not in opts


def test_options_none_con_context_window_crea_dict():
    captured = []
    client = _client_with_stream_capture(captured)
    cw = ContextWindow(limit_tokens=8192, output_reserve=1024)
    client.chat(
        "test-model",
        [{"role": "user", "content": "hola"}],
        None,
        lambda _: None,
        lambda *_: "",
        options=None,
        context_window=cw,
    )
    opts = captured[0]["options"]
    assert opts == {"num_ctx": 8192}
