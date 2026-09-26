"""Cliente de verificación. Lógica pura, sin Qt ni Workspace.

Cuatro niveles de verificación:
  1. Sintaxis    — ast.parse para Python.
  2. Calidad     — ruff check + mypy (si están instalados).
  3. Seguridad   — regex de secretos comunes.
  4. Conflictos  — marcadores de merge git sin resolver.

Filosofía: silencio si todo está OK. Solo habla cuando encuentra algo.
"""
from __future__ import annotations

import ast
import re
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path


# ─────────────────────────────────────────────────────────────────────
# Estructura de issues
# ─────────────────────────────────────────────────────────────────────


@dataclass(frozen=True)
class SyntaxIssue:
    line: int
    column: int
    message: str

    def format(self, filename: str) -> str:
        return f"{filename}:{self.line}:{self.column}  {self.message}"


@dataclass(frozen=True)
class QualityIssue:
    line: int
    column: int
    code: str
    message: str

    def format(self, filename: str) -> str:
        loc = (
            f"{filename}:{self.line}:{self.column}"
            if self.line else filename
        )
        return f"{loc}  [{self.code}] {self.message}"


@dataclass(frozen=True)
class SecretIssue:
    line: int
    kind: str
    snippet: str  # enmascarado

    def format(self, filename: str) -> str:
        return (
            f"{filename}:{self.line}  [secret] posible {self.kind}: "
            f"{self.snippet}"
        )


@dataclass(frozen=True)
class ConflictIssue:
    line: int
    marker: str

    def format(self, filename: str) -> str:
        return (
            f"{filename}:{self.line}  [conflict] marcador git sin "
            f"resolver: {self.marker}"
        )


# ─────────────────────────────────────────────────────────────────────
# Nivel 1 — Sintaxis
# ─────────────────────────────────────────────────────────────────────


# ─────────────────────────────────────────────────────────────────────
# Filtro de codigos relevantes
#
# Ruff y mypy reportan decenas de warnings cosmeticos (orden de
# imports, int() redundante, __all__ sin ordenar, Tuple->tuple...)
# que no rompen el codigo pero consumen rondas del modelo. El
# objetivo de la verificacion es detectar CODIGO ROTO, no estilo.
#
# Solo pasan los codigos que indican un error real:
#   - No compila (syntax).
#   - Import/name roto (F821, F811, name-defined, import-not-found).
#   - Llamada con tipos/args incorrectos (arg-type, call-arg,
#     call-overload, return-value, attr-defined).
#
# Todo lo demas se descarta silenciosamente.
# ─────────────────────────────────────────────────────────────────────

_RUFF_RELEVANT_CODES: frozenset[str] = frozenset({
    "invalid-syntax",   # syntax real
    "F821",              # undefined name
    "F811",              # redefinition
    "F823",              # local referenced before assignment
    "F501",              # % format error
    "E999",              # syntax error (legacy)
})

_MYPY_RELEVANT_CODES: frozenset[str] = frozenset({
    "syntax",           # syntax real
    "name-defined",     # undefined name
    "attr-defined",     # attribute unknown
    "import-not-found", # import roto
    "arg-type",         # arg de tipo incorrecto
    "call-arg",         # arg faltante/extra
    "call-overload",    # call invalido
    "return-value",     # return incorrecto
    "valid-type",       # tipo invalido
})


def _check_main_guard(source: str) -> list[SyntaxIssue]:
    """Detecta ``if __name__ == "main":`` (falta un guion bajo).

    Modelos pequeños escriben ``"main"`` en vez de ``"__main__"``
    y el bloque ``main()`` nunca se ejecuta. El script termina con
    exit 0 sin output y la app no puede distinguirlo de un exito.
    Ruff y mypy no lo detectan (bug 2026-09-26).
    """
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        # Si no compila, check_python_syntax ya lo reporta.
        return []
    issues: list[SyntaxIssue] = []
    for node in ast.walk(tree):
        if not isinstance(node, ast.Compare):
            continue
        # Detectar `__name__ == "..."` y `"..." == __name__`.
        sides: list[ast.expr] = [node.left] + list(node.comparators)
        if len(sides) != 2:
            continue
        left, right = sides
        name_side: ast.expr | None = None
        const_side: ast.expr | None = None
        for a, b in ((left, right), (right, left)):
            if isinstance(a, ast.Name) and a.id == "__name__":
                name_side, const_side = a, b
                break
        if name_side is None:
            continue
        if not isinstance(const_side, ast.Constant):
            continue
        value = const_side.value
        if not isinstance(value, str):
            continue
        if value == "__main__":
            continue
        issues.append(SyntaxIssue(
            line=node.lineno,
            column=node.col_offset,
            message=(
                f'__name__ comparado con "{value}" en vez de '
                '"__main__". El bloque main() no se ejecutara.'
            ),
        ))
    return issues


def check_python_syntax(path: Path) -> list[SyntaxIssue]:
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
        return [SyntaxIssue(0, 0, f"parse error: {exc}")]
    # Compila: buscar patrones que ejecutan silenciosamente mal.
    return _check_main_guard(source)


def check_syntax(path: Path) -> list[SyntaxIssue]:
    if path.suffix.lower() == ".py":
        return check_python_syntax(path)
    return []


# ─────────────────────────────────────────────────────────────────────
# Nivel 2 — Calidad (ruff + mypy, opcional)
# ─────────────────────────────────────────────────────────────────────


_RUFF_LINE = re.compile(
    r"^.+?:(?P<line>\d+):(?P<col>\d+):\s*(?P<code>\S+)\s+(?P<msg>.+)$"
)
_MYPY_LINE = re.compile(
    r"^.+?:(?P<line>\d+):(?:\s*(?P<col>\d+):)?\s*"
    r"(?P<sev>error|warning|note):\s*(?P<msg>.+?)"
    r"(?:\s+\[(?P<code>[a-z-]+)\])?$"
)


def check_quality(path: Path) -> list[QualityIssue]:
    """Ruff + mypy. Silencio si ninguno está instalado o si no hay issues."""
    if path.suffix.lower() != ".py":
        return []
    issues: list[QualityIssue] = []
    issues.extend(_run_ruff(path))
    issues.extend(_run_mypy(path))
    return issues


def _run_ruff(path: Path) -> list[QualityIssue]:
    if shutil.which("ruff") is None:
        return []
    try:
        proc = subprocess.run(
            ["ruff", "check", "--output-format=concise", "--no-cache", str(path)],
            capture_output=True, text=True, timeout=10,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    out: list[QualityIssue] = []
    for raw in proc.stdout.splitlines():
        m = _RUFF_LINE.match(raw.strip())
        if not m:
            continue
        code = m.group("code")
        if code not in _RUFF_RELEVANT_CODES:
            # Cosmetica (I001, RUF*, UP*, BLE*, S110, F401...):
            # se descarta. Consume rondas del modelo sin aportar.
            continue
        out.append(QualityIssue(
            line=int(m.group("line")),
            column=int(m.group("col")),
            code=code,
            message=m.group("msg").strip(),
        ))
    return out


def _run_mypy(path: Path) -> list[QualityIssue]:
    if shutil.which("mypy") is None:
        return []
    try:
        proc = subprocess.run(
            ["mypy", "--no-error-summary", "--no-color-output", str(path)],
            capture_output=True, text=True, timeout=5,
        )
    except (subprocess.TimeoutExpired, OSError):
        return []
    out: list[QualityIssue] = []
    for raw in proc.stdout.splitlines():
        m = _MYPY_LINE.match(raw.strip())
        if not m:
            continue
        # `sev` = error|warning|note. `code` = codigo real entre
        # [..] al final (attr-defined, arg-type, etc.) o None.
        sev = m.group("sev")
        if sev == "note":
            continue
        code = m.group("code")
        if code is None or code not in _MYPY_RELEVANT_CODES:
            continue
        out.append(QualityIssue(
            line=int(m.group("line")),
            column=int(m.group("col") or 0),
            code="mypy",
            message=m.group("msg").strip(),
        ))
    return out


# ─────────────────────────────────────────────────────────────────────
# Nivel 3 — Secretos
# ─────────────────────────────────────────────────────────────────────


_SECRET_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("AWS access key", re.compile(r"AKIA[0-9A-Z]{16}")),
    ("GitHub token", re.compile(r"\bgh[psoru]_[a-zA-Z0-9]{36,}\b")),
    ("Slack token", re.compile(r"\bxox[baprs]-[0-9a-zA-Z-]{10,}\b")),
    ("Google API key", re.compile(r"\bAIza[0-9A-Za-z\-_]{35}\b")),
    ("clave PEM", re.compile(
        r"-----BEGIN (?:RSA |EC |OPENSSH |DSA |PGP )?PRIVATE KEY-----"
    )),
    ("Stripe key", re.compile(r"\bsk_(?:live|test)_[0-9a-zA-Z]{20,}\b")),
)


def _mask(snippet: str) -> str:
    if len(snippet) <= 8:
        return "*" * len(snippet)
    return snippet[:4] + "…" + snippet[-4:]


def scan_secrets(path: Path) -> list[SecretIssue]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out: list[SecretIssue] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        for kind, pattern in _SECRET_PATTERNS:
            for m in pattern.finditer(line):
                out.append(SecretIssue(
                    line=lineno,
                    kind=kind,
                    snippet=_mask(m.group(0)),
                ))
    return out


# ─────────────────────────────────────────────────────────────────────
# Nivel 4 — Conflictos git
# ─────────────────────────────────────────────────────────────────────


def check_conflicts(path: Path) -> list[ConflictIssue]:
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out: list[ConflictIssue] = []
    for lineno, line in enumerate(source.splitlines(), start=1):
        stripped = line.lstrip()
        # Solo los marcadores git exactos (7 caracteres) al inicio de
        # línea, seguidos de espacio o fin. Evita falsos positivos con
        # "======" en comentarios decorativos.
        for marker in ("<<<<<<<", "=======", ">>>>>>>"):
            if stripped.startswith(marker):
                after = stripped[len(marker):len(marker) + 1]
                if not after or after in (" ", "\t"):
                    out.append(ConflictIssue(line=lineno, marker=marker))
                    break
    return out


# ─────────────────────────────────────────────────────────────────────
# Orquestador
# ─────────────────────────────────────────────────────────────────────


def verify_all(path: Path) -> dict[str, list]:
    """Ejecuta los 4 niveles. Devuelve dict por categoría.

    Siempre devuelve las 4 claves para simplificar el formateo.
    """
    return {
        "syntax": check_syntax(path),
        "quality": check_quality(path),
        "secret": scan_secrets(path),
        "conflict": check_conflicts(path),
    }
    