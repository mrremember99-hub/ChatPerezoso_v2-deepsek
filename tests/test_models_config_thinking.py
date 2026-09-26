"""Tests del toggle de thinking por modelo."""
from __future__ import annotations

import json

from core.models_config import ModelOverride, ModelsConfig


def test_override_thinking_default_is_none():
    o = ModelOverride(mode="auto")
    assert o.thinking is None


def test_load_parses_thinking_false(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({
        "overrides": {
            "qwen3:14b": {"mode": "native", "thinking": False},
        }
    }), encoding="utf-8")
    cfg = ModelsConfig(path)
    o = cfg.get("qwen3:14b")
    assert o.thinking is False
    assert o.mode == "native"


def test_load_parses_thinking_true(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({
        "overrides": {
            "deepseek-r1": {"mode": "xml", "thinking": True},
        }
    }), encoding="utf-8")
    cfg = ModelsConfig(path)
    assert cfg.get("deepseek-r1").thinking is True


def test_load_absent_thinking_is_none(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({
        "overrides": {
            "llama3.1": {"mode": "native"},
        }
    }), encoding="utf-8")
    cfg = ModelsConfig(path)
    assert cfg.get("llama3.1").thinking is None


def test_load_ignores_invalid_thinking_string(tmp_path):
    path = tmp_path / "models.json"
    path.write_text(json.dumps({
        "overrides": {
            "raro": {"mode": "auto", "thinking": "sí"},
            "otro": {"mode": "auto", "thinking": "yes"},
        }
    }), encoding="utf-8")
    cfg = ModelsConfig(path)
    assert cfg.get("raro").thinking is None
    assert cfg.get("otro").thinking is None


def test_load_acepta_thinking_levels(tmp_path):
    """low/medium/high se aceptan tal cual (para gpt-oss)."""
    path = tmp_path / "models.json"
    path.write_text(json.dumps({
        "overrides": {
            "a": {"mode": "auto", "thinking": "low"},
            "b": {"mode": "auto", "thinking": "MEDIUM"},
            "c": {"mode": "auto", "thinking": "High"},
        }
    }), encoding="utf-8")
    cfg = ModelsConfig(path)
    assert cfg.get("a").thinking == "low"
    assert cfg.get("b").thinking == "medium"
    assert cfg.get("c").thinking == "high"


def test_set_acepta_thinking_level(tmp_path):
    path = tmp_path / "models.json"
    cfg = ModelsConfig(path)
    cfg.set("gpt-oss:20b", "auto", thinking="high")
    cfg2 = ModelsConfig(path)
    assert cfg2.get("gpt-oss:20b").thinking == "high"


def test_set_rechaza_thinking_invalido(tmp_path):
    import pytest
    path = tmp_path / "models.json"
    cfg = ModelsConfig(path)
    with pytest.raises(ValueError):
        cfg.set("x", "auto", thinking="turbo")


def test_set_persists_thinking(tmp_path):
    path = tmp_path / "models.json"
    cfg = ModelsConfig(path)
    cfg.set("qwen3:14b", "native", thinking=False)
    # Releer desde disco para verificar persistencia.
    cfg2 = ModelsConfig(path)
    assert cfg2.get("qwen3:14b").thinking is False


def test_set_thinking_none_does_not_write_field(tmp_path):
    path = tmp_path / "models.json"
    cfg = ModelsConfig(path)
    cfg.set("llama3.1", "native")
    raw = json.loads(path.read_text(encoding="utf-8"))
    assert "thinking" not in raw["overrides"]["llama3.1"]


def test_set_auto_with_thinking_persists(tmp_path):
    """Un override de thinking sin cambio de modo debe persistir."""
    path = tmp_path / "models.json"
    cfg = ModelsConfig(path)
    cfg.set("muse-glimmer", "auto", thinking=False)
    cfg2 = ModelsConfig(path)
    o = cfg2.get("muse-glimmer")
    assert o.mode == "auto"
    assert o.thinking is False


def test_remove_clears_thinking(tmp_path):
    path = tmp_path / "models.json"
    cfg = ModelsConfig(path)
    cfg.set("x", "native", thinking=False)
    cfg.remove("x")
    cfg2 = ModelsConfig(path)
    assert cfg2.get("x").thinking is None
    assert cfg2.get("x").mode == "auto"


def test_get_override_for_unknown_model_returns_default(tmp_path):
    path = tmp_path / "models.json"
    cfg = ModelsConfig(path)
    o = cfg.get("modelo-que-no-existe")
    assert o.mode == "auto"
    assert o.thinking is None
