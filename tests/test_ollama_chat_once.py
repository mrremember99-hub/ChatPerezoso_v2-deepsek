"""S6-a: OllamaClient.chat_once() — una ronda sin bucle.

Sin red: monkeypatch de OllamaClient._stream para devolver un
dict message programado.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.ollama import OllamaClient  # noqa: E402


def _client_with_message(monkeypatch, message: dict) -> OllamaClient:
    """Cliente con _stream monkeypatcheado para devolver `message`."""
    client = OllamaClient()
    monkeypatch.setattr(client, "_stream", lambda *a, **kw: message)
    return client


def test_chat_once_solo_texto(monkeypatch):
    client = _client_with_message(monkeypatch, {
        "role": "assistant",
        "content": "hola",
        "tool_calls": [],
    })
    deltas = list(client.chat_once(
        "m", [{"role": "user", "content": "x"}],
    ))
    assert deltas == [
        {"kind": "text", "text": "hola"},
        {"kind": "done"},
    ]


def test_chat_once_texto_mas_tool_call(monkeypatch):
    tc = {
        "id": "c1",
        "function": {"name": "leer_archivo", "arguments": {"path": "a.py"}},
    }
    client = _client_with_message(monkeypatch, {
        "role": "assistant",
        "content": "leyendo",
        "tool_calls": [tc],
    })
    deltas = list(client.chat_once(
        "m", [{"role": "user", "content": "x"}],
    ))
    assert deltas == [
        {"kind": "text", "text": "leyendo"},
        {"kind": "tool_call", "tool_call": tc},
        {"kind": "done"},
    ]


def test_chat_once_solo_tool_call(monkeypatch):
    tc = {"id": "c1", "function": {"name": "t", "arguments": {}}}
    client = _client_with_message(monkeypatch, {
        "role": "assistant",
        "content": "",
        "tool_calls": [tc],
    })
    deltas = list(client.chat_once(
        "m", [{"role": "user", "content": "x"}],
    ))
    assert deltas == [
        {"kind": "tool_call", "tool_call": tc},
        {"kind": "done"},
    ]


def test_chat_once_vacio(monkeypatch):
    client = _client_with_message(monkeypatch, {
        "role": "assistant",
        "content": "",
        "tool_calls": [],
    })
    deltas = list(client.chat_once(
        "m", [{"role": "user", "content": "x"}],
    ))
    assert deltas == [{"kind": "done"}]


def test_chat_once_pasa_tools_y_options(monkeypatch):
    captured: dict = {}

    def fake_stream(model, messages, tools, on_text, **kw):
        captured["model"] = model
        captured["messages"] = messages
        captured["tools"] = tools
        captured["on_text"] = on_text
        captured["options"] = kw.get("options")
        return {"role": "assistant", "content": "", "tool_calls": []}

    client = OllamaClient()
    monkeypatch.setattr(client, "_stream", fake_stream)

    tools = [{"type": "function", "function": {"name": "t"}}]
    opts = {"temperature": 0.1}
    list(client.chat_once(
        "m",
        [{"role": "user", "content": "x"}],
        tools=tools,
        options=opts,
    ))
    assert captured["model"] == "m"
    assert captured["tools"] == tools
    assert captured["options"] == opts
    # on_text debe ser None: el adapter consume los dicts.
    assert captured["on_text"] is None
