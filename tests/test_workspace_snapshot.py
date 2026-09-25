"""Tests del snapshot de workspace con interfaces Python."""
from __future__ import annotations

import pytest

from core.workspace import Workspace
from core.workspace_snapshot import snapshot_workspace


def _ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    return Workspace(root), root


def test_snapshot_incluye_interfaz_python(tmp_path):
    ws, root = _ws(tmp_path)
    (root / "gui.py").write_text(
        "class AppLayout:\n"
        "    def __init__(self, master): ...\n"
        "    def build(self): ...\n"
        "    def _private_helper(self): ...\n"
        "\n"
        "def process(a, b): ...\n"
        "\n"
        "CONFIG = {}\n",
        encoding="utf-8",
    )
    snap = snapshot_workspace(ws)
    assert "class AppLayout" in snap
    assert "def build(" in snap
    assert "def __init__(" in snap
    assert "_private_helper" not in snap
    assert "def process(a, b)" in snap
    assert "CONFIG" in snap


def test_snapshot_clases_con_bases(tmp_path):
    ws, root = _ws(tmp_path)
    (root / "m.py").write_text(
        "class Foo(Bar, Baz):\n"
        "    pass\n",
        encoding="utf-8",
    )
    snap = snapshot_workspace(ws)
    assert "class Foo(Bar, Baz)" in snap


def test_snapshot_python_vacio_cae_a_preview(tmp_path):
    ws, root = _ws(tmp_path)
    (root / "empty.py").write_text(
        "# solo un comentario\n",
        encoding="utf-8",
    )
    snap = snapshot_workspace(ws)
    assert "empty.py" in snap
    assert "# solo un comentario" in snap


def test_snapshot_python_sintaxis_invalida_cae_a_preview(tmp_path):
    ws, root = _ws(tmp_path)
    (root / "roto.py").write_text(
        "def mal(\n", encoding="utf-8"
    )
    snap = snapshot_workspace(ws)
    assert "roto.py" in snap
    assert "def mal(" in snap


def test_snapshot_metodos_privados_excluidos(tmp_path):
    ws, root = _ws(tmp_path)
    (root / "x.py").write_text(
        "class X:\n"
        "    def _hidden(self): ...\n"
        "    def visible(self): ...\n",
        encoding="utf-8",
    )
    snap = snapshot_workspace(ws)
    assert "visible" in snap
    assert "_hidden" not in snap


def test_snapshot_no_python_mantiene_preview(tmp_path):
    ws, root = _ws(tmp_path)
    (root / "notes.md").write_text(
        "# Titulo\nTexto descriptivo.\n", encoding="utf-8"
    )
    snap = snapshot_workspace(ws)
    assert "notes.md" in snap
    assert "# Titulo" in snap
