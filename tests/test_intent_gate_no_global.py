"""Regresión C1: _build_intent_gate no contamina el registro global.

Antes, `OllamaClient._build_intent_gate` llamaba a
`ToolIntentGate.register_rules(rules)`. Eso escribía al diccionario
global `_RULES_REGISTRY` en cada chat(), contradiciendo la sección H5
de la auditoría (que decía que el registro ya no se usaba en producción).

Ahora:
  · Provider sano → gate con self.rules, sin tocar el global.
  · Provider roto → gate vacío (fail-closed).
  · Lista suelta (tests) → registro global (compat path).
"""
from __future__ import annotations

from core.intent import IntentRule, ToolIntentGate
from core.ollama import OllamaClient


class _FakeProvider:
    def __init__(self, rules: dict):
        self._rules = rules

    def intent_rules(self):
        return self._rules


class _BrokenProvider:
    """Devuelve algo que no es dict (provider bug)."""

    def intent_rules(self):
        return "no soy un dict"


def test_build_intent_gate_with_provider_does_not_touch_registry():
    """Un provider sano NO debe escribir al registro global."""
    # Limpiar el registro para la prueba.
    ToolIntentGate._RULES_REGISTRY.clear()

    rules = {
        "listar_carpeta": IntentRule(verbs=("lista",)),
        "leer_archivo": IntentRule(verbs=("lee",)),
    }
    provider = _FakeProvider(rules)
    gate = OllamaClient._build_intent_gate(provider)

    # El gate tiene las reglas del provider.
    assert set(gate.rules) == {"listar_carpeta", "leer_archivo"}

    # Y el registro global sigue VACÍO.
    assert ToolIntentGate._RULES_REGISTRY == {}, (
        f"el registro fue contaminado: {ToolIntentGate._RULES_REGISTRY}"
    )


def test_build_intent_gate_with_broken_provider_fails_closed():
    """Un provider con intent_rules() malformado devuelve gate vacío."""
    ToolIntentGate._RULES_REGISTRY.clear()

    broken = _BrokenProvider()
    gate = OllamaClient._build_intent_gate(broken)

    # Fail-closed: sin reglas.
    assert gate.rules == {}


def test_build_intent_gate_with_none_returns_empty():
    ToolIntentGate._RULES_REGISTRY.clear()
    gate = OllamaClient._build_intent_gate(None)
    assert gate.rules == {}


def test_build_intent_gate_with_list_uses_registry():
    """Path de compatibilidad con tests (listas sueltas)."""
    ToolIntentGate._RULES_REGISTRY.clear()
    ToolIntentGate.register_rules({
        "listar_carpeta": IntentRule(verbs=("lista",)),
    })

    # Lista de diccionarios, no un provider.
    tools = [{"type": "function", "function": {"name": "listar_carpeta"}}]
    gate = OllamaClient._build_intent_gate(tools)

    # El gate lee del registro global (compat path).
    assert "listar_carpeta" in gate.rules

    ToolIntentGate._RULES_REGISTRY.clear()