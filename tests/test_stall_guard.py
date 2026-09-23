"""Tests del stall guard y del fallback XML.

Cubre:
  · Detección de "modelo responde sin tool calls" cuando el usuario
    pidió verificación explícita.
  · Inyección de nudge y reintento (una sola vez).
  · Marcado del modelo para forzar XML en la próxima llamada.
  · _choose_strategy devuelve XmlToolStrategy para modelos forzados.
"""
from __future__ import annotations

import pytest

from core.intent import IntentRule
from core.model_capabilities import ModelCapabilities
from core.ollama import OllamaClient
from core.tool_strategies import NativeToolStrategy, XmlToolStrategy


@pytest.fixture(autouse=True)
def _reset_caches(monkeypatch):
    from core import model_capabilities

    model_capabilities.clear_cache()

    import httpx

    class _Resp:
        def raise_for_status(self):
            return None

        def json(self):
            return {"capabilities": ["tools"]}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _Resp())
    yield
    model_capabilities.clear_cache()


class _FakeTools:
    """Provider mínimo para tests del stall guard."""

    def definitions(self):
        return [{
            "type": "function",
            "function": {
                "name": "listar_carpeta",
                "description": "",
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
                verbs=("lista", "listar", "comprueba"),
                target_words=("carpeta", "workspace"),
            ),
        }

    def call(self, name, arguments, *, allow_destructive=False,
             cancel_event=None):
        return "(sin salida)"

    def requires_confirmation(self, name):
        return False


# ── _choose_strategy ────────────────────────────────────────────────

def test_choose_strategy_native_by_default():
    client = OllamaClient()
    caps = ModelCapabilities(name="m", native_tools=True, probed=True)
    strategy = client._choose_strategy(caps, None, "m")
    assert isinstance(strategy, NativeToolStrategy)


def test_choose_strategy_xml_when_forced():
    client = OllamaClient()
    client._force_xml_models.add("m")
    caps = ModelCapabilities(name="m", native_tools=True, probed=True)
    strategy = client._choose_strategy(caps, None, "m")
    assert isinstance(strategy, XmlToolStrategy)


def test_choose_strategy_xml_when_capabilities_say_xml():
    client = OllamaClient()
    caps = ModelCapabilities(name="m", native_tools=False, probed=True)
    strategy = client._choose_strategy(caps, None, "m")
    assert isinstance(strategy, XmlToolStrategy)


# ── _user_requested_verification ─────────────────────────────────────

@pytest.mark.parametrize("text,expected", [
    ("ejecuta py_compile gui.py", True),
    ("verifica el archivo", True),
    ("cita el output literal", True),
    ("run the test", True),
    ("check this", True),
    ("¿qué es X?", False),
    ("explícame esto", False),
    ("", False),
])
def test_user_requested_verification(text, expected):
    assert OllamaClient._user_requested_verification(text) is expected


# ── Stall guard end-to-end ───────────────────────────────────────────

def test_stall_detected_nudge_injected_then_success(monkeypatch):
    """Round 1 sin tool calls → nudge → Round 2 con tool calls."""
    client = OllamaClient()
    histories: list[list[dict]] = []
    responses = iter([
        {"role": "assistant",
         "content": "FASE VERIFICADA · salida: (sin salida)"},
        {"role": "assistant", "content": "", "tool_calls": [
            {"function": {
                "name": "listar_carpeta",
                "arguments": {"path": "."},
            }},
        ]},
        {"role": "assistant", "content": "Completado."},
    ])

    def fake_stream(model, messages, tools, on_text,
                    cancel_event=None, options=None):
        histories.append([dict(m) for m in messages])
        return next(responses)

    monkeypatch.setattr(client, "_stream", fake_stream)

    result = client.chat(
        "test-model",
        [{"role": "user",
          "content": "lista la carpeta y verifica con py_compile"}],
        _FakeTools(),
        lambda _: None,
        lambda *_: "(sin salida)",
    )

    assert result == "Completado."
    assert len(histories) == 3
    nudge = [
        m for m in histories[1]
        if m.get("role") == "user"
        and "No has emitido" in str(m.get("content", ""))
    ]
    assert nudge, "nudge no encontrado en la segunda ronda"
    assert "test-model" not in client._force_xml_models


def test_no_stall_without_verification_request(monkeypatch):
    """Prompt sin verificación → no nudge aunque el modelo no use tools."""
    client = OllamaClient()
    histories: list[list[dict]] = []

    def fake_stream(model, messages, tools, on_text,
                    cancel_event=None, options=None):
        histories.append([dict(m) for m in messages])
        return {"role": "assistant", "content": "La carpeta está vacía."}

    monkeypatch.setattr(client, "_stream", fake_stream)

    result = client.chat(
        "test-model",
        [{"role": "user", "content": "lista la carpeta"}],
        _FakeTools(),
        lambda _: None,
        lambda *_: "",
    )

    assert result == "La carpeta está vacía."
    assert len(histories) == 1
    assert "test-model" not in client._force_xml_models


def test_model_marked_for_xml_after_max_retries(monkeypatch):
    """Si tras el nudge sigue sin ejecutar, marcar el modelo."""
    client = OllamaClient()
    histories: list[list[dict]] = []

    def fake_stream(model, messages, tools, on_text,
                    cancel_event=None, options=None):
        histories.append([dict(m) for m in messages])
        return {"role": "assistant",
                "content": "FASE VERIFICADA · salida: (sin salida)"}

    monkeypatch.setattr(client, "_stream", fake_stream)

    result = client.chat(
        "test-model",
        [{"role": "user",
          "content": "lista la carpeta y verifica con py_compile"}],
        _FakeTools(),
        lambda _: None,
        lambda *_: "",
    )

    assert "FASE VERIFICADA" in result
    assert len(histories) == 2
    assert "test-model" in client._force_xml_models


# ── reset_forced_xml ─────────────────────────────────────────────────

def test_reset_forced_xml_specific_model():
    client = OllamaClient()
    client._force_xml_models.add("m1")
    client._force_xml_models.add("m2")
    client.reset_forced_xml("m1")
    assert "m1" not in client._force_xml_models
    assert "m2" in client._force_xml_models


def test_reset_forced_xml_all():
    client = OllamaClient()
    client._force_xml_models.add("m1")
    client._force_xml_models.add("m2")
    client.reset_forced_xml()
    assert not client._force_xml_models