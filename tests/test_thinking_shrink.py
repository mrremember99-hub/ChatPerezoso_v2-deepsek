"""Tests del truncado de thinking (Hueco 5)."""
from __future__ import annotations

from core.ollama import (
    _THINKING_KEEP_HEAD,
    _THINKING_KEEP_TAIL,
    _THINKING_MAX_CHARS,
    _shrink_thinking,
)


def test_thinking_corto_no_se_toca():
    text = "Pensando en el problema..."
    assert _shrink_thinking(text) == text


def test_thinking_vacio():
    assert _shrink_thinking("") == ""
    assert _shrink_thinking(None) == ""


def test_thinking_justo_en_el_limite():
    text = "x" * _THINKING_MAX_CHARS
    assert _shrink_thinking(text) == text


def test_thinking_largo_se_trunca():
    text = "HEAD" + ("x" * 10000) + "TAIL"
    result = _shrink_thinking(text)
    assert len(result) < len(text)
    assert result.startswith("H")
    assert result.endswith("L")
    assert "omitidos" in result


def test_thinking_preserva_head_y_tail():
    head = "A" * _THINKING_KEEP_HEAD
    middle = "B" * 5000
    tail = "C" * _THINKING_KEEP_TAIL
    result = _shrink_thinking(head + middle + tail)
    assert result.startswith(head)
    assert result.endswith(tail)


def test_thinking_marcador_menciona_omitidos():
    text = "x" * 20000
    result = _shrink_thinking(text)
    expected_omitted = 20000 - _THINKING_KEEP_HEAD - _THINKING_KEEP_TAIL
    assert str(expected_omitted) in result
