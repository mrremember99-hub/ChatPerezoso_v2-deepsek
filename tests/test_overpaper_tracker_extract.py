"""Overpaper run #1: agrupacion de tracebacks multi-linea."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from scripts import overpaper_tracker as t  # noqa: E402


def test_un_traceback_cuenta_como_uno(tmp_path):
    log = tmp_path / "app.log"
    log.write_text(
        "arrancando\n"
        "Traceback (most recent call last):\n"
        "  File \"a.py\", line 1\n"
        "    x = 1/0\n"
        "ZeroDivisionError: division by zero\n"
        "sigue la app\n",
        encoding="utf-8",
    )
    errs = t._extract_errors(log)
    assert len(errs) == 1
    assert "ZeroDivisionError" in errs[0]


def test_dos_tracebacks_cuentan_dos(tmp_path):
    log = tmp_path / "app.log"
    log.write_text(
        "Traceback (most recent call last):\n"
        "  File \"a.py\", line 1\n"
        "ValueError: a\n"
        "linea normal\n"
        "Traceback (most recent call last):\n"
        "  File \"b.py\", line 2\n"
        "TypeError: b\n",
        encoding="utf-8",
    )
    errs = t._extract_errors(log)
    assert len(errs) == 2
    assert any("ValueError" in e for e in errs)
    assert any("TypeError" in e for e in errs)


def test_log_vacio(tmp_path):
    log = tmp_path / "app.log"
    log.write_text("", encoding="utf-8")
    assert t._extract_errors(log) == []


def test_log_inexistente(tmp_path):
    assert t._extract_errors(tmp_path / "no") == []
