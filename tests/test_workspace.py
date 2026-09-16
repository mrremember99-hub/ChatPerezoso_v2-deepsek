from pathlib import Path

import pytest

from core.workspace import Workspace, WorkspaceError


def test_list_and_read(tmp_path: Path):
    (tmp_path / "a.txt").write_text("hola", encoding="utf-8")
    ws = Workspace(tmp_path)
    assert "a.txt" in ws.list_dir()
    assert ws.read_file("a.txt") == "hola"


def test_path_traversal_is_blocked(tmp_path: Path):
    ws = Workspace(tmp_path)
    with pytest.raises(WorkspaceError):
        ws.read_file("../fuera.txt")


def test_list_and_read_stay_inside_workspace(tmp_path: Path):
    outside = tmp_path.parent / "fuera.txt"
    outside.write_text("secreto", encoding="utf-8")
    ws = Workspace(tmp_path)
    with pytest.raises(WorkspaceError):
        ws.read_file("../fuera.txt")


def test_create_write_delete(tmp_path: Path):
    ws = Workspace(tmp_path)
    assert "creado" in ws.create_file("x.txt", "uno")
    assert ws.read_file("x.txt") == "uno"
    assert "escrito" in ws.write_file("x.txt", "dos")
    assert ws.read_file("x.txt") == "dos"
    assert "borrado" in ws.delete_file("x.txt")


def test_create_folder(tmp_path: Path):
    ws = Workspace(tmp_path)
    assert "creada" in ws.create_folder("fotos")
    assert (tmp_path / "fotos").is_dir()
    assert "[DIR]" in ws.list_dir()


def test_create_folder_does_not_overwrite(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.create_folder("fotos")
    with pytest.raises(WorkspaceError):
        ws.create_folder("fotos")


def test_create_folder_creates_missing_parents(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.create_folder("a/b/c")
    assert (tmp_path / "a" / "b" / "c").is_dir()


def test_create_folder_stays_inside_workspace(tmp_path: Path):
    ws = Workspace(tmp_path)
    with pytest.raises(WorkspaceError):
        ws.create_folder("../fuera")


def test_write_operations_stay_inside_workspace(tmp_path: Path):
    ws = Workspace(tmp_path)
    with pytest.raises(WorkspaceError):
        ws.create_file("../fuera.txt", "no")
    with pytest.raises(WorkspaceError):
        ws.write_file("../fuera.txt", "no")


def test_create_does_not_overwrite(tmp_path: Path):
    ws = Workspace(tmp_path)
    ws.create_file("x.txt", "uno")
    with pytest.raises(WorkspaceError):
        ws.create_file("x.txt", "dos")


def test_write_limit(tmp_path: Path):
    ws = Workspace(tmp_path)
    with pytest.raises(WorkspaceError):
        ws.write_file("x.txt", "x" * (1_000_001))
