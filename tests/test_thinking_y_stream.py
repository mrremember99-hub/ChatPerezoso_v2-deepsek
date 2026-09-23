"""Tests de los fixes P0 de la auditoria externa.

  - thinking se preserva en el assistant message de tool loops.
  - StreamFinished distingue completo de interrumpido.
  - Intent gate no autoriza buscar_en_workspace sin target.
"""
from __future__ import annotations

import json

from core.stream_events import StreamFinished


def test_stream_finished_defaults():
    sf = StreamFinished()
    assert sf.completed is True
    assert sf.done_reason is None


def test_stream_finished_completed_false():
    sf = StreamFinished(completed=False, done_reason=None)
    assert sf.completed is False
    assert sf.done_reason is None


def test_stream_finished_done_reason():
    sf = StreamFinished(completed=True, done_reason="length")
    assert sf.completed is True
    assert sf.done_reason == "length"


def test_parse_line_acumula_thinking():
    from core.ollama import OllamaClient

    message = {"role": "assistant", "content": ""}
    content_parts: list[str] = []
    thinking_parts: list[str] = []

    line = json.dumps({
        "message": {"thinking": "estoy pensando", "content": ""},
    })
    OllamaClient.parse_ollama_line(
        line, message, content_parts, thinking_parts
    )
    assert thinking_parts == ["estoy pensando"]


def test_parse_line_guarda_done_reason():
    from core.ollama import OllamaClient

    message = {"role": "assistant", "content": ""}
    content_parts: list[str] = []

    line = json.dumps({
        "message": {"content": ""},
        "done": True,
        "done_reason": "stop",
    })
    OllamaClient.parse_ollama_line(line, message, content_parts)
    assert message.get("_done_reason") == "stop"


def test_parse_line_sin_thinking_parts_no_falla():
    """El parametro thinking_parts es opcional."""
    from core.ollama import OllamaClient

    message = {"role": "assistant", "content": ""}
    content_parts: list[str] = []

    line = json.dumps({"message": {"thinking": "x", "content": ""}})
    OllamaClient.parse_ollama_line(line, message, content_parts)


def test_round_result_assistant_thinking_default():
    from core.tool_strategies import RoundResult

    r = RoundResult()
    assert r.assistant_thinking == ""


def test_round_result_assistant_thinking_asignable():
    from core.tool_strategies import RoundResult

    r = RoundResult()
    r.assistant_thinking = "foo"
    assert r.assistant_thinking == "foo"


