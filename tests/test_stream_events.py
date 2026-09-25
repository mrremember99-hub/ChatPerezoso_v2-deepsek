"""Tests del contrato de eventos tipados del stream de Ollama.

`iter_ollama_events` es la nueva interfaz: consume el stream y emite
objetos `StreamEvent` (TextDelta, ToolCallsDelta, StreamFinished). El
wrapper `_stream_async` los convierte a callbacks para no romper la
firma de `chat()`.

Estos tests verifican el contrato del iterador SIN ejecutar HTTP real:
usan un mock de AsyncClient igual que los otros tests de ollama.
"""
from __future__ import annotations

import json


from core.ollama import OllamaClient
from core.stream_events import (
    StreamFinished,
    TextDelta,
    ToolCallsDelta,
)


def _make_stream_client(monkeypatch, lineas: list[dict]) -> OllamaClient:
    """Construye un OllamaClient cuyo AsyncClient simula el stream dado.

    Cada elemento de `lineas` es un dict que se serializa a NDJSON.
    """
    import httpx

    encoded = [(json.dumps(item) + "\n").encode("utf-8") for item in lineas]

    class _FakeResponse:
        def raise_for_status(self): return None
        async def aiter_bytes(self, chunk_size=1024):
            for chunk in encoded:
                yield chunk

    class _FakeStreamCtx:
        async def __aenter__(self): return _FakeResponse()
        async def __aexit__(self, *a): return False

    class _FakeAsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def stream(self, *a, **k): return _FakeStreamCtx()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return OllamaClient()


def _run_iter(client: OllamaClient, **kwargs) -> list:
    """Consume el iterador de eventos sincrónicamente vía el runner."""
    async def _consume():
        events = []
        async for event in client.iter_ollama_events(
            {"model": "m", "messages": [], "stream": True},
            **kwargs,
        ):
            events.append(event)
        return events
    return client._async_runner.submit(_consume())


# ── iter_ollama_events ──────────────────────────────────────────────

def test_iter_emits_text_delta_per_chunk(monkeypatch):
    client = _make_stream_client(monkeypatch, [
        {"message": {"content": "Hola"}, "done": False},
        {"message": {"content": " mundo"}, "done": False},
        {"message": {}, "done": True},
    ])
    events = _run_iter(client)

    text_events = [e for e in events if isinstance(e, TextDelta)]
    assert len(text_events) == 2
    assert text_events[0].text == "Hola"
    assert text_events[1].text == " mundo"


def test_iter_emits_stream_finished_at_end(monkeypatch):
    client = _make_stream_client(monkeypatch, [
        {"message": {"content": "Hola"}, "done": False},
        {"message": {}, "done": True},
    ])
    events = _run_iter(client)

    # El último evento debe ser StreamFinished.
    assert isinstance(events[-1], StreamFinished)
    assert events[-1].content == "Hola"


def test_iter_emits_tool_calls_delta(monkeypatch):
    client = _make_stream_client(monkeypatch, [
        {
            "message": {
                "content": "",
                "tool_calls": [
                    {"function": {"name": "listar", "arguments": {}}},
                ],
            },
            "done": False,
        },
        {"message": {}, "done": True},
    ])
    events = _run_iter(client)

    tool_events = [e for e in events if isinstance(e, ToolCallsDelta)]
    assert len(tool_events) == 1
    assert tool_events[0].calls[0]["function"]["name"] == "listar"
    # El mensaje final debe incluir las tool_calls.
    finished = events[-1]
    assert isinstance(finished, StreamFinished)
    assert finished.message["tool_calls"][0]["function"]["name"] == "listar"


def test_iter_handles_split_utf8_across_chunks(monkeypatch):
    """Un carácter 'ñ' partido entre dos chunks se decodifica bien."""
    import httpx

    # Dos chunks: uno termina con el primer byte de 'ñ', el siguiente
    # empieza con el segundo. Los chunks son bytes crudos, no JSON.
    line1 = json.dumps({"message": {"content": "mañana"}, "done": False})
    line2 = json.dumps({"message": {}, "done": True})
    full = (line1 + "\n" + line2 + "\n").encode("utf-8")

    # Cortamos en la mitad de un carácter multibyte.
    split_at = full.find(b"\xc3") + 1  # el primer byte de 'ñ'
    chunk1 = full[:split_at]
    chunk2 = full[split_at:]

    class _FakeResponse:
        def raise_for_status(self): return None
        async def aiter_bytes(self, chunk_size=1024):
            yield chunk1
            yield chunk2

    class _FakeStreamCtx:
        async def __aenter__(self): return _FakeResponse()
        async def __aexit__(self, *a): return False

    class _FakeAsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def stream(self, *a, **k): return _FakeStreamCtx()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    client = OllamaClient()
    events = _run_iter(client)

    text_events = [e for e in events if isinstance(e, TextDelta)]
    assert len(text_events) == 1
    assert text_events[0].text == "mañana"
    assert "\ufffd" not in text_events[0].text


# ── parse_ollama_line ───────────────────────────────────────────────

def test_parse_line_returns_none_for_empty():
    assert OllamaClient.parse_ollama_line("", {}, []) is None
    assert OllamaClient.parse_ollama_line("   ", {}, []) is None


def test_parse_line_returns_none_for_invalid_json():
    assert OllamaClient.parse_ollama_line("{not json}", {}, []) is None


def test_parse_line_returns_text_delta_and_appends():
    message: dict = {}
    parts: list = []
    event = OllamaClient.parse_ollama_line(
        '{"message": {"content": "hola"}, "done": false}',
        message,
        parts,
    )
    assert isinstance(event, TextDelta)
    assert event.text == "hola"
    assert parts == ["hola"]


def test_parse_line_returns_tool_calls_delta():
    message: dict = {}
    parts: list = []
    raw_calls = [{"function": {"name": "x", "arguments": {}}}]
    line = json.dumps({"message": {"tool_calls": raw_calls}, "done": False})
    event = OllamaClient.parse_ollama_line(line, message, parts)
    assert isinstance(event, ToolCallsDelta)
    assert event.calls[0]["function"]["name"] == "x"
    assert message["tool_calls"][0]["function"]["name"] == "x"


def test_parse_line_marks_done():
    message: dict = {}
    parts: list = []
    OllamaClient.parse_ollama_line(
        '{"message": {}, "done": true}', message, parts
    )
    assert message["_done"] is True


def test_parse_line_prefers_text_over_tool_calls():
    """Si una línea tiene texto Y tool_calls, gana el TextDelta (el
    tool_calls ya se acumuló en el mensaje)."""
    message: dict = {}
    parts: list = []
    line = json.dumps({
        "message": {
            "content": "preambulo",
            "tool_calls": [{"function": {"name": "x", "arguments": {}}}],
        },
        "done": False,
    })
    event = OllamaClient.parse_ollama_line(line, message, parts)
    assert isinstance(event, TextDelta)
    # Y las tool_calls también se registraron en el mensaje
    assert message["tool_calls"]
