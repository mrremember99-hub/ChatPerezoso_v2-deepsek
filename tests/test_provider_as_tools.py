"""Regresión: chat() debe aceptar tanto listas como ToolProvider.

El bug original: cuando chat() recibía un provider en lugar de una lista,
`_mentions_mcp_tool` iteraba sobre el provider y lanzaba TypeError en
cuanto la petición no era una "workspace operation".
"""
from __future__ import annotations

from typing import Any


from core.intent import IntentRule
from core.ollama import OllamaClient


class _FakeProvider:
    """Provider mínimo con la interfaz que chat() espera."""

    def __init__(self, names: list[str]):
        self.names = names

    def definitions(self) -> list[dict[str, Any]]:
        return [
            {
                "type": "function",
                "function": {
                    "name": n,
                    "description": "",
                    "parameters": {"type": "object", "properties": {}, "required": []},
                },
            }
            for n in self.names
        ]

    def intent_rules(self) -> dict[str, IntentRule]:
        return {n: IntentRule(verbs=(n,)) for n in self.names}

    def call(self, *a, **k):
        return "ok"

    def requires_confirmation(self, *a, **k):
        return False


# -- _extract_definitions ----------------------------------------------------

def test_extract_definitions_from_provider():
    provider = _FakeProvider(["uno"])
    result = OllamaClient._extract_definitions(provider)
    assert result is not None
    assert [d["function"]["name"] for d in result] == ["uno"]


def test_extract_definitions_from_list():
    definitions = [{"type": "function", "function": {"name": "uno"}}]
    result = OllamaClient._extract_definitions(definitions)
    assert result == definitions


def test_extract_definitions_none():
    assert OllamaClient._extract_definitions(None) is None


def test_extract_definitions_empty_list():
    assert OllamaClient._extract_definitions([]) is None


def test_extract_definitions_empty_provider():
    provider = _FakeProvider([])
    assert OllamaClient._extract_definitions(provider) is None


# -- chat() con provider (la regresión) -------------------------------------

def test_chat_accepts_provider_for_mcp_request(monkeypatch):
    """El bug original crasheaba aquí: petición MCP + provider en lugar de
    lista. Debe responder sin TypeError."""
    client = OllamaClient()
    provider = _FakeProvider(["mcp__demo__saludar"])
    seen_tools: list = []

    def fake_stream(model, messages, tools, on_text, cancel_event=None,
                    options=None):
        seen_tools.append(tools)
        return {"role": "assistant", "content": "listo"}

    monkeypatch.setattr(client, "_stream", fake_stream)
    result = client.chat(
        "test-model",
        [{"role": "user", "content": "usa mcp__demo__saludar"}],
        provider,
        lambda _t: None,
        lambda _n, _a: "ok",
    )
    assert result == "listo"
    assert seen_tools  # el stream recibió las definiciones


def test_chat_accepts_provider_for_workspace_request(monkeypatch):
    """La ruta que antes "funcionaba por casualidad" debe seguir funcionando."""
    client = OllamaClient()
    provider = _FakeProvider(["listar_carpeta"])

    def fake_stream(*a, **k):
        return {"role": "assistant", "content": "ok"}

    monkeypatch.setattr(client, "_stream", fake_stream)
    result = client.chat(
        "test-model",
        [{"role": "user", "content": "lista la carpeta"}],
        provider,
        lambda _t: None,
        lambda _n, _a: "ok",
    )
    assert result == "ok"


def test_chat_still_accepts_plain_list(monkeypatch):
    """Compatibilidad con la API antigua."""
    client = OllamaClient()

    def fake_stream(*a, **k):
        return {"role": "assistant", "content": "ok"}

    monkeypatch.setattr(client, "_stream", fake_stream)
    result = client.chat(
        "test-model",
        [{"role": "user", "content": "lista la carpeta"}],
        [{"type": "function", "function": {"name": "listar_carpeta"}}],
        lambda _t: None,
        lambda _n, _a: "ok",
    )
    assert result == "ok"
