from __future__ import annotations

from typing import Any

from core.composite_tools import FilteredToolProvider
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
    def __init__(self, names: list[str], rules: dict[str, IntentRule] | None = None):
        self.names = names
        self.rules = rules or {n: IntentRule(verbs=(n,)) for n in names}
        self.calls: list[tuple[str, dict]] = []

    def definitions(self):
        return [_tool(n) for n in self.names]

    def intent_rules(self):
        return dict(self.rules)

    def call(self, name, arguments, *, allow_destructive=False, cancel_event=None):
        self.calls.append((name, arguments))
        return f"{name}:{arguments}"

    def requires_confirmation(self, name):
        return False


# -- FilteredToolProvider ----------------------------------------------------

def test_filtered_exposes_only_allowed():
    source = _Provider(["a", "b", "c"])
    filtered = FilteredToolProvider(source, {"a", "c"})
    names = [d["function"]["name"] for d in filtered.definitions()]
    assert names == ["a", "c"]


def test_filtered_empty_allowed_exposes_nothing():
    source = _Provider(["a", "b"])
    filtered = FilteredToolProvider(source, set())
    assert filtered.definitions() == []


def test_filtered_call_rejects_unallowed():
    source = _Provider(["a", "b"])
    filtered = FilteredToolProvider(source, {"a"})
    result = filtered.call("b", {})
    assert "no permitida" in result
    assert source.calls == []


def test_filtered_call_forwards_allowed():
    source = _Provider(["a", "b"])
    filtered = FilteredToolProvider(source, {"a"})
    filtered.call("a", {"x": 1})
    assert source.calls == [("a", {"x": 1})]


def test_filtered_intent_rules_only_allowed():
    source = _Provider(["a", "b", "c"])
    filtered = FilteredToolProvider(source, {"a", "c"})
    rules = filtered.intent_rules()
    assert set(rules) == {"a", "c"}


def test_filtered_requires_confirmation_respects_filter():
    source = _Provider(["a", "b"])
    filtered = FilteredToolProvider(source, {"a"})
    assert not filtered.requires_confirmation("b")
