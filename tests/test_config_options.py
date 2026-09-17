from __future__ import annotations

import json

from core.config import AppConfig


def test_default_temperature_and_num_ctx():
    config = AppConfig()
    assert config.temperature == 0.7
    assert config.num_ctx == 0


def test_ollama_options_minimal():
    config = AppConfig(temperature=0.3, num_ctx=0)
    assert config.ollama_options() == {"temperature": 0.3}


def test_ollama_options_with_num_ctx():
    config = AppConfig(temperature=0.5, num_ctx=8192)
    options = config.ollama_options()
    assert options["temperature"] == 0.5
    assert options["num_ctx"] == 8192


def test_load_coerces_int_to_float_temperature(tmp_path, monkeypatch):
    from core import config as config_module

    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"temperature": 1}), encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)

    config = AppConfig.load()
    assert config.temperature == 1.0
    assert isinstance(config.temperature, float)


def test_load_rejects_bool_temperature(tmp_path, monkeypatch):
    from core import config as config_module

    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"temperature": True}), encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)

    config = AppConfig.load()
    assert config.temperature == AppConfig.temperature  # default


def test_load_clamps_extreme_temperature(tmp_path, monkeypatch):
    from core import config as config_module

    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"temperature": 999}), encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)

    config = AppConfig.load()
    assert config.temperature <= 2.0


def test_load_clamps_extreme_num_ctx(tmp_path, monkeypatch):
    from core import config as config_module

    config_file = tmp_path / "config.json"
    config_file.write_text(json.dumps({"num_ctx": 10_000_000}), encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)

    config = AppConfig.load()
    assert config.num_ctx <= 512_000
