"""Tests end-to-end del modo XML en OllamaClient.chat."""
from __future__ import annotations


import pytest

from core import model_capabilities
from core.intent import IntentRule
from core.ollama import OllamaClient


@pytest.fixture(autouse=True)
def _reset_cache():
    model_capabilities.clear_cache()
    yield
    model_capabilities.clear_cache()


class _FakeTools:
    """Provider mínimo para tests."""

    def __init__(self):
        self.calls: list[tuple[str, dict]] = []

    def definitions(self):
        return [{
            "type": "function",
            "function": {
                "name": "listar_carpeta",
                "description": "Lista el contenido.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": [],
                },
            },
        }]

    def intent_rules(self):
        return {
            "listar_carpeta": IntentRule(
                verbs=("lista", "listar"),
                target_words=("carpeta", "workspace"),
            ),
        }

    def call(self, name, arguments, *, allow_destructive=False, cancel_event=None):
        self.calls.append((name, arguments))
        return "archivo1.txt\narchivo2.txt"

    def requires_confirmation(self, name):
        return False


def _patch_show(monkeypatch, capabilities):
    import httpx

    class _ShowResponse:
        def raise_for_status(self): return None
        def json(self):
            return {"capabilities": capabilities}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _ShowResponse())


def _patch_stream(monkeypatch, rounds):
    """Sustituye httpx.AsyncClient para devolver los rounds dados.

    Cada round es una lista de diccionarios que se convierten a las
    líneas NDJSON que Ollama enviaría. El cliente ahora usa la API
    async (AsyncClient.aiter_bytes), no la sync.
    """
    import httpx
    captured_payloads = []
    iter_round = iter(rounds)

    class _FakeStreamResponse:
        def __init__(self, lines):
            self._lines = lines
        def raise_for_status(self): return None
        async def aiter_bytes(self, chunk_size=1024):
            for line in self._lines:
                yield (line + chr(10)).encode("utf-8")

    class _FakeStreamCtx:
        def __init__(self, lines):
            self._lines = lines
        async def __aenter__(self):
            return _FakeStreamResponse(self._lines)
        async def __aexit__(self, *a):
            return False

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        def stream(self, method, url, **kwargs):
            captured_payloads.append(kwargs.get("json"))
            try:
                round_data = next(iter_round)
            except StopIteration:
                round_data = []
            lines = [json_module_dumps(c) for c in round_data]
            return _FakeStreamCtx(lines)

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return captured_payloads



def json_module_dumps(obj):
    import json as _json
    return _json.dumps(obj)


# ── Tests ────────────────────────────────────────────────────────────

def test_chat_xml_mode_executes_tool_and_returns_text(monkeypatch):
    _patch_show(monkeypatch, capabilities=["completion"])
    _patch_stream(monkeypatch, rounds=[
        # Round 1: el modelo emite un bloque XML
        [{"message": {"content": 'Voy a listar.\n<tool_call>{"name": "listar_carpeta", "arguments": {"path": "."}}</tool_call>'}, "done": False},
         {"message": {}, "done": True}],
        # Round 2: respuesta final sin XML
        [{"message": {"content": "Aquí están los archivos."}, "done": False},
         {"message": {}, "done": True}],
    ])

    client = OllamaClient()
    tools = _FakeTools()
    texts = []

    result = client.chat(
        "deepseek-r1",
        [{"role": "user", "content": "Lista la carpeta"}],
        tools,
        texts.append,
        on_tool=tools.call,
    )

    assert result == "Aquí están los archivos."
    assert tools.calls == [("listar_carpeta", {"path": "."})]
    # El XML se limpió del texto visible
    assert all("<tool_call>" not in t for t in texts)
    assert "Voy a listar." in "".join(texts)


def test_chat_xml_mode_does_not_send_tools_param(monkeypatch):
    """En modo XML, el payload no debe llevar `tools`."""
    _patch_show(monkeypatch, capabilities=[])
    payloads = _patch_stream(monkeypatch, rounds=[
        [{"message": {"content": "Respuesta directa."}, "done": False},
         {"message": {}, "done": True}],
    ])

    client = OllamaClient()
    tools = _FakeTools()
    client.chat(
        "deepseek-r1",
        [{"role": "user", "content": "Lista la carpeta"}],
        tools,
        lambda t: None,
        on_tool=tools.call,
    )

    assert len(payloads) == 1
    assert "tools" not in payloads[0]
    # El system prompt incluye el bloque XML de instrucciones
    system_msg = payloads[0]["messages"][0]
    assert system_msg["role"] == "system"
    assert "tool_call" in system_msg["content"]


def test_chat_native_mode_sends_tools_param(monkeypatch):
    """En modo nativo, el payload debe incluir `tools`."""
    _patch_show(monkeypatch, capabilities=["tools"])
    payloads = _patch_stream(monkeypatch, rounds=[
        [{"message": {"content": "Respuesta."}, "done": False},
         {"message": {}, "done": True}],
    ])

    client = OllamaClient()
    tools = _FakeTools()
    client.chat(
        "llama3.1",
        [{"role": "user", "content": "Lista la carpeta"}],
        tools,
        lambda t: None,
        on_tool=tools.call,
    )

    assert len(payloads) == 1
    assert "tools" in payloads[0]
    assert payloads[0]["tools"][0]["function"]["name"] == "listar_carpeta"


def test_chat_xml_mode_without_tool_call_returns_text(monkeypatch):
    """Modo XML pero el modelo no emite XML: se devuelve el texto."""
    _patch_show(monkeypatch, capabilities=[])
    _patch_stream(monkeypatch, rounds=[
        [{"message": {"content": "No necesito herramientas."}, "done": False},
         {"message": {}, "done": True}],
    ])

    client = OllamaClient()
    tools = _FakeTools()
    texts = []

    result = client.chat(
        "deepseek-r1",
        [{"role": "user", "content": "Lista la carpeta"}],
        tools,
        texts.append,
        on_tool=tools.call,
    )

    assert result == "No necesito herramientas."
    assert tools.calls == []
    assert texts == ["No necesito herramientas."]


def test_chat_xml_mode_unknown_tool_in_block_is_ignored(monkeypatch):
    """Bloque XML con nombre no permitido se descarta y se trata como texto."""
    _patch_show(monkeypatch, capabilities=[])
    _patch_stream(monkeypatch, rounds=[
        [{"message": {"content": '<tool_call>{"name": "inventada", "arguments": {}}</tool_call>'}, "done": False},
         {"message": {}, "done": True}],
    ])

    client = OllamaClient()
    tools = _FakeTools()
    client.chat(
        "deepseek-r1",
        [{"role": "user", "content": "Lista la carpeta"}],
        tools,
        lambda t: None,
        on_tool=tools.call,
    )

    assert tools.calls == []


def test_chat_xml_mode_multiple_tools_in_one_round(monkeypatch):
    _patch_show(monkeypatch, capabilities=[])
    _patch_stream(monkeypatch, rounds=[
        [{"message": {"content": (
            '<tool_call>{"name": "listar_carpeta", "arguments": {}}</tool_call>'
        )}, "done": False},
         {"message": {}, "done": True}],
        [{"message": {"content": "Hecho."}, "done": False},
         {"message": {}, "done": True}],
    ])

    client = OllamaClient()
    tools = _FakeTools()
    client.chat(
        "deepseek-r1",
        [{"role": "user", "content": "Lista la carpeta"}],
        tools,
        lambda t: None,
        on_tool=tools.call,
    )

    assert len(tools.calls) == 1
    assert tools.calls[0][0] == "listar_carpeta"
