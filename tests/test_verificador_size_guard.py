"""P2#6: check_python_syntax no invoca parso si el archivo es enorme."""
from __future__ import annotations

import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.verificador import client as vc  # noqa: E402


def test_archivo_grande_no_llega_a_parso(tmp_path, monkeypatch):
    """Si el archivo excede MAX_VERIFY_BYTES, parso ni se toca."""
    def boom(*a, **kw):
        raise AssertionError("parso no deberia llamarse en archivo grande")

    monkeypatch.setattr(vc.parso, "load_grammar", boom)

    big = tmp_path / "big.py"
    big.write_bytes(b"x = 1\n" * (vc.MAX_VERIFY_BYTES // 6 + 100))

    issues = vc.check_python_syntax(big)
    assert len(issues) == 1
    assert "demasiado grande" in issues[0].message
    assert str(vc.MAX_VERIFY_BYTES) in issues[0].message


def test_archivo_normal_si_llega_a_parso(tmp_path, monkeypatch):
    """Regresion: por debajo del limite, sigue el flujo normal."""
    called = {"n": 0}
    orig = vc.parso.load_grammar

    def spy(*a, **kw):
        called["n"] += 1
        return orig(*a, **kw)

    monkeypatch.setattr(vc.parso, "load_grammar", spy)

    small = tmp_path / "small.py"
    small.write_text("x = 1\n", encoding="utf-8")

    issues = vc.check_python_syntax(small)
    assert issues == []
    assert called["n"] == 1


def test_archivo_exactamente_en_el_limite_pasa(tmp_path, monkeypatch):
    """<= MAX_VERIFY_BYTES debe seguir intentando parsear."""
    called = {"n": 0}
    orig = vc.parso.load_grammar

    def spy(*a, **kw):
        called["n"] += 1
        return orig(*a, **kw)

    monkeypatch.setattr(vc.parso, "load_grammar", spy)

    # Contenido valido (un comentario) exactamente del tamano limite.
    pad = vc.MAX_VERIFY_BYTES - len("# ")
    limit_file = tmp_path / "limit.py"
    limit_file.write_bytes(b"# " + b"x" * pad)

    vc.check_python_syntax(limit_file)
    assert called["n"] == 1
