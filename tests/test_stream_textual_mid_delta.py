"""Regresión N5: el detector textual encuentra `{` en cualquier posición.

Antes, el detector solo miraba el primer carácter no-espacio del delta.
Con deltas de 4-5 chars (típico en Ollama), el `{` de un JSON
frecuentemente cae en medio:

    Delta 1: "Claro, aquí tie"
    Delta 2: 'nes: {"name": "lee'    ← no empieza por `{`

Antes el JSON se emitía tal cual. Ahora se detecta la posición del
`{`/`[` en cualquier sitio del delta, se emite la parte previa y se
bufferiza desde el `{`.
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
    import httpx

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
        # target_words=(archivo,) + requires_target=True (default)
        # para que el gate autorice cuando el prompt dice "lee el archivo".
        return {
            "leer_archivo": IntentRule(
                verbs=("lee", "leer"),
                target_words=("archivo",),
                accepts_filename=True,
            ),
        }
    def call(self, *a, **k): return "contenido"
    def requires_confirmation(self, *a): return False


def test_brace_in_middle_of_delta_is_detected(monkeypatch):
    """Falso positivo con `{` en medio: no se duplica y no se ve JSON."""
    client = _make_client(monkeypatch, rounds=[[
        # Delta 1: texto normal.
        {"message": {"content": "Claro, aquí tie"}, "done": False},
        # Delta 2: `{` cae en MEDIO del delta. Antes se emitía tal cual.
        {"message": {"content": 'nes: {"name": "leer_archivo"}'}, "done": False},
        {"message": {}, "done": True},
    ]])

    emitted: list[str] = []
    client.chat(
        "test-model",
        [{"role": "user", "content": "explícame"}],
        _Tools(),
        emitted.append,
        lambda *a: "",
    )

    full = "".join(emitted)
    # El texto debe aparecer UNA vez. Sin duplicación.
    assert full.count("Claro, aquí tie") == 1, f"duplicado: {full!r}"
    # Y el JSON no debe verse (el falso positivo se detecta y el buffer se emite limpio).
    # Nota: en este test, al final NO hay tool call real, así que el buffered
    # se emite. Pero NO debe verse el `nes: ` duplicado.
    # El "nes: " es parte del buffer, se emite una sola vez.
    assert full.count("nes: ") == 1, f"'nes: ' duplicado o ausente: {full!r}"


def test_bracket_in_middle_of_delta_is_detected(monkeypatch):
    """`[` en medio del delta también activa el buffer."""
    client = _make_client(monkeypatch, rounds=[[
        {"message": {"content": "Mira: "}, "done": False},
        {"message": {"content": 'aquí: ["a", "b", "c"]'}, "done": False},
        {"message": {}, "done": True},
    ]])

    emitted: list[str] = []
    client.chat(
        "test-model",
        [{"role": "user", "content": "lista"}],
        _Tools(),
        emitted.append,
        lambda *a: "",
    )

    full = "".join(emitted)
    # El texto antes del `[` se emite una vez.
    assert full.count("Mira: ") == 1
    # El texto entre `Mira: ` y `[` se emite antes del buffer.
    assert "aquí: " in full


def test_early_chars_before_brace_are_emitted(monkeypatch):
    """Los chars antes del `{` se emiten como texto normal.

    El modelo emite un tool call textual en la ronda 1; el retry
    consume un segundo round. Sin ese segundo round, el stream del
    retry queda vacio y (con H1) se rechaza como interrumpido.
    """
    client = _make_client(monkeypatch, rounds=[
        [
            {"message": {"content": 'Voy a usar: {"name": "leer_archivo"}'}, "done": False},
            {"message": {}, "done": True},
        ],
        [
            {"message": {"content": "Listo."}, "done": False},
            {"message": {}, "done": True},
        ],
    ])

    emitted: list[str] = []
    client.chat(
        "test-model",
        [{"role": "user", "content": "lee el archivo"}],
        _Tools(),
        emitted.append,
        lambda *a: "",
    )

    full = "".join(emitted)
    # "Voy a usar: " se emite (antes del `{`).
    assert "Voy a usar: " in full
    # El JSON no aparece crudo.
    assert '"name"' not in full or full.count('"name"') == 0, (
        f"JSON visible: {full!r}"
    )