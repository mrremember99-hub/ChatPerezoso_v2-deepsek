"""F6-bis (2026-09-27): sugerencias accionables por codigo."""
from __future__ import annotations

from plugins.verificador.client import (
    _MYPY_SUGGESTIONS,
    _RUFF_SUGGESTIONS,
)


def test_ruff_suggestions_cubre_codigos_principales():
    assert "F821" in _RUFF_SUGGESTIONS
    assert "F811" in _RUFF_SUGGESTIONS
    assert "F823" in _RUFF_SUGGESTIONS
    assert "F501" in _RUFF_SUGGESTIONS
    assert "invalid-syntax" in _RUFF_SUGGESTIONS


def test_mypy_suggestions_cubre_codigos_principales():
    assert "name-defined" in _MYPY_SUGGESTIONS
    assert "attr-defined" in _MYPY_SUGGESTIONS
    assert "import-not-found" in _MYPY_SUGGESTIONS
    assert "arg-type" in _MYPY_SUGGESTIONS
    assert "call-arg" in _MYPY_SUGGESTIONS


def test_f821_menciona_except_lambda():
    sug = _RUFF_SUGGESTIONS["F821"]
    assert "except" in sug
    assert "lambda" in sug


def test_sugerencias_son_cortas():
    for code, sug in _RUFF_SUGGESTIONS.items():
        assert len(sug) < 300, f"{code}: {len(sug)} chars"
    for code, sug in _MYPY_SUGGESTIONS.items():
        assert len(sug) < 300, f"{code}: {len(sug)} chars"


def test_ruff_f821_lleva_sugerencia(tmp_path):
    import shutil
    import pytest
    if shutil.which("ruff") is None:
        pytest.skip("ruff no instalado")

    from plugins.verificador.client import _run_ruff

    p = tmp_path / "bug.py"
    p.write_text(
        "def f():\n"
        "    try:\n"
        "        pass\n"
        "    except Exception as exc:\n"
        "        print(lambda: str(exc))\n",
        encoding="utf-8",
    )
    issues = _run_ruff(p)
    f821 = [i for i in issues if i.code == "F821"]
    assert f821, "no se detecto F821"
    assert "Sugerencia" in f821[0].message
