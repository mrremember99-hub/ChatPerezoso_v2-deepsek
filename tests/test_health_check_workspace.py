"""Tests de scripts/health_check.check_workspace (P3#5).

Sin red. Manipula permisos: restaura siempre en teardown.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import health_check  # noqa: E402


@pytest.fixture
def isolate(tmp_path, monkeypatch):
    """ROOT aislado: sin config.json salvo que el test lo cree."""
    monkeypatch.setattr(health_check, "ROOT", tmp_path)
    return tmp_path


def test_readable_carpeta_normal(tmp_path):
    assert health_check._readable(tmp_path) is True


def test_readable_no_existe(tmp_path):
    assert health_check._readable(tmp_path / "nope") is False


def test_readable_sin_permisos(tmp_path):
    d = tmp_path / "priv"
    d.mkdir()
    os.chmod(d, 0o000)
    try:
        assert health_check._readable(d) is False
    finally:
        os.chmod(d, 0o755)


def test_check_workspace_ok(isolate, capsys):
    (isolate / "workspace").mkdir()
    assert health_check.check_workspace() is True
    out = capsys.readouterr().out
    assert "legible" in out and "sí" in out


def test_check_workspace_ilegible_falla(isolate, capsys):
    ws = isolate / "workspace"
    ws.mkdir()
    os.chmod(ws, 0o000)
    try:
        assert health_check.check_workspace() is False
        out = capsys.readouterr().out
        assert "legible" in out and "—" in out
    finally:
        os.chmod(ws, 0o755)


def test_check_workspace_inexistente_falla(isolate):
    assert health_check.check_workspace() is False
