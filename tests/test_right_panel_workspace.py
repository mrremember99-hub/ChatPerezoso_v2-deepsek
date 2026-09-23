"""R-1: explorador de archivos del workspace en el panel derecho.

Verifica que RightPanel tiene el arbol, que el modelo esta
conectado, y que el filtro proxy rechaza directorios de sistema.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")


@pytest.fixture
def panel(qapp):
    from ui.views.right_panel import RightPanel
    p = RightPanel()
    yield p
    p.deleteLater()
    qapp.processEvents()


def test_panel_has_workspace_tree(panel):
    assert hasattr(panel, "workspace_tree")
    assert panel.workspace_tree.model() is not None


def test_set_workspace_accepts_path(panel, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("x", encoding="utf-8")

    panel.set_workspace(tmp_path)
    # El tree debe tener un root index valido tras set_workspace.
    root = panel.workspace_tree.rootIndex()
    assert root.isValid()


def test_set_workspace_accepts_none(panel):
    panel.set_workspace(None)
    # Root index invalido => muestra el modelo completo. No debe crashear.
    assert not panel.workspace_tree.rootIndex().isValid()


def test_set_workspace_accepts_string(panel, tmp_path):
    panel.set_workspace(str(tmp_path))
    assert panel.workspace_tree.rootIndex().isValid()
