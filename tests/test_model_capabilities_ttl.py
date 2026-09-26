"""Tests del TTL de la caché de capabilities (N4).

Verifican que:
- Dentro del TTL no se re-probea.
- Pasado el TTL sí se re-probea.
- clear_cache() sigue limpiando todo.
"""
from __future__ import annotations

import pytest

import core.model_capabilities as mc
from core.model_capabilities import clear_cache, get_capabilities


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_cache()
    yield
    clear_cache()


class _FakeResponse:
    def raise_for_status(self):
        return None

    def json(self):
        return {"capabilities": ["tools"]}


def _patch_post(monkeypatch, calls):
    import httpx

    def fake_post(url, json=None, timeout=None):
        calls.append({"url": url, "json": json})
        return _FakeResponse()

    monkeypatch.setattr(httpx, "post", fake_post)


class _Clock:
    """Reloj controlable, sustituye time.monotonic dentro del módulo."""
    def __init__(self):
        self.now = 0.0

    def monotonic(self):
        return self.now


def test_cache_hit_dentro_del_ttl(monkeypatch):
    calls: list = []
    _patch_post(monkeypatch, calls)
    clock = _Clock()
    monkeypatch.setattr(mc, "time", clock)

    get_capabilities("http://localhost:11434", "llama3.1")
    clock.now += 30.0  # dentro del TTL (60s)
    get_capabilities("http://localhost:11434", "llama3.1")
    clock.now += 20.0  # aún dentro (total 50s)
    get_capabilities("http://localhost:11434", "llama3.1")

    assert len(calls) == 1


def test_cache_miss_tras_ttl(monkeypatch):
    calls: list = []
    _patch_post(monkeypatch, calls)
    clock = _Clock()
    monkeypatch.setattr(mc, "time", clock)

    get_capabilities("http://localhost:11434", "llama3.1")
    clock.now += 61.0  # supera el TTL
    get_capabilities("http://localhost:11434", "llama3.1")

    assert len(calls) == 2


def test_ttl_se_renueva_tras_refresh(monkeypatch):
    """Tras un re-probe por TTL, el nuevo timestamp cuenta desde ese momento."""
    calls: list = []
    _patch_post(monkeypatch, calls)
    clock = _Clock()
    monkeypatch.setattr(mc, "time", clock)

    get_capabilities("http://localhost:11434", "llama3.1")  # t=0
    clock.now = 61.0
    get_capabilities("http://localhost:11434", "llama3.1")  # re-probe
    clock.now = 100.0  # 39s desde el re-probe
    get_capabilities("http://localhost:11434", "llama3.1")  # cache hit

    assert len(calls) == 2


def test_force_refresh_ignora_ttl(monkeypatch):
    calls: list = []
    _patch_post(monkeypatch, calls)
    clock = _Clock()
    monkeypatch.setattr(mc, "time", clock)

    get_capabilities("http://localhost:11434", "llama3.1")
    get_capabilities("http://localhost:11434", "llama3.1", force_refresh=True)

    assert len(calls) == 2


def test_clear_cache_limpia_timestamps(monkeypatch):
    calls: list = []
    _patch_post(monkeypatch, calls)
    clock = _Clock()
    monkeypatch.setattr(mc, "time", clock)

    get_capabilities("http://localhost:11434", "llama3.1")
    clear_cache()
    get_capabilities("http://localhost:11434", "llama3.1")

    assert len(calls) == 2
