"""Tests del filtro de codigos de ruff/mypy."""
from __future__ import annotations

from pathlib import Path

import pytest

from plugins.verificador.client import (
    _MYPY_RELEVANT_CODES,
    _RUFF_RELEVANT_CODES,
    check_quality,
)


def test_ruff_whitelist_incluye_errores_reales():
    assert "invalid-syntax" in _RUFF_RELEVANT_CODES
    assert "F821" in _RUFF_RELEVANT_CODES
    assert "F811" in _RUFF_RELEVANT_CODES


def test_ruff_whitelist_excluye_cosmeticos():
    assert "I001" not in _RUFF_RELEVANT_CODES
    assert "RUF046" not in _RUFF_RELEVANT_CODES
    assert "RUF022" not in _RUFF_RELEVANT_CODES
    assert "UP006" not in _RUFF_RELEVANT_CODES
    assert "BLE001" not in _RUFF_RELEVANT_CODES
    assert "F401" not in _RUFF_RELEVANT_CODES


def test_mypy_whitelist_incluye_errores_reales():
    assert "syntax" in _MYPY_RELEVANT_CODES
    assert "attr-defined" in _MYPY_RELEVANT_CODES
    assert "arg-type" in _MYPY_RELEVANT_CODES
    assert "import-not-found" in _MYPY_RELEVANT_CODES


def test_mypy_whitelist_excluye_ruido():
    # dict-item, valid-type y demas dependen de stubs externos.
    assert "dict-item" not in _MYPY_RELEVANT_CODES


@pytest.mark.skipif(
    __import__("shutil").which("ruff") is None,
    reason="ruff no instalado",
)
def test_archivo_con_solo_cosmetica_no_reporta(tmp_path):
    """Un archivo con imports sin ordenar y cosmética no genera issues."""
    # Codigo valido pero con I001 (import sin ordenar).
    f = tmp_path / "cosmetico.py"
    f.write_text(
        "import sys\n"
        "import os\n"
        "\n"
        "print(os.path, sys.path)\n",
        encoding="utf-8",
    )
    issues = check_quality(f)
    # I001 (orden imports) se descarta, no debe aparecer.
    codes = [i.code for i in issues]
    assert "I001" not in codes


@pytest.mark.skipif(
    __import__("shutil").which("ruff") is None,
    reason="ruff no instalado",
)
def test_archivo_con_name_roto_si_reporta(tmp_path):
    """Un F821 (undefined name) sí debe aparecer."""
    f = tmp_path / "roto.py"
    f.write_text(
        "def f():\n"
        "    return variable_que_no_existe\n",
        encoding="utf-8",
    )
    issues = check_quality(f)
    codes = [i.code for i in issues]
    assert "F821" in codes


# -- mypy: el codigo real viene entre [..] al final ---------------------

def test_mypy_line_captura_codigo_entre_corchetes():
    """Verifica que _MYPY_LINE extrae `attr-defined` y similares."""
    from plugins.verificador.client import _MYPY_LINE

    sample = 'x.py:10:5: error: "Foo" has no attribute "bar"  [attr-defined]'
    m = _MYPY_LINE.match(sample)
    assert m is not None
    assert m.group("sev") == "error"
    assert m.group("code") == "attr-defined"


def test_mypy_line_sin_codigo_da_none():
    """Errores de sintaxis de mypy no llevan [] al final."""
    from plugins.verificador.client import _MYPY_LINE

    sample = 'x.py:10: error: Syntax error in source'
    m = _MYPY_LINE.match(sample)
    assert m is not None
    assert m.group("sev") == "error"
    assert m.group("code") is None


def test_mypy_line_nota_no_lleva_codigo():
    from plugins.verificador.client import _MYPY_LINE

    sample = 'x.py:10: note: See https://... for more details'
    m = _MYPY_LINE.match(sample)
    assert m is not None
    assert m.group("sev") == "note"
