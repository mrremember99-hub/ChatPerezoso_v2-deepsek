"""Tests del check __name__ == "main" (bug modelo 2026-09-26)."""
from __future__ import annotations

from pathlib import Path

from plugins.verificador.client import check_python_syntax


def test_detecta_main_sin_guiones(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(
        'def main():\n'
        '    print("hola")\n'
        '\n'
        'if __name__ == "main":\n'
        '    main()\n',
        encoding="utf-8",
    )
    issues = check_python_syntax(p)
    assert len(issues) == 1
    assert "__main__" in issues[0].message
    assert '"main"' in issues[0].message


def test_no_detecta_main_correcto(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(
        'def main():\n'
        '    print("hola")\n'
        '\n'
        'if __name__ == "__main__":\n'
        '    main()\n',
        encoding="utf-8",
    )
    assert check_python_syntax(p) == []


def test_no_detecta_strings_sin_compare(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(
        'x = "main"\n'
        'y = "__main__"\n',
        encoding="utf-8",
    )
    assert check_python_syntax(p) == []


def test_detecta_variante_mayusculas(tmp_path):
    p = tmp_path / "m.py"
    p.write_text('if __name__ == "Main":\n    pass\n', encoding="utf-8")
    issues = check_python_syntax(p)
    assert len(issues) == 1
    assert '"Main"' in issues[0].message


def test_detecta_orden_invertido(tmp_path):
    p = tmp_path / "m.py"
    p.write_text('if "main" == __name__:\n    pass\n', encoding="utf-8")
    issues = check_python_syntax(p)
    assert len(issues) == 1


def test_sintaxis_rota_no_se_duplica(tmp_path):
    p = tmp_path / "m.py"
    p.write_text('def foo(:\n', encoding="utf-8")
    issues = check_python_syntax(p)
    # Solo el SyntaxError, no el check del main guard.
    assert len(issues) == 1
    assert "__main__" not in issues[0].message


def test_no_detecta_si_esta_en_comentario(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(
        '# if __name__ == "main":\n'
        'x = 1\n',
        encoding="utf-8",
    )
    assert check_python_syntax(p) == []


def test_no_detecta_en_docstring(tmp_path):
    p = tmp_path / "m.py"
    p.write_text(
        's = """\n'
        'Ejemplo: if __name__ == \"main\":\n'
        '"""\n',
        encoding="utf-8",
    )
    assert check_python_syntax(p) == []
