import json
from pathlib import Path

from core.config import AppConfig


def test_default_config_has_sane_values():
    config = AppConfig()
    assert config.ollama_host.startswith("http")
    assert config.width >= 600
    assert config.height >= 400


def test_workspace_path_is_a_path_and_does_not_create_directories(tmp_path, monkeypatch):
    """``workspace_path()`` es puro: no debe crear nada en disco."""
    from core import config as config_module

    target = tmp_path / "no-existe"
    monkeypatch.setattr(config_module, "DEFAULT_WORKSPACE", target)

    config = AppConfig(workspace=str(target))
    result = config.workspace_path()

    assert isinstance(result, Path)
    assert result == target.resolve()
    assert not target.exists(), "workspace_path() no debe crear el directorio"


def test_load_ignores_values_with_wrong_type(tmp_path, monkeypatch):
    from core import config as config_module

    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({
            "ollama_host": "http://localhost:11434",
            "width": "no soy un int",
            "height": 720,
            "model": 42,
        }),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)

    config = AppConfig.load()

    assert config.width == AppConfig.width       # default, no "no soy un int"
    assert config.height == 720                  # válido, respetado
    assert config.model == AppConfig.model       # default, no 42


def test_load_clamps_absurd_dimensions(tmp_path, monkeypatch):
    from core import config as config_module

    config_file = tmp_path / "config.json"
    config_file.write_text(
        json.dumps({"width": -100, "height": 99999}),
        encoding="utf-8",
    )
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)

    config = AppConfig.load()

    assert config.width >= 600
    assert config.height <= 3000


def test_load_tolerates_corrupted_json(tmp_path, monkeypatch):
    from core import config as config_module

    config_file = tmp_path / "config.json"
    config_file.write_text("esto no es json", encoding="utf-8")
    monkeypatch.setattr(config_module, "CONFIG_FILE", config_file)

    config = AppConfig.load()

    assert config.width == AppConfig.width
    assert config.height == AppConfig.height
