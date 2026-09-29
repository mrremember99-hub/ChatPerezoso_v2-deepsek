"""Overpaper run #1: ShellClient debe normalizar 'python' a 'python3'."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.shell.client import ShellClient  # noqa: E402


def test_python_se_normaliza_a_python3(tmp_path):
    client = ShellClient(tmp_path)
    out = client.execute("python --version")
    assert "no existe" not in out, f"no normalizo: {out!r}"
    assert "Python" in out


def test_python3_sin_cambios(tmp_path):
    client = ShellClient(tmp_path)
    out = client.execute("python3 --version")
    assert "no existe" not in out
    assert "Python" in out
