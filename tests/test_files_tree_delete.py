"""UI backlog: menu contextual del arbol de archivos gana Borrar.

Paso 1/2: RightPanel emite delete_requested. El controller
conecta en el paso 2.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.views.right_panel import RightPanel


def test_right_panel_tiene_signal_delete_requested(qapp):
    assert hasattr(RightPanel, "delete_requested")


def test_build_file_menu_incluye_borrar(qapp, tmp_path):
    panel = RightPanel()
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    menu = panel._build_file_menu(f)
    labels = [a.text() for a in menu.actions()]
    assert "Borrar" in labels


def test_build_file_menu_carpeta_incluye_borrar(qapp, tmp_path):
    panel = RightPanel()
    d = tmp_path / "sub"
    d.mkdir()
    menu = panel._build_file_menu(d)
    labels = [a.text() for a in menu.actions()]
    assert "Borrar" in labels


def test_accion_borrar_emite_signal(qapp, tmp_path):
    panel = RightPanel()
    f = tmp_path / "a.txt"
    f.write_text("x", encoding="utf-8")
    received: list[str] = []
    panel.delete_requested.connect(received.append)
    menu = panel._build_file_menu(f)
    for a in menu.actions():
        if a.text() == "Borrar":
            a.trigger()
    assert received == [str(f)]


def test_accion_borrar_carpeta_emite_signal(qapp, tmp_path):
    panel = RightPanel()
    d = tmp_path / "sub"
    d.mkdir()
    received: list[str] = []
    panel.delete_requested.connect(received.append)
    menu = panel._build_file_menu(d)
    for a in menu.actions():
        if a.text() == "Borrar":
            a.trigger()
    assert received == [str(d)]
