"""Fixes de la auditoria 2026-09-27."""
from __future__ import annotations

import tomllib
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


def test_pyproject_tiene_simplemma_y_parso():
    """Bug: pip install . en venv limpio rompia por falta de deps."""
    data = tomllib.loads((ROOT / "pyproject.toml").read_text("utf-8"))
    deps = data["project"]["dependencies"]
    assert any("simplemma" in d for d in deps), deps
    assert any("parso" in d for d in deps), deps


def test_write_tool_hints_incluye_editar_e_insertar():
    """Bug: el dialogo especializado no aplicaba a las 2 tools nuevas."""
    from ui.views.dialogs import _WRITE_TOOL_HINTS, _is_write_tool
    assert "editar_archivo" in _WRITE_TOOL_HINTS
    assert "insertar_en_archivo" in _WRITE_TOOL_HINTS
    assert _is_write_tool("editar_archivo")
    assert _is_write_tool("insertar_en_archivo")


def test_appconfig_tiene_keep_alive():
    from core.config import AppConfig
    assert AppConfig().keep_alive == "30m"


def test_ollama_client_acepta_keep_alive():
    from core.ollama import OllamaClient
    c = OllamaClient("http://x", keep_alive="5m")
    assert c.keep_alive == "5m"
    c2 = OllamaClient("http://x")
    assert c2.keep_alive == "30m"


def test_appconfig_load_acepta_keep_alive_custom(tmp_path, monkeypatch):
    """El keep_alive viene de config.json."""
    import core.config as cfg_mod
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text('{"keep_alive": "5m"}', encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "CONFIG_FILE", cfg_file)
    cfg = cfg_mod.AppConfig.load()
    assert cfg.keep_alive == "5m"


# -- num_predict configurable (auditoria 2026-09-27, test OVERPAPER) -----

def test_appconfig_tiene_num_predict_default_cero():
    from core.config import AppConfig
    assert AppConfig().num_predict == 0


def test_ollama_client_acepta_num_predict():
    from core.ollama import OllamaClient
    c = OllamaClient("http://x", num_predict=-1)
    assert c.num_predict == -1
    c2 = OllamaClient("http://x")
    assert c2.num_predict == 0


def test_appconfig_load_acepta_num_predict_custom(tmp_path, monkeypatch):
    import core.config as cfg_mod
    cfg_file = tmp_path / "config.json"
    cfg_file.write_text('{"num_predict": -1}', encoding="utf-8")
    monkeypatch.setattr(cfg_mod, "CONFIG_FILE", cfg_file)
    cfg = cfg_mod.AppConfig.load()
    assert cfg.num_predict == -1
