"""Tests del cache de CompositeToolProvider (parche H-6)."""
from __future__ import annotations

from typing import Any

from core.composite_tools import CompositeToolProvider
from core.intent import IntentRule


def _tool(name: str) -> dict[str, Any]:
    return {
        "type": "function",
        "function": {
            "name": name,
            "description": "",
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }


class _Provider:
    def __init__(self, names: list[str]):
        self.names = list(names)
        self.definitions_calls = 0

    def definitions(self):
        self.definitions_calls += 1
        return [_tool(n) for n in self.names]

    def intent_rules(self):
        return {n: IntentRule(verbs=(n,)) for n in self.names}

    def call(self, name, arguments, *, allow_destructive=False, cancel_event=None):
        return ""

    def requires_confirmation(self, name):
        return False


def test_definitions_cached_across_calls():
    provider = _Provider(["a", "b"])
    composite = CompositeToolProvider([provider])

    composite.definitions()
    composite.definitions()
    composite.definitions()

    # definitions() del provider subyacente solo se llamó una vez.
    assert provider.definitions_calls == 1


def test_invalidate_forces_recompute():
    provider = _Provider(["a", "b"])
    composite = CompositeToolProvider([provider])

    composite.definitions()
    composite.invalidate()
    composite.definitions()

    assert provider.definitions_calls == 2


def test_invalidate_picks_up_new_tools():
    provider = _Provider(["a"])
    composite = CompositeToolProvider([provider])

    first = composite.definitions()
    assert [d["function"]["name"] for d in first] == ["a"]

    # Añadir un tool al provider y invalidar.
    provider.names.append("b")
    composite.invalidate()

    second = composite.definitions()
    assert [d["function"]["name"] for d in second] == ["a", "b"]


def test_intent_rules_cached_across_calls():
    provider = _Provider(["a", "b"])
    composite = CompositeToolProvider([provider])

    r1 = composite.intent_rules()
    r2 = composite.intent_rules()

    # Mismo objeto (cache hit).
    assert r1 is r2


def test_intent_rules_invalidated():
    provider = _Provider(["a"])
    composite = CompositeToolProvider([provider])

    r1 = composite.intent_rules()
    provider.names.append("b")
    composite.invalidate()
    r2 = composite.intent_rules()

    assert set(r1) == {"a"}
    assert set(r2) == {"a", "b"}
