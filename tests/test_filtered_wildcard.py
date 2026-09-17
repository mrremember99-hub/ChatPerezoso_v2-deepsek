"""Test del comodín mcp__* en FilteredToolProvider."""
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
    def __init__(self, names: list[str]):
        self.names = names

    def definitions(self):
        return [_tool(n) for n in self.names]

    def intent_rules(self):
        return {n: IntentRule(verbs=(n,)) for n in self.names}

    def call(self, name, arguments, *, allow_destructive=False, cancel_event=None):
        return f"{name}:{arguments}"

    def requires_confirmation(self, name):
        return False


def test_wildcard_allows_all_mcp_tools():
    source = _Provider([
        "leer_archivo",
        "mcp__fs__read_text_file",
        "mcp__demo__saludar",
    ])
    filtered = FilteredToolProvider(source, {"leer_archivo", "mcp__*"})
    names = [d["function"]["name"] for d in filtered.definitions()]
    assert "leer_archivo" in names
    assert "mcp__fs__read_text_file" in names
    assert "mcp__demo__saludar" in names


def test_wildcard_excludes_non_mcp_tools():
    source = _Provider(["leer_archivo", "borrar_archivo", "mcp__x__y"])
    filtered = FilteredToolProvider(source, {"mcp__*"})
    names = [d["function"]["name"] for d in filtered.definitions()]
    assert names == ["mcp__x__y"]


def test_wildcard_call_forwards_mcp():
    source = _Provider(["mcp__x__y"])
    filtered = FilteredToolProvider(source, {"mcp__*"})
    result = filtered.call("mcp__x__y", {})
    assert result == "mcp__x__y:{}"


def test_wildcard_rejects_non_mcp_call():
    source = _Provider(["leer_archivo"])
    filtered = FilteredToolProvider(source, {"mcp__*"})
    result = filtered.call("leer_archivo", {})
    assert "no permitida" in result


def test_wildcard_with_explicit_tool():
    source = _Provider(["leer_archivo", "mcp__x__y", "borrar_archivo"])
    filtered = FilteredToolProvider(source, {"leer_archivo", "mcp__*"})
    names = sorted(d["function"]["name"] for d in filtered.definitions())
    assert names == ["leer_archivo", "mcp__x__y"]


def test_no_wildcard_means_only_explicit():
    source = _Provider(["mcp__x__y", "leer_archivo"])
    filtered = FilteredToolProvider(source, {"leer_archivo"})
    names = [d["function"]["name"] for d in filtered.definitions()]
    assert names == ["leer_archivo"]
