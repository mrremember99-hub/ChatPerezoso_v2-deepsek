"""Snapshot conciso del workspace para pasar entre fases.

Se usa en la orquestación determinista: cada fase recibe un listado
breve de los archivos existentes en ese momento, con tamaño y un
preview de las primeras líneas. Presupuesto: ~2 KB.

Filosofía: información justa para que el modelo sepa qué hay sin
ahogarse. Si necesita ver un archivo entero, llama a la tool.
"""
from __future__ import annotations

from pathlib import Path

from .workspace import Workspace, _SKIP_DIRS


_MAX_FILES = 25
_PREVIEW_LINES = 3
_MAX_PREVIEW_CHARS = 200
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
