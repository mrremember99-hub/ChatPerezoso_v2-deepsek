"""H5: no buffering si el `{` esta dentro de un code-fence."""
from __future__ import annotations

import json

import pytest

from core import model_capabilities
from core.ollama import OllamaClient, _in_code_fence


@pytest.fixture(autouse=True)
def _reset_cache():
    model_capabilities.clear_cache()
    yield
    model_capabilities.clear_cache()


def _make_client(monkeypatch, rounds):
    import httpx

    class _ShowResponse:
        def raise_for_status(self): return None
        def json(self): return {"capabilities": ["tools"]}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _ShowResponse())
    iter_round = iter(rounds)

    class _FakeResponse:
        def __init__(self, lines): self._lines = lines
        def raise_for_status(self): return None
        async def aiter_bytes(self, chunk_size=1024):
            for line in self._lines:
                yield (line + "\n").encode("utf-8")

    class _FakeStreamCtx:
        def __init__(self, lines): self._lines = lines
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
        return [{"type": "function", "function": {"name": "leer_archivo", "description": "", "parameters": {"type": "object", "properties": {}, "required": []}}}]
    def intent_rules(self):
        from core.intent import IntentRule
        # Reglas que autorizan leer_archivo con "lee el archivo".
        # Sin esto, tool_names queda vacio, el gate no expone
        # nada y el parser de tool call textual no detecta el JSON.
        return {
            "leer_archivo": IntentRule(
                verbs=("lee", "leer"),
                target_words=("archivo",),
                accepts_filename=True,
            ),
        }
    def call(self, *a, **k): return "contenido"
    def requires_confirmation(self, *a): return False


def test_in_code_fence_helper():
    # Sin backticks: no estamos en fence.
    assert not _in_code_fence("texto")
    # Fence abierto: 1 par de ``` (impar).
    assert _in_code_fence("a```\nb\n")
    # Fence cerrado: 2 pares (par).
    assert not _in_code_fence("a```\nb\n```\n")
    # Fence con lenguaje.
    assert _in_code_fence("a```json\n")


def test_brace_dentro_de_fence_no_bufferea(monkeypatch):
    """Un `{` tras un ``` no activa buffering."""
    client = _make_client(monkeypatch, rounds=[[
        {"message": {"content": "Aqui un ejemplo:\n```json\n"}, "done": False},
        {"message": {"content": '{"a": 1}'}, "done": False},
        {"message": {"content": "\n```\n"}, "done": False},
        {"message": {}, "done": True},
    ]])

    emitted: list[str] = []
    client.chat(
        "test-model",
        [{"role": "user", "content": "dame un ejemplo"}],
        _Tools(),
        emitted.append,
        lambda *a: "",
    )

    full = "".join(emitted)
    assert '{"a": 1}' in full
    assert "Aqui un ejemplo" in full


def test_brace_sin_fence_sigue_buffereando(monkeypatch):
    """Sin fence, el `{` sigue activando el buffering anti-tool-call."""
    client = _make_client(monkeypatch, rounds=[
        [
            {"message": {"content": "Voy a usar: "}, "done": False},
            {"message": {"content": '{"name": "leer_archivo"}'}, "done": False},
            {"message": {}, "done": True},
        ],
        # Ronda 2: el retry textual consume un segundo round.
        [
            {"message": {"content": "Contenido leido."}, "done": False},
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

    full = "".join(emitted)
    # El texto previo se emite una vez, pero el JSON crudo no.
    assert full.count("Voy a usar: ") == 1
    assert '"name"' not in full
