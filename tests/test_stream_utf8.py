"""Verifica que el streaming no corrompe caracteres UTF-8 multibyte.

Bug original: `iter_bytes(chunk_size=1024)` entrega bytes en fronteras
arbitrarias, no alineadas a caracteres UTF-8. Si un carácter multibyte
cae partido entre dos chunks, decodificar cada chunk por separado con
errors="replace" producía U+FFFD. El decoder incremental de codecs lo
evita reteniendo los bytes pendientes hasta completar el carácter.
"""
from __future__ import annotations

import codecs


REPLACEMENT_CHAR = chr(0xFFFD)


def test_incremental_decoder_handles_split_multibyte():
    """Un carácter 'ñ' partido entre dos chunks se decodifica bien."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    chunk1 = b"ma" + b"\xc3"
    chunk2 = b"\xb1" + b"ana"
    result = decoder.decode(chunk1) + decoder.decode(chunk2)
    assert result == "mañana"
    assert REPLACEMENT_CHAR not in result


def test_incremental_decoder_handles_emoji_split():
    """Un emoji (4 bytes) partido en 4 chunks."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    chunks = [b"\xf0", b"\x9f", b"\x98", b"\x80"]
    result = "".join(decoder.decode(c) for c in chunks)
    assert result == "😀"
    assert REPLACEMENT_CHAR not in result


def test_naive_decode_corrupts_split_multibyte():
    """Documenta el bug: decode por chunk SÍ corrompe."""
    chunk1 = b"ma" + b"\xc3"
    chunk2 = b"\xb1" + b"ana"
    naive = chunk1.decode("utf-8", errors="replace") + chunk2.decode("utf-8", errors="replace")
    assert REPLACEMENT_CHAR in naive
    assert naive != "mañana"


def test_decoder_final_flush():
    """El flush final no deja bytes pendientes."""
    decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")
    partial = decoder.decode(b"\xc3")
    assert partial == ""
    tail = decoder.decode(b"", final=True)
    assert REPLACEMENT_CHAR in tail


def test_stream_preserves_spanish_characters(monkeypatch):
    """El _stream completo preserva caracteres españoles entre chunks."""
    import json as _json
    import httpx
    from core.ollama import OllamaClient

    respuesta_json = _json.dumps({
        "message": {"content": "mañana"},
        "done": False,
    })
    done_json = _json.dumps({"message": {}, "done": True})

    lineas = [
        (respuesta_json + "\n").encode("utf-8"),
        (done_json + "\n").encode("utf-8"),
    ]

    class _FakeResponse:
        def raise_for_status(self):
            return None

        async def aiter_bytes(self, chunk_size=1024):
            # Emitimos byte a byte: así todo carácter multibyte cae
            # partido entre chunks consecutivos.
            for linea in lineas:
                for i in range(len(linea)):
                    yield linea[i:i+1]

    class _FakeCtx:
        async def __aenter__(self):
            return _FakeResponse()

        async def __aexit__(self, *a):
            return False

    class _FakeAsyncClient:
        def __init__(self, *a, **k):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *a):
            return False

        def stream(self, *a, **k):
            return _FakeCtx()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    client = OllamaClient()
    capturado = []
    result = client._stream(
        "test-model",
        [{"role": "user", "content": "di mañana"}],
        tools=None,
        on_text=capturado.append,
    )

    assert result["content"] == "mañana"
    assert "".join(capturado) == "mañana"
    assert REPLACEMENT_CHAR not in result["content"]
