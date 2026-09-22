"""Regresión WS-1: list_dir ignora directorios de sistema.

Antes, `listar_carpeta(recursive=True)` incluía `.git/`, `node_modules/`,
`__pycache__/`, etc. En un workspace real, el `MAX_LIST_ITEMS` cortaba
el listado antes de llegar a los archivos que le interesan al usuario.
Ahora se filtran esos directorios, igual que hace el plugin search.
"""
from __future__ import annotations

import pytest

from core.workspace import Workspace, WorkspaceError


# ── WS-1: filtrado de directorios ────────────────────────────────────

def test_list_flat_skips_git_dir(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "config").write_text("basura", encoding="utf-8")
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("print()", encoding="utf-8")

    result = Workspace(tmp_path).list_dir(".")

    assert "src" in result
    assert ".git" not in result
    assert "config" not in result


def test_list_flat_skips_multiple_system_dirs(tmp_path):
    for name in (".git", "node_modules", "__pycache__", ".venv", ".idea"):
        (tmp_path / name).mkdir()
    (tmp_path / "real.txt").write_text("x", encoding="utf-8")

    result = Workspace(tmp_path).list_dir(".")

    assert "real.txt" in result
    for name in (".git", "node_modules", "__pycache__", ".venv", ".idea"):
        assert name not in result, f"'{name}' no deberia aparecer"


def test_list_recursive_skips_system_dirs(tmp_path):
    (tmp_path / ".git").mkdir()
    (tmp_path / ".git" / "HEAD").write_text("ref: refs/heads/main", encoding="utf-8")
    (tmp_path / ".git" / "objects").mkdir()
    (tmp_path / ".git" / "objects" / "ab").mkdir()
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "app.py").write_text("x", encoding="utf-8")

    result = Workspace(tmp_path).list_dir(".", recursive=True)

    assert "app.py" in result
    assert ".git" not in result
    assert "HEAD" not in result
    assert "objects" not in result


def test_list_still_shows_regular_dirs(tmp_path):
    """El filtro no debe ocultar directorios legítimos."""
    for name in ("src", "docs", "tests", "subcarpeta"):
        (tmp_path / name).mkdir()

    result = Workspace(tmp_path).list_dir(".")

    for name in ("src", "docs", "tests", "subcarpeta"):
        assert name in result, f"'{name}' no deberia estar filtrado"


# ── WS-2: mensaje de error con ruta ──────────────────────────────────

def test_workspace_error_includes_path_for_file(tmp_path):
    """Si el path es un archivo, el error lo indica con la ruta."""
    archivo = tmp_path / "no_es_carpeta.txt"
    archivo.write_text("contenido", encoding="utf-8")

    with pytest.raises(WorkspaceError) as exc_info:
        Workspace(archivo)

    msg = str(exc_info.value)
    assert "no es una carpeta" in msg
    assert str(archivo.resolve()) in msg, (
        f"el mensaje deberia contener la ruta; recibido: {msg!r}"
    )