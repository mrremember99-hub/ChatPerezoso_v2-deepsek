"""Snapshot conciso del workspace para pasar entre fases.

Se usa en la orquestación determinista: cada fase recibe un listado
breve de los archivos existentes en ese momento, con tamaño y un
preview de las primeras líneas. Presupuesto: ~2 KB.

Filosofía: información justa para que el modelo sepa qué hay sin
ahogarse. Si necesita ver un archivo entero, llama a la tool.
"""
from __future__ import annotations

import ast
from pathlib import Path

from .workspace import Workspace, _SKIP_DIRS


_MAX_FILES = 25
_PREVIEW_LINES = 3
_MAX_PREVIEW_CHARS = 200
# Cuantos simbolos por archivo .py mostramos como maximo.
_MAX_SYMBOLS = 30
_TEXT_EXTS = {
    ".py", ".md", ".txt", ".json", ".toml", ".yaml", ".yml",
    ".ini", ".cfg", ".sh", ".js", ".ts", ".html", ".css",
    ".sql", ".rs", ".go", ".java", ".c", ".cpp", ".h",
}


def snapshot_workspace(
    workspace: Workspace,
    *,
    max_files: int = _MAX_FILES,
) -> str:
    """Devuelve un listado breve del workspace.

    Estructura:

        [ESTADO DEL WORKSPACE]
        (vacío)

    o:

        [ESTADO DEL WORKSPACE]
        - gui.py (204 líneas, 5.2 KB)
          import tkinter as tk
          from tkinter import ttk
          class OverpaperGUI:
        - core_processor.py (65 líneas, 1.9 KB)
          from PIL import Image, ImageOps
          ...
    """
    root = workspace.root
    if not root.exists():
        return "[ESTADO DEL WORKSPACE]\n(vacío)"

    files: list[Path] = []
    for p in sorted(root.rglob("*")):
        if not p.is_file():
            continue
        if any(part in _SKIP_DIRS for part in p.parts):
            continue
        if p.name.startswith("."):
            continue
        files.append(p)
        if len(files) >= max_files:
            break

    if not files:
        return "[ESTADO DEL WORKSPACE]\n(vacío)"

    lines = ["[ESTADO DEL WORKSPACE]"]
    for p in files:
        rel = p.relative_to(root)
        try:
            size = p.stat().st_size
        except OSError:
            continue
        size_str = _human_size(size)
        info = f"- {rel} ({size_str})"

        # .py: extraer interfaz publica con ast. Si funciona,
        # saltamos el preview de texto: la interfaz es mas util.
        if p.suffix.lower() == ".py" and size < 200_000:
            iface = _extract_python_interface(p)
            if iface:
                lines.append(info)
                lines.extend(iface)
                continue

        if p.suffix.lower() in _TEXT_EXTS and size < 200_000:
            preview = _read_preview(p)
            if preview:
                lines.append(info)
                for pline in preview:
                    lines.append(f"    {pline}")
                continue
        lines.append(info)

    return "\n".join(lines)


def _human_size(n: int) -> str:
    if n < 1024:
        return f"{n} B"
    if n < 1024 * 1024:
        return f"{n / 1024:.1f} KB"
    return f"{n / (1024 * 1024):.1f} MB"


def _read_preview(path: Path) -> list[str]:
    """Primeras ``_PREVIEW_LINES`` líneas no vacías, truncadas."""
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    out: list[str] = []
    for raw in text.splitlines():
        stripped = raw.rstrip()
        if not stripped:
            continue
        if len(stripped) > _MAX_PREVIEW_CHARS:
            stripped = stripped[:_MAX_PREVIEW_CHARS] + "…"
        out.append(stripped)
        if len(out) >= _PREVIEW_LINES:
            break
    return out

# ── Extraccion de interfaz publica de .py ─────────────────────────────


def _extract_python_interface(path: Path) -> list[str]:
    """Extrae clases, funciones y constantes top-level de un archivo .py.

    Devuelve lista de lineas indentadas. Vacia si el parseo falla o si
    no hay simbolos publicos (en ese caso el llamante cae al preview).
    """
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    out: list[str] = []
    count = 0
    for node in tree.body:
        if count >= _MAX_SYMBOLS:
            out.append("    ...")
            break
        if isinstance(node, ast.ClassDef):
            bases = ", ".join(_name_of(b) for b in node.bases)
            header = (
                f"  class {node.name}({bases})" if bases
                else f"  class {node.name}"
            )
            out.append(header)
            count += 1
            for item in node.body:
                if count >= _MAX_SYMBOLS:
                    break
                if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef)):
                    if item.name.startswith("_") and not item.name.startswith("__"):
                        continue
                    args = _format_args(item.args)
                    out.append(f"    def {item.name}({args})")
                    count += 1
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_") and not node.name.startswith("__"):
                continue
            args = _format_args(node.args)
            out.append(f"  def {node.name}({args})")
            count += 1
        elif isinstance(node, ast.Assign):
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    out.append(f"  {t.id} = ...")
                    count += 1
                    break

    return out


def _format_args(args: ast.arguments) -> str:
    """Solo nombres de argumentos, sin defaults ni anotaciones."""
    parts: list[str] = []
    for a in args.args:
        if a.arg in ("self", "cls"):
            continue
        parts.append(a.arg)
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    for a in args.kwonlyargs:
        parts.append(a.arg)
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")
    return ", ".join(parts)


def _name_of(node: ast.AST) -> str:
    """Nombre legible de un nodo (Name/Attribute)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name_of(node.value)}.{node.attr}"
    return "?"

