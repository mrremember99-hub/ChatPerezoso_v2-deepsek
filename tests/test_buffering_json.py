"""Regresión #1: buffering textual solo cuando es probable tool call.

Antes: cualquier `{` o `[` activaba buffering y congelaba el stream
hasta el final de la respuesta.

Ahora: se cancela en cuanto se ve un cierre de JSON (`}` o `]`) sin
keyword de tool call, o tras 200 caracteres sin keyword ni cierre.
El texto se emite incrementalmente.
"""
from __future__ import annotations

import asyncio
import json
from unittest.mock import patch

from core.ollama import OllamaClient
from core.stream_events import StreamFinished, TextDelta


async def _run_stream(deltas: list[str], tools=None):
    """Simula un stream y captura CADA llamada a on_text por separado."""
    calls: list[str] = []

    async def fake_iter(self, payload, *, cancel_event=None):
        for text in deltas:
            yield TextDelta(text)
        yield StreamFinished(
            message={"role": "assistant", "content": "".join(deltas)},
            metrics={},
            completed=True,
            done_reason="stop",
        )

    client = OllamaClient()
    with patch.object(OllamaClient, "iter_ollama_events", fake_iter):
        await client._stream_async(
            model="test",
            messages=[],
            tools=tools,
            on_text=calls.append,
        )
    return calls


def test_json_en_prosa_no_congela_stream():
    """El texto fluye antes del cierre del bloque JSON.

    Con el bug original, "Fin del mensaje." llegaba en el mismo flush
    final que el bloque JSON. Con el fix, llega en una llamada propia
    porque el buffering se canceló al detectar el `}`.
    """
    deltas = [
        "Aquí tienes un ejemplo:\n\n",
        '{\n',
        '    "nombre": "Marco"\n',
        '}\n\n',
        "Fin del mensaje.",
    ]
    calls = asyncio.run(_run_stream(deltas))

    # Comprobación 1: varias llamadas a on_text (no una sola al final).
    assert len(calls) >= 3, (
        f"Stream no fue incremental. Llamadas: {calls}"
    )

    # Comprobación 2: el bloque JSON y "Fin del mensaje" NO van juntos.
    ultima = calls[-1]
    assert "Marco" not in ultima, (
        f"El buffer no se canceló: el JSON llegó tarde. Última: {ultima!r}"
    )
    assert "Fin del mensaje" in ultima


def test_tool_call_textual_si_bufferiza():
    """Un JSON de tool call textual SÍ debe bufferizarse.

    El `"name"` en los primeros caracteres confirma el buffering y el
    bloque final NO se emite a on_text (porque es una tool call).
    """
    payload = json.dumps({
        "name": "crear_archivo",
        "arguments": {"path": "x.py", "content": "y"},
    })
    # Trocear en deltas de 10 chars para simular streaming.
    deltas = [payload[i:i+10] for i in range(0, len(payload), 10)]
    calls = asyncio.run(
        _run_stream(
            deltas,
            tools=[{"function": {"name": "crear_archivo"}}],
        )
    )
    # Ninguna llamada a on_text debe contener el JSON de la tool call.
    unido = "".join(calls)
    assert "crear_archivo" not in unido, (
        f"El JSON de tool call se filtró al usuario: {calls}"
    )
    assert '"name"' not in unido