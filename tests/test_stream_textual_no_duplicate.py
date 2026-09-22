"""Regresión N4: no duplicar texto al detectar/descartar tool-call textual.

Antes de este fix, si el detector veía un `{` en un delta y después
resultaba que NO era una tool-call (falso positivo), se emitía el
`content` COMPLETO al final. Como los deltas previos al `{` ya se
habían emitido, el usuario veía:

    "Aquí tienes un ejemplo:\n\n"
    "Aquí tienes un ejemplo:\n\n{\"key\": \"value\"}"

Este test verifica que solo se emite la parte buffereada, no el
contenido completo, cuando no hay tool-call real.
"""
from __future__ import annotations

import json

import pytest

from core import model_capabilities
from core.ollama import OllamaClient


@pytest.fixture(autouse=True)
def _reset_cache():
    model_capabilities.clear_cache()
    yield
    model_capabilities.clear_cache()


def _make_client(monkeypatch, rounds: list[list[dict]]):
    """Construye un OllamaClient con un stream mockeado."""
    import httpx

    # /api/show → asume nativo (no XML).
    class _ShowResponse:
        def raise_for_status(self): return None
        def json(self): return {"capabilities": ["tools"]}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _ShowResponse())

    iter_round = iter(rounds)

    class _FakeResponse:
        def __init__(self, lines):
            self._lines = lines
        def raise_for_status(self): return None
        async def aiter_bytes(self, chunk_size=1024):
            for line in self._lines:
                yield (line + "\n").encode("utf-8")

    class _FakeStreamCtx:
        def __init__(self, lines):
            self._lines = lines
        async def __aenter__(self): return _FakeResponse(self._lines)
        async def __aexit__(self, *a): return False

    class _FakeAsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def stream(self, *a, **k):
            try:
                data = next(iter_round)
            except StopIteration:
                data = []
            lines = [json.dumps(d) for d in data]
            return _FakeStreamCtx(lines)

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return OllamaClient()


class _Tools:
    def definitions(self):
        return [{
            "type": "function",
            "function": {
                "name": "leer_archivo",
                "description": "",
                "parameters": {
                    "type": "object", "properties": {}, "required": [],
                },
            },
        }]

    def intent_rules(self):
        from core.intent import IntentRule
        return {"leer_archivo": IntentRule(verbs=("lee",), target_words=())}

    def call(self, *a, **k):
        return "contenido"

    def requires_confirmation(self, *a):
        return False


def test_false_positive_does_not_duplicate(monkeypatch):
    """Falso positivo: `{` en texto normal, no es tool call.

    El texto ANTES del `{` ya se emitió. El texto desde el `{` está
    bufferizado. Si al final no era tool call, solo se emite el
    buffer, no el content completo.
    """
    client = _make_client(monkeypatch, rounds=[[
        {"message": {"content": "Aquí un ejemplo: "}, "done": False},
        {"message": {"content": "{\"key\": \"valor\"}"}, "done": False},
        {"message": {}, "done": True},
    ]])

    emitted: list[str] = []
    client.chat(
        "test-model",
        [{"role": "user", "content": "explícame algo"}],
        _Tools(),
        emitted.append,
        lambda *a: "no debe llamarse",
    )

    full = "".join(emitted)
    # El texto debe aparecer UNA vez, no dos.
    assert full.count("Aquí un ejemplo:") == 1, (
        f"texto duplicado: {full!r}"
    )
    assert full.count('{"key": "valor"}') == 1, (
        f"json duplicado: {full!r}"
    )


def test_real_tool_call_does_not_emit_buffered_content(monkeypatch):
    """Tool call real: el contenido buffereado NO debe emitirse."""
    # Este caso es raro en la practica porque Ollama emite tool_calls
    # nativos cuando el modelo los soporta. Lo importante es que si
    # el content se queda en el buffer por parecer JSON, y luego SÍ
    # resulta que era una tool call, no se emite nada raro.
    client = _make_client(monkeypatch, rounds=[
        [
            {"message": {"content": "Voy a leer el archivo. "}, "done": False},
            {"message": {"content": "{\"name\": \"leer_archivo\", \"arguments\": {}}"}, "done": False},
            {"message": {"tool_calls": [{"function": {"name": "leer_archivo", "arguments": {}}}]}, "done": True},
        ],
        # Segunda ronda: respuesta final.
        [
            {"message": {"content": "Hecho."}, "done": False},
            {"message": {}, "done": True},
        ],
    ])

    emitted: list[str] = []
    client.chat(
        "test-model",
        [{"role": "user", "content": "lee el archivo"}],
        _Tools(),
        emitted.append,
        lambda *a: "contenido",
    )

    # El texto "Voy a leer el archivo." aparece una vez (emitido antes
    # del `{`). El JSON buffereado NO se emite porque era tool call.
    full = "".join(emitted)
    assert full.count("Voy a leer") == 1
    assert '{"name"' not in full, f"json crudo visible: {full!r}"


def test_no_buffering_normal_text_unchanged(monkeypatch):
    """Sin `{` en ningún delta, todo se emite tal cual (sin cambio)."""
    client = _make_client(monkeypatch, rounds=[[
        {"message": {"content": "Hola "}, "done": False},
        {"message": {"content": "mundo."}, "done": False},
        {"message": {}, "done": True},
    ]])

    emitted: list[str] = []
    client.chat(
        "test-model",
        [{"role": "user", "content": "saluda"}],
        _Tools(),
        emitted.append,
        lambda *a: "",
    )

    assert "".join(emitted) == "Hola mundo."
    