"""F3-ter (2026-09-28): rechazo de truncado por tamano en write_file."""
from __future__ import annotations

import pytest

from core.workspace import Workspace, WorkspaceError


@pytest.fixture
def ws(tmp_path):
    return Workspace(tmp_path)


def test_shrink_rejected_above_threshold(ws, tmp_path):
    p = tmp_path / "big.py"
    p.write_text("x = 1\n" * 400, encoding="utf-8")
    with pytest.raises(WorkspaceError, match="Reduccion drastica"):
        ws.write_file("big.py", "x = 1\n")


def test_shrink_below_min_size_allowed(ws, tmp_path):
    p = tmp_path / "small.py"
    p.write_text("a\n" * 100, encoding="utf-8")
    result = ws.write_file("small.py", "a\n")
    assert "escrito" in result.lower()


def test_growth_allowed(ws, tmp_path):
    p = tmp_path / "grow.py"
    p.write_text("x" * 2000, encoding="utf-8")
    result = ws.write_file("grow.py", "x" * 3000)
    assert "escrito" in result.lower()


def test_new_file_no_check(ws):
    result = ws.write_file("nuevo.py", "x = 1\n")
    assert "escrito" in result.lower()


def test_placeholder_content_rejected(ws, tmp_path):
    p = tmp_path / "gui.py"
    p.write_text("import tkinter as tk\n" * 200, encoding="utf-8")
    content_truncado = (
        "import tkinter as tk\n"
        "# ... rest of the original gui.py content unchanged ...\n"
    )
    with pytest.raises(WorkspaceError, match="Reduccion drastica"):
        ws.write_file("gui.py", content_truncado)
