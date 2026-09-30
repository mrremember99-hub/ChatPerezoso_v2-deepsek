"""S6-b-1a: tests de OllamaAdapter."""
from __future__ import annotations

import threading
from collections.abc import Iterator
from typing import Any

from core.harness.model import ModelDelta
from core.harness.ollama_adapter import OllamaAdapter


class _FakeClient:
    """OllamaClient minimo: chat_once() devuelve un script."""

    def __init__(self, script: list[dict[str, Any]]) -> None:
        self._script = list(script)
        self.calls: list[dict[str, Any]] = []

    def chat_once(
        self,
        model: str,
        messages: list[dict],
        *,
        tools=None,
        options=None,
        cancel_event=None,
    ) -> Iterator[dict[str, Any]]:
        self.calls.append({
            "model": model,
            "messages": [dict(m) for m in messages],
            "tools": tools,
            "options": options,
            "cancel_event": cancel_event,
        })
        yield from self._script


def _a(script, **kw) -> tuple[OllamaAdapter, _FakeClient]:
    c = _FakeClient(script)
    adapter = OllamaAdapter(c, model="m", **kw)
    return adapter, c


def test_solo_texto() -> None:
    adapter, _ = _a([
        {"kind": "text", "text": "hola"},
        {"kind": "done"},
    ])
    deltas = list(adapter.chat([{"role": "user", "content": "x"}]))
    assert deltas == [
        ModelDelta(kind="text", text="hola"),
        ModelDelta(kind="done"),
    ]


def test_texto_y_tool_call() -> None:
    tc = {"id": "c1", "function": {"name": "t", "arguments": {}}}
    adapter, _ = _a([
        {"kind": "text", "text": "leyendo"},
        {"kind": "tool_call", "tool_call": tc},
        {"kind": "done"},
    ])
    deltas = list(adapter.chat([{"role": "user", "content": "x"}]))
    assert deltas == [
        ModelDelta(kind="text", text="leyendo"),
        ModelDelta(kind="tool_call", tool_call=tc),
        ModelDelta(kind="done"),
    ]


def test_solo_tool_call() -> None:
    tc = {"id": "c1", "function": {"name": "t", "arguments": {}}}
    adapter, _ = _a([
        {"kind": "tool_call", "tool_call": tc},
        {"kind": "done"},
    ])
    deltas = list(adapter.chat([{"role": "user", "content": "x"}]))
    assert deltas == [
        ModelDelta(kind="tool_call", tool_call=tc),
        ModelDelta(kind="done"),
    ]


def test_texto_vacio_se_omite() -> None:
    adapter, _ = _a([
        {"kind": "text", "text": ""},
        {"kind": "done"},
    ])
    deltas = list(adapter.chat([{"role": "user", "content": "x"}]))
    assert deltas == [ModelDelta(kind="done")]


def test_kind_desconocido_se_ignora() -> None:
    adapter, _ = _a([
        {"kind": "thinking", "text": "razonamiento"},
        {"kind": "text", "text": "ok"},
        {"kind": "done"},
    ])
    deltas = list(adapter.chat([{"role": "user", "content": "x"}]))
    assert deltas == [
        ModelDelta(kind="text", text="ok"),
        ModelDelta(kind="done"),
    ]


def test_tool_call_malformado_se_ignora() -> None:
    adapter, _ = _a([
        {"kind": "tool_call", "tool_call": "no-dict"},
        {"kind": "done"},
    ])
    deltas = list(adapter.chat([{"role": "user", "content": "x"}]))
    assert deltas == [ModelDelta(kind="done")]


def test_config_pasa_por_constructor() -> None:
    tools = [{"type": "function", "function": {"name": "t"}}]
    opts = {"temperature": 0.1}
    adapter, client = _a(
        [{"kind": "done"}], options=opts,
    )
    list(adapter.chat(
        [{"role": "user", "content": "x"}],
        tools=tools,
    ))
    assert client.calls[0]["model"] == "m"
    assert client.calls[0]["tools"] == tools
    assert client.calls[0]["options"] == opts


def test_cancel_event_se_propaga() -> None:
    ev = threading.Event()
    adapter, client = _a([{"kind": "done"}])
    list(adapter.chat(
        [{"role": "user", "content": "x"}], cancel_event=ev,
    ))
    assert client.calls[0]["cancel_event"] is ev


def test_raw_no_dict_se_ignora() -> None:
    adapter, _ = _a([
        "no-dict",
        {"kind": "done"},
    ])
    deltas = list(adapter.chat([{"role": "user", "content": "x"}]))
    assert deltas == [ModelDelta(kind="done")]
