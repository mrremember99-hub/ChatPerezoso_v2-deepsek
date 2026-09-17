"""Tests del caché de resultados de herramientas."""
from __future__ import annotations

import time

from core.composite_tools import CachedToolProvider
from core.tool_cache import ToolCache


class _FakeProvider:
    def __init__(self):
        self.call_count = 0

    def definitions(self):
        return [{"type": "function", "function": {"name": "x"}}]

    def intent_rules(self):
        return {}

    def requires_confirmation(self, name):
        return False

    def call(self, name, arguments, *, allow_destructive=False, cancel_event=None):
        self.call_count += 1
        return f"result-{self.call_count}"


# ── ToolCache ───────────────────────────────────────────────────────

def test_cache_hit_and_miss():
    c = ToolCache(default_ttl=10.0)
    c.put("x", {"a": 1}, "result")
    assert c.get("x", {"a": 1}) == "result"
    assert c.get("x", {"a": 2}) is None
    assert c.hits == 1
    assert c.misses == 1


def test_cache_argument_order_does_not_matter():
    c = ToolCache(default_ttl=10.0)
    c.put("x", {"a": 1, "b": 2}, "result")
    assert c.get("x", {"b": 2, "a": 1}) == "result"


def test_cache_expires_after_ttl():
    c = ToolCache(default_ttl=0.05)
    c.put("x", {}, "result")
    assert c.get("x", {}) == "result"
    time.sleep(0.1)
    assert c.get("x", {}) is None


def test_cache_per_tool_ttl_override():
    c = ToolCache(default_ttl=10.0)
    c.set_ttl("lenta", 0.05)
    c.put("lenta", {}, "result")
    time.sleep(0.1)
    assert c.get("lenta", {}) is None


def test_cache_invalidate_all():
    c = ToolCache(default_ttl=10.0)
    c.put("x", {}, "a")
    c.put("y", {}, "b")
    c.invalidate_all()
    assert c.size == 0


def test_cache_invalidate_by_name():
    c = ToolCache(default_ttl=10.0)
    c.put("x", {}, "a")
    c.put("y", {}, "b")
    c.invalidate("x")
    assert c.get("x", {}) is None
    assert c.get("y", {}) == "b"


def test_cache_nested_arguments():
    c = ToolCache(default_ttl=10.0)
    args = {"list": [1, 2, {"k": "v"}], "nested": {"a": [3, 4]}}
    c.put("x", args, "result")
    assert c.get("x", args) == "result"
    # Cambio en un valor anidado -> miss
    assert c.get("x", {"list": [1, 2, {"k": "otro"}], "nested": {"a": [3, 4]}}) is None


# ── CachedToolProvider ──────────────────────────────────────────────

def test_provider_caches_cacheable_tool():
    inner = _FakeProvider()
    p = CachedToolProvider(inner, cacheable={"x"})
    p.call("x", {})
    p.call("x", {})
    p.call("x", {})
    assert inner.call_count == 1


def test_provider_does_not_cache_non_cacheable_tool():
    inner = _FakeProvider()
    p = CachedToolProvider(inner, cacheable={"x"})
    p.call("y", {})
    p.call("y", {})
    p.call("y", {})
    assert inner.call_count == 3


def test_provider_invalidates_on_mutating_tool():
    inner = _FakeProvider()
    p = CachedToolProvider(inner, cacheable={"read"}, invalidating={"write"})
    p.call("read", {})
    p.call("read", {})
    assert inner.call_count == 1
    p.call("write", {})
    p.call("read", {})
    assert inner.call_count == 3  # read cached 1, write 1, read re-ejecutado


def test_provider_does_not_cache_errors():
    class _ErrorProvider(_FakeProvider):
        def call(self, name, arguments, *, allow_destructive=False, cancel_event=None):
            self.call_count += 1
            return "ERROR: fallo"

    inner = _ErrorProvider()
    p = CachedToolProvider(inner, cacheable={"x"})
    p.call("x", {})
    p.call("x", {})
    assert inner.call_count == 2  # ambos intentos, no se cachea el error


def test_provider_definitions_delegated():
    inner = _FakeProvider()
    p = CachedToolProvider(inner)
    assert p.definitions() == inner.definitions()


def test_provider_requires_confirmation_delegated():
    class _ConfirmProvider(_FakeProvider):
        def requires_confirmation(self, name):
            return name == "danger"

    inner = _ConfirmProvider()
    p = CachedToolProvider(inner)
    assert p.requires_confirmation("danger") is True
    assert p.requires_confirmation("safe") is False
