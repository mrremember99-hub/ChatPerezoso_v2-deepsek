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


# ── context_length ─────────────────────────────────────────────────────

def test_context_length_extracted_from_llama_arch(monkeypatch):
    """model_info con clave 'llama.context_length' se extrae."""
    _patch_post(monkeypatch, data={
        "capabilities": ["tools"],
        "model_info": {"llama.context_length": 131072},
    })
    caps = get_capabilities("http://localhost:11434", "llama3.1")
    assert caps.context_length == 131072


def test_context_length_extracted_from_qwen_arch(monkeypatch):
    """Otras arquitecturas usan prefijos distintos."""
    _patch_post(monkeypatch, data={
        "capabilities": ["tools"],
        "model_info": {"qwen2.context_length": 32768},
    })
    caps = get_capabilities("http://localhost:11434", "qwen2.5")
    assert caps.context_length == 32768


def test_context_length_accepts_flat_key(monkeypatch):
    """Algunas versiones de Ollama exponen 'context_length' sin prefijo."""
    _patch_post(monkeypatch, data={
        "capabilities": ["tools"],
        "model_info": {"context_length": 8192},
    })
    caps = get_capabilities("http://localhost:11434", "modelo")
    assert caps.context_length == 8192


def test_context_length_unknown_returns_zero(monkeypatch):
    """Sin model_info o sin la clave, context_length es 0 (desconocido)."""
    _patch_post(monkeypatch, data={"capabilities": ["tools"]})
    caps = get_capabilities("http://localhost:11434", "modelo")
    assert caps.context_length == 0


def test_context_length_ignores_invalid_values(monkeypatch):
    """Valores no numéricos o negativos se ignoran."""
    for valor in [None, "muchos", -1, 0, True]:
        _patch_post(monkeypatch, data={
            "capabilities": ["tools"],
            "model_info": {"llama.context_length": valor},
        })
        clear_cache()
        caps = get_capabilities("http://localhost:11434", "modelo")
        assert caps.context_length == 0, f"Valor inválido {valor!r} aceptado"


def test_context_length_from_float(monkeypatch):
    """Algunos modelos exponen el valor como float (131072.0)."""
    _patch_post(monkeypatch, data={
        "capabilities": ["tools"],
        "model_info": {"llama.context_length": 131072.0},
    })
    caps = get_capabilities("http://localhost:11434", "modelo")
    assert caps.context_length == 131072
    assert isinstance(caps.context_length, int)


def test_context_length_on_fallback_error(monkeypatch):
    """Si /api/show falla, context_length es 0 (desconocido)."""
    import httpx
    request = httpx.Request("POST", "http://localhost:11434/api/show")
    _patch_post(
        monkeypatch,
        raise_exc=httpx.ConnectError("no server", request=request),
    )
    caps = get_capabilities("http://localhost:11434", "modelo")
    assert caps.context_length == 0
    assert caps.probed is False


def test_override_preserva_context_length(monkeypatch):
    """H6 del out(4): override solo cambia native_tools; el resto del
    probe (context_length, vision, thinking) se preserva."""
    from core.models_config import set_override

    _patch_post(monkeypatch, data={
        "capabilities": ["tools", "vision", "thinking"],
        "model_info": {"llama.context_length": 131072},
    })
    set_override("modelo-forzado", "xml")
    try:
        caps = get_capabilities("http://localhost:11434", "modelo-forzado")
        assert caps.source == "override"
        # El override cambio native_tools (mode=xml).
        assert caps.native_tools is False
        # Pero el probe trajo el resto.
        assert caps.context_length == 131072
        assert caps.vision is True
        assert caps.thinking is True
    finally:
        from core.models_config import ModelsConfig
        cfg = ModelsConfig()
        cfg.remove("modelo-forzado")


def test_override_native_preserva_context_length(monkeypatch):
    """Con mode=native, native_tools=True y context_length del probe."""
    from core.models_config import set_override

    _patch_post(monkeypatch, data={
        "capabilities": ["completion"],
        "model_info": {"llama.context_length": 32768},
    })
    set_override("modelo-forzado-2", "native")
    try:
        caps = get_capabilities(
            "http://localhost:11434", "modelo-forzado-2"
        )
        assert caps.source == "override"
        assert caps.native_tools is True
        assert caps.context_length == 32768
    finally:
        from core.models_config import ModelsConfig
        cfg = ModelsConfig()
        cfg.remove("modelo-forzado-2")
