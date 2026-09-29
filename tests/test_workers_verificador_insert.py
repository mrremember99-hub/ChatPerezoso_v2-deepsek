"""Overpaper run #3: el verificador cubre insertar/editar.

Bug: `_VERIFY_AFTER` solo tenia crear_archivo + escribir_archivo.
Un insertar_en_archivo con F821 (variable usada antes de definida)
pasaba sin verificacion y el error se acumulaba.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.workers import ChatWorker  # noqa: E402


def test_verify_after_incluye_insertar_y_editar():
    assert "insertar_en_archivo" in ChatWorker._VERIFY_AFTER
    assert "editar_archivo" in ChatWorker._VERIFY_AFTER


def test_verify_after_mantiene_las_originales():
    assert "crear_archivo" in ChatWorker._VERIFY_AFTER
    assert "escribir_archivo" in ChatWorker._VERIFY_AFTER


def test_path_keys_cubre_las_4_tools():
    """Las 4 tools usan 'path' como clave."""
    keys = ChatWorker._PATH_KEYS
    assert "path" in keys
