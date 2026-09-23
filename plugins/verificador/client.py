"""Cliente de verificación. Lógica pura, sin Qt ni Workspace."""
from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path


@dataclass(frozen=True)
class SyntaxIssue:
    """Un problema de sintaxis detectado."""

    line: int
    column: int
    message: str

    def format(self, filename: str) -> str:
        return f"{filename}:{self.line}:{self.column}  {self.message}"


def check_python_syntax(path: Path) -> list[SyntaxIssue]:
    """Parsea un archivo Python con ast. No ejecuta el código."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        return [SyntaxIssue(0, 0, f"no se pudo leer: {exc}")]
    try:
        ast.parse(source, filename=str(path))
    except SyntaxError as exc:
        return [
            SyntaxIssue(
                line=exc.lineno or 0,
                column=exc.offset or 0,
                message=exc.msg or "syntax error",
            )
        ]
    except ValueError as exc:
        # ast.parse puede lanzar ValueError en algunos casos (bytes nulos).
        return [SyntaxIssue(0, 0, f"parse error: {exc}")]
    return []


def check_syntax(path: Path) -> list[SyntaxIssue]:
    """Verifica sintaxis según la extensión.

    Hoy solo Python. Otros lenguajes devuelven lista vacía por diseño
    (no ruido si no podemos verificar de verdad).
    """
    ext = path.suffix.lower()
    if ext == ".py":
        return check_python_syntax(path)
    return []
