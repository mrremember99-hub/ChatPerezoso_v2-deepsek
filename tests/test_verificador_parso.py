"""Parso como motor de sintaxis multi-error (2026-09-27)."""
from __future__ import annotations

from pathlib import Path

from plugins.verificador.client import (
    _iter_syntax_errors,
    check_python_syntax,
)


def test_detecta_errores_independientes(tmp_path):
    code = (
        "def uno():\n"
        "    return 1\n"
        "\n"
        "x = [1, 2, 3\n"
        "\n"
        "def dos():\n"
        "    return 2\n"
        "\n"
        "y = {\n"
        '    "a": 1\n'
    )
    p = tmp_path / "multi.py"
    p.write_text(code, encoding="utf-8")
    issues = check_python_syntax(p)
    assert len(issues) >= 2, f"esperaba 2+, obtuve {len(issues)}"


def test_codigo_valido_sin_errores(tmp_path):
    p = tmp_path / "ok.py"
    p.write_text("def f():\n    return 1\n", encoding="utf-8")
    assert check_python_syntax(p) == []


def test_error_unico_detectado(tmp_path):
    p = tmp_path / "one.py"
    p.write_text("def f(:\n    return 1\n", encoding="utf-8")
    issues = check_python_syntax(p)
    assert len(issues) >= 1
    assert any(i.line == 1 for i in issues)


def test_fichero_inexistente_mensaje_claro(tmp_path):
    p = tmp_path / "no_existe.py"
    issues = check_python_syntax(p)
    assert len(issues) == 1
    assert "no se pudo leer" in issues[0].message


def test_iter_syntax_errors_devuelve_lista_vacia_en_valido():
    assert _iter_syntax_errors("x = 1\n") == []


def test_iter_syntax_errors_detecta_error_simple():
    issues = _iter_syntax_errors("def f(:\n    pass\n")
    assert len(issues) >= 1
    assert issues[0].line == 1


def test_iter_syntax_errors_quita_prefijo_syntaxerror():
    issues = _iter_syntax_errors("def f(:\n    pass\n")
    assert issues
    for i in issues:
        assert not i.message.startswith("SyntaxError:")


def test_main_guard_sigue_detectando(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(
        'if __name__ == "main":\n    pass\n',
        encoding="utf-8",
    )
    issues = check_python_syntax(p)
    assert len(issues) == 1
    assert "__main__" in issues[0].message
