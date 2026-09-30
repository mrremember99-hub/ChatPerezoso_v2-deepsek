"""Fix run #5: bootstrap lee workspace de config.json.

El diagnostico mostraba WORKSPACE_DIR aunque config.json apuntase
a otra ruta. El ChatWorker si lee el config, asi que el bootstrap
mentia. Estos tests aislan bootstrap.py con ROOT/CONFIG_FILE en
tmp_path para verificar el nuevo comportamiento.
"""
from __future__ import annotations

import dataclasses
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import bootstrap  # noqa: E402


@pytest.fixture
def isolated(tmp_path, monkeypatch):
    """Aisla bootstrap.py: ROOT, WORKSPACE_DIR, CONFIG_FILE en tmp."""
    monkeypatch.setattr(bootstrap, "ROOT", tmp_path)
    monkeypatch.setattr(bootstrap, "WORKSPACE_DIR", tmp_path / "workspace")
    monkeypatch.setattr(bootstrap, "CONFIG_FILE", tmp_path / "config.json")
    return bootstrap


def _check_ok(check) -> bool:
    """Extrae el booleano del Check sin asumir el nombre del field."""
    fields = dataclasses.fields(check)
    for f in fields:
        val = getattr(check, f.name)
        if isinstance(val, bool):
            return val
    raise AssertionError(f"Check sin field booleano: {fields}")


# -- _resolve_workspace_dir ------------------------------------------


def test_resolve_con_config_custom(isolated, tmp_path):
    custom = tmp_path / "custom_ws"
    custom.mkdir()
    (tmp_path / "config.json").write_text(
        json.dumps({"workspace": str(custom)}), encoding="utf-8",
    )
    assert isolated._resolve_workspace_dir() == custom.resolve()


def test_resolve_sin_config(isolated):
    assert isolated._resolve_workspace_dir() == isolated.WORKSPACE_DIR


def test_resolve_config_sin_workspace(isolated, tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"model": "x"}), encoding="utf-8",
    )
    assert isolated._resolve_workspace_dir() == isolated.WORKSPACE_DIR


def test_resolve_config_corrupto(isolated, tmp_path):
    (tmp_path / "config.json").write_text("no-json", encoding="utf-8")
    assert isolated._resolve_workspace_dir() == isolated.WORKSPACE_DIR


def test_resolve_config_workspace_vacio(isolated, tmp_path):
    (tmp_path / "config.json").write_text(
        json.dumps({"workspace": ""}), encoding="utf-8",
    )
    assert isolated._resolve_workspace_dir() == isolated.WORKSPACE_DIR


# -- ensure_workspace -------------------------------------------------


def test_ensure_crea_ruta_custom(isolated, tmp_path):
    custom = tmp_path / "custom_ws"
    (tmp_path / "config.json").write_text(
        json.dumps({"workspace": str(custom)}), encoding="utf-8",
    )
    check = isolated.ensure_workspace()
    assert _check_ok(check) is True
    assert custom.is_dir()
    assert (custom / "README.md").exists()


def test_ensure_usa_default_sin_config(isolated):
    check = isolated.ensure_workspace()
    assert _check_ok(check) is True
    assert isolated.WORKSPACE_DIR.is_dir()


def test_ensure_ok_si_workspace_ya_existe(isolated, tmp_path):
    custom = tmp_path / "custom_ws"
    custom.mkdir()
    (tmp_path / "config.json").write_text(
        json.dumps({"workspace": str(custom)}), encoding="utf-8",
    )
    check = isolated.ensure_workspace()
    assert _check_ok(check) is True
    assert str(custom) in check.detail
