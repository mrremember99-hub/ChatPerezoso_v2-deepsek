"""Tests para detección de capacidades vía /api/show."""
from __future__ import annotations

import pytest

from core.model_capabilities import clear_cache, get_capabilities


@pytest.fixture(autouse=True)
def _reset_cache():
    clear_cache()
    yield
    clear_cache()


class _FakeResponse:
    def __init__(self, data):
        self._data = data

    def raise_for_status(self):
        return None

    def json(self):
        return self._data


def _patch_post(monkeypatch, *, data=None, raise_exc=None, calls=None):
    import httpx

    def fake_post(url, json=None, timeout=None):
        if calls is not None:
            calls.append({"url": url, "json": json})
        if raise_exc is not None:
            raise raise_exc
        return _FakeResponse(data)

    monkeypatch.setattr(httpx, "post", fake_post)


def test_capabilities_native_tools(monkeypatch):
    _patch_post(monkeypatch, data={"capabilities": ["tools", "completion"]})
    caps = get_capabilities("http://localhost:11434", "llama3.1")
    assert caps.native_tools is True
    assert caps.probed is True
    assert caps.tool_mode == "native"


def test_capabilities_without_tools_falls_to_xml(monkeypatch):
    _patch_post(monkeypatch, data={"capabilities": ["completion"]})
    caps = get_capabilities("http://localhost:11434", "deepseek-r1")
    assert caps.native_tools is False
    assert caps.probed is True
    assert caps.tool_mode == "xml"


def test_capabilities_no_field_assumes_native(monkeypatch):
    """Ollama viejo sin `capabilities`: asumimos native para compatibilidad."""
    _patch_post(monkeypatch, data={"template": "..."})
    caps = get_capabilities("http://localhost:11434", "modelo-viejo")
    assert caps.native_tools is True
    assert caps.probed is False
    assert caps.tool_mode == "unknown"


def test_capabilities_http_error_falls_back_to_native(monkeypatch):
    import httpx

    # httpx.ConnectError (y otras subclases de HTTPError) requieren un
    # `request` como segundo argumento en versiones recientes de httpx.
    # Usamos HTTPError genérico, que la ruta de _probe captura igual.
    request = httpx.Request("POST", "http://localhost:11434/api/show")
    _patch_post(
        monkeypatch,
        raise_exc=httpx.ConnectError("no server", request=request),
    )
    caps = get_capabilities("http://localhost:11434", "modelo")
    assert caps.native_tools is True
    assert caps.probed is False


def test_capabilities_cached_across_calls(monkeypatch):
    calls = []
    _patch_post(monkeypatch, data={"capabilities": ["tools"]}, calls=calls)
    get_capabilities("http://localhost:11434", "llama3.1")
    get_capabilities("http://localhost:11434", "llama3.1")
    get_capabilities("http://localhost:11434", "llama3.1")
    assert len(calls) == 1


def test_capabilities_force_refresh_bypasses_cache(monkeypatch):
    calls = []
    _patch_post(monkeypatch, data={"capabilities": ["tools"]}, calls=calls)
    get_capabilities("http://localhost:11434", "llama3.1")
    get_capabilities("http://localhost:11434", "llama3.1", force_refresh=True)
    assert len(calls) == 2


def test_capabilities_empty_model_returns_unknown():
    caps = get_capabilities("http://localhost:11434", "")
    assert caps.native_tools is False
    assert caps.probed is False
    assert caps.tool_mode == "unknown"


def test_capabilities_different_models_not_shared_in_cache(monkeypatch):
    calls = []
    _patch_post(monkeypatch, data={"capabilities": ["tools"]}, calls=calls)
    get_capabilities("http://localhost:11434", "modelo-a")
    get_capabilities("http://localhost:11434", "modelo-b")
    assert len(calls) == 2
