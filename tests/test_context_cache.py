"""Tests de la cache de costes de ContextWindow."""
from __future__ import annotations

from core.context_window import ContextWindow, RequestTokenCache


def test_cache_returns_same_value_for_same_message():
    w = ContextWindow(limit_tokens=8192)
    cache = RequestTokenCache()
    msg = {"role": "user", "content": "hola mundo"}
    t1 = cache.get_or_compute(w, msg)
    t2 = cache.get_or_compute(w, msg)
    assert t1 == t2
    assert t1 > 0


def test_cache_recomputes_when_content_changes():
    w = ContextWindow(limit_tokens=8192)
    cache = RequestTokenCache()
    msg = {"role": "user", "content": "corto"}
    t1 = cache.get_or_compute(w, msg)
    # Mutamos in-place: mismo dict, nuevo string.
    msg["content"] = "mucho más largo " * 100
    t2 = cache.get_or_compute(w, msg)
    assert t2 > t1


def test_cache_handles_tool_calls():
    w = ContextWindow(limit_tokens=8192)
    cache = RequestTokenCache()
    msg = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "x", "arguments": {"a": 1}}}],
    }
    t1 = cache.get_or_compute(w, msg)
    t2 = cache.get_or_compute(w, msg)
    assert t1 == t2
    assert t1 > 0


def test_cache_recomputes_when_tool_calls_change():
    w = ContextWindow(limit_tokens=8192)
    cache = RequestTokenCache()
    msg = {
        "role": "assistant",
        "content": "",
        "tool_calls": [{"function": {"name": "x", "arguments": {}}}],
    }
    t1 = cache.get_or_compute(w, msg)
    msg["tool_calls"] = [
        {"function": {"name": "x", "arguments": {"big": "y" * 500}}}
    ]
    t2 = cache.get_or_compute(w, msg)
    assert t2 > t1


def test_fit_with_cache_matches_fit_without_cache():
    """La cache no cambia el resultado, solo el coste de calcularlo."""
    messages = []
    for i in range(20):
        messages.append({"role": "user", "content": f"pregunta {i} " * 20})
        messages.append({
            "role": "assistant",
            "content": f"respuesta {i} " * 20,
        })

    w = ContextWindow(limit_tokens=2000, output_reserve=200, min_turns=2)
    pruned_a, budget_a = w.fit(
        system_prompt="eres un asistente",
        tool_definitions=[],
        messages=messages,
    )

    w2 = ContextWindow(limit_tokens=2000, output_reserve=200, min_turns=2)
    cache = RequestTokenCache()
    pruned_b, budget_b = w2.fit(
        system_prompt="eres un asistente",
        tool_definitions=[],
        messages=messages,
        cache=cache,
    )

    assert len(pruned_a) == len(pruned_b)
    assert budget_a.estimated_prompt == budget_b.estimated_prompt
    assert budget_a.dropped_messages == budget_b.dropped_messages
