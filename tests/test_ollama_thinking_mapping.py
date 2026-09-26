"""Tests del mapeo thinking bool->nivel para gpt-oss (N1)."""
from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from core import model_capabilities
from core.ollama import OllamaClient


@pytest.fixture(autouse=True)
def _reset_cache():
    model_capabilities.clear_cache()
    yield
    model_capabilities.clear_cache()


def _make_client(monkeypatch, captured_payloads):
    import httpx

    class _ShowResponse:
        def raise_for_status(self): return None
        def json(self): return {"capabilities": ["tools", "thinking"]}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _ShowResponse())

    class _FakeResponse:
        def __init__(self): pass
        def raise_for_status(self): return None
        async def aiter_bytes(self, chunk_size=1024):
            yield (json.dumps({"message": {}, "done": True}) + "\n").encode("utf-8")

    class _FakeStreamCtx:
        async def __aenter__(self): return _FakeResponse()
        async def __aexit__(self, *a): return False

    class _FakeAsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def stream(self, method, url, json=None, **kw):
            captured_payloads.append(json)
            return _FakeStreamCtx()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return OllamaClient()


def test_thinking_bool_se_mapea_a_high_para_gpt_oss(monkeypatch, tmp_path):
    from core import models_config
    fake = models_config.ModelsConfig(tmp_path / "models.json")
    fake.set("gpt-oss:20b", "auto", thinking=True)
    monkeypatch.setattr(models_config, "_default", fake)

    payloads = []
    client = _make_client(monkeypatch, payloads)
    client.chat(
        "gpt-oss:20b",
        [{"role": "user", "content": "hola"}],
        None,
        lambda _: None,
        lambda *_: "",
    )
    assert payloads
    assert payloads[0].get("think") == "high"


def test_thinking_bool_se_mapea_a_low_para_gpt_oss(monkeypatch, tmp_path):
    from core import models_config
    fake = models_config.ModelsConfig(tmp_path / "models.json")
    fake.set("gpt-oss:20b", "auto", thinking=False)
    monkeypatch.setattr(models_config, "_default", fake)

    payloads = []
    client = _make_client(monkeypatch, payloads)
    client.chat(
        "gpt-oss:20b",
        [{"role": "user", "content": "hola"}],
        None,
        lambda _: None,
        lambda *_: "",
    )
    assert payloads[0].get("think") == "low"


def test_thinking_str_se_envia_tal_cual(monkeypatch, tmp_path):
    from core import models_config
    fake = models_config.ModelsConfig(tmp_path / "models.json")
    fake.set("gpt-oss:20b", "auto", thinking="medium")
    monkeypatch.setattr(models_config, "_default", fake)

    payloads = []
    client = _make_client(monkeypatch, payloads)
    client.chat(
        "gpt-oss:20b",
        [{"role": "user", "content": "hola"}],
        None,
        lambda _: None,
        lambda *_: "",
    )
    assert payloads[0].get("think") == "medium"


def test_thinking_bool_no_gpt_oss_se_envia_tal_cual(monkeypatch, tmp_path):
    from core import models_config
    fake = models_config.ModelsConfig(tmp_path / "models.json")
    fake.set("qwen3:14b", "auto", thinking=False)
    monkeypatch.setattr(models_config, "_default", fake)

    payloads = []
    client = _make_client(monkeypatch, payloads)
    client.chat(
        "qwen3:14b",
        [{"role": "user", "content": "hola"}],
        None,
        lambda _: None,
        lambda *_: "",
    )
    # qwen3 acepta bool, no se mapea.
    assert payloads[0].get("think") is False
