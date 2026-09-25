"""Tests de integridad del stream (H1/H2/H5 de los informes)."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from core.ollama import (
    _INTERRUPTED_STREAM_MSG,
    _TRUNCATED_STREAM_SUFFIX,
)


# ── Constantes ──────────────────────────────────────────────────────────

def test_interrupted_msg_menciona_historial():
    assert "historial" in _INTERRUPTED_STREAM_MSG
    assert "Reintenta" in _INTERRUPTED_STREAM_MSG


def test_truncated_suffix_menciona_num_predict():
    assert "num_predict" in _TRUNCATED_STREAM_SUFFIX
    assert "truncada" in _TRUNCATED_STREAM_SUFFIX.lower()


# ── RoundResult: campos nuevos ──────────────────────────────────────────

def test_roundresult_por_defecto_completed_true():
    from core.tool_strategies import RoundResult
    r = RoundResult(is_final=True, final_text="hola")
    assert r.completed is True
    assert r.done_reason is None


def test_roundresult_acepta_completed_false():
    from core.tool_strategies import RoundResult
    r = RoundResult(is_final=True, final_text="hola", completed=False)
    assert r.completed is False


def test_roundresult_acepta_done_reason_length():
    from core.tool_strategies import RoundResult
    r = RoundResult(is_final=True, final_text="x", done_reason="length")
    assert r.done_reason == "length"


# ── Integracion con chat() ──────────────────────────────────────────────

@pytest.fixture(autouse=True)
def _register_core_rules(tmp_path):
    """Registra las reglas del nucleo para que el gate autorice las tools.

    Mismo patron que test_ollama.py: sin esto, el ToolIntentGate
    bloquea listar_carpeta y los tests de tool_calls no llegan a la
    ejecucion.
    """
    from core.intent import ToolIntentGate
    from core.tools import ToolRegistry
    from core.workspace import Workspace

    rules = ToolRegistry(Workspace(tmp_path)).intent_rules()
    ToolIntentGate.register_rules(rules)


def test_chat_rechaza_stream_interrumpido_sin_tool_calls(monkeypatch):
    """H1: EOF antes de done=true. chat() lanza OllamaError."""
    from core.ollama import OllamaClient, OllamaError

    client = OllamaClient()

    def fake_stream(model, messages, tools, on_text, cancel_event=None, options=None):
        on_text("Texto parcial")
        return {
            "role": "assistant",
            "content": "Texto parcial",
            "_stream_completed": False,
            "_stream_done_reason": None,
        }

    monkeypatch.setattr(client, "_stream", fake_stream)
    with pytest.raises(OllamaError, match="cerro antes"):
        client.chat(
            "test-model",
            [{"role": "user", "content": "Hola"}],
            None,
            lambda _: None,
            lambda *_: "",
        )


def test_chat_rechaza_stream_interrumpido_con_tool_calls(monkeypatch):
    """H5: tool_calls + EOF. No deben ejecutarse."""
    from core.ollama import OllamaClient, OllamaError

    client = OllamaClient()
    calls = []

    def fake_stream(model, messages, tools, on_text, cancel_event=None, options=None):
        return {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "listar_carpeta", "arguments": {"path": "."}}}
            ],
            "_stream_completed": False,
            "_stream_done_reason": None,
        }

    monkeypatch.setattr(client, "_stream", fake_stream)
    with pytest.raises(OllamaError, match="cerro antes"):
        client.chat(
            "test-model",
            [{"role": "user", "content": "Lista"}],
            [{"type": "function", "function": {"name": "listar_carpeta"}}],
            lambda _: None,
            lambda name, args: calls.append((name, args)) or "ok",
        )
    assert calls == []


def test_chat_marca_truncamiento_por_length(monkeypatch):
    """H2: done_reason=length. El sufijo se anade al texto final."""
    from core.ollama import OllamaClient, _TRUNCATED_STREAM_SUFFIX

    client = OllamaClient()

    def fake_stream(model, messages, tools, on_text, cancel_event=None, options=None):
        on_text("Respuesta corta")
        return {
            "role": "assistant",
            "content": "Respuesta corta",
            "_stream_completed": True,
            "_stream_done_reason": "length",
        }

    monkeypatch.setattr(client, "_stream", fake_stream)
    result = client.chat(
        "test-model",
        [{"role": "user", "content": "Hola"}],
        None,
        lambda _: None,
        lambda *_: "",
    )
    assert _TRUNCATED_STREAM_SUFFIX in result


def test_chat_acepta_stream_completo(monkeypatch):
    """Control: done=true + done_reason=stop. Sin cambios."""
    from core.ollama import OllamaClient

    client = OllamaClient()

    def fake_stream(model, messages, tools, on_text, cancel_event=None, options=None):
        on_text("Hola")
        return {
            "role": "assistant",
            "content": "Hola",
            "_stream_completed": True,
            "_stream_done_reason": "stop",
        }

    monkeypatch.setattr(client, "_stream", fake_stream)
    result = client.chat(
        "test-model",
        [{"role": "user", "content": "Hola"}],
        None,
        lambda _: None,
        lambda *_: "",
    )
    assert result == "Hola"
