#!/usr/bin/env python3
"""Empaqueta ChatPerezoso en un zip limpio, sin estado local ni artefactos.

Por defecto incluye:
  · Código fuente: core/, ui/, plugins/, scripts/, tests/
  · Entry points: main.py, bootstrap.py
  · Configuración de proyecto: pyproject.toml, requirements*.txt, pytest.ini
  · Documentación: README*.md, PLAN_V2.md, TODO.md, config.example.json
  · Scripts de setup: setup.sh, setup.command, cleanup.command

Por defecto excluye:
  · Estado local del usuario: config.json, agents.json, mcp_servers.json,
    history.json, models.json
  · Workspace del agente: workspace/
  · Entorno virtual: .venv/, venv/
  · Artefactos: __pycache__/, *.pyc, .pytest_cache/, .mypy_cache/,
    .ruff_cache/, chatperezoso.egg-info/
  · Backups internos: .patches/
  · Sistema: .DS_Store, Thumbs.db

Uso:
    python scripts/make_release.py                  # nombre por defecto
    python scripts/make_release.py --output out.zip
    python scripts/make_release.py --no-tests       # sin tests/
    python scripts/make_release.py --keep-state     # incluir estado local
    python scripts/make_release.py --list           # solo listar, no zip
"""
from __future__ import annotations

import argparse
import sys
import zipfile
from datetime import datetime
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent

# Directorios que NUNCA se recorren.
EXCLUDE_DIRS: frozenset[str] = frozenset({
    # Entornos y VCS
    ".venv", "venv", "env", ".git", ".hg", ".svn",
    # Python
    "__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache",
    ".tox", "chatperezoso.egg-info", "dist", "build",
    # Editores
    ".idea", ".vscode", ".vs",
    # Node (por si acaso)
    "node_modules",
    # Estado del usuario
    "workspace",
    # Backups internos
    ".patches",
})

# Archivos que NUNCA se incluyen (ni por nombre ni por sufijo).
EXCLUDE_FILES: frozenset[str] = frozenset({
    # Sistema
    ".DS_Store", "._.DS_Store", "Thumbs.db", "desktop.ini",
    # Estado del usuario
    "config.json", "agents.json", "mcp_servers.json",
    "history.json", "models.json",
    # Artefactos
    "chatperezoso.egg-link",
})

EXCLUDE_SUFFIXES: tuple[str, ...] = (
    ".pyc", ".pyo", ".pyd", ".egg-info",
    ".zip", ".tar", ".gz",
    # Backups locales (agents.json.bak, config.json.bak-*, etc.)
    ".bak",
)


def _is_excluded(rel: Path, *, include_tests: bool,
                 keep_state: bool) -> bool:
    """Decide si una ruta relativa se excluye del zip."""
    parts = rel.parts
    if not parts:
        return False
    # Directorios prohibidos en cualquier nivel.
    if any(p in EXCLUDE_DIRS for p in parts):
        return True
    if not include_tests and "tests" in parts:
        return True
    name = parts[-1]
    # Estado del usuario: se puede reactivar con --keep-state.
    if keep_state:
        # Si keep_state, dejamos pasar los JSON de estado aunque
        # estén en EXCLUDE_FILES. El resto de reglas sigue aplicando.
        if name in ("config.json", "agents.json", "mcp_servers.json",
                    "history.json", "models.json"):
            pass
        elif name in EXCLUDE_FILES:
            return True
    else:
        if name in EXCLUDE_FILES:
            return True
    # Sufijos prohibidos.
    if any(name.endswith(suf) for suf in EXCLUDE_SUFFIXES):
        return True
    return False


def _iter_files(*, include_tests: bool, keep_state: bool) -> list[Path]:
    """Recorre el proyecto y devuelve la lista de archivos a incluir."""
    files: list[Path] = []
    for path in sorted(ROOT.rglob("*")):
        if not path.is_file():
            continue
        try:
            rel = path.relative_to(ROOT)
        except ValueError:
            continue
        if _is_excluded(rel, include_tests=include_tests,
                        keep_state=keep_state):
            continue
        files.append(rel)
    return files


def _default_output_name() -> str:
    stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
    return f"chatperezoso-{stamp}.zip"


def _human_size(n: float) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def main() -> int:
    p = argparse.ArgumentParser(
        description="Empaqueta ChatPerezoso en un zip limpio.",
    )
    p.add_argument("--output", "-o", default=None,
                   help="Nombre del zip (por defecto: chatperezoso-<fecha>.zip)")
    p.add_argument("--no-tests", action="store_true",
                   help="Excluir la carpeta tests/")
    p.add_argument("--keep-state", action="store_true",
                   help="Incluir config.json, agents.json, models.json, etc.")
    p.add_argument("--list", action="store_true",
                   help="Solo listar los archivos que se incluirían")
    p.add_argument("--quiet", "-q", action="store_true",
                   help="No imprimir la lista de archivos")
    args = p.parse_args()

    files = _iter_files(
        include_tests=not args.no_tests,
        keep_state=args.keep_state,
    )

    if not files:
        print("ERROR: no hay archivos que incluir. ¿Estás en la raíz del proyecto?",
              file=sys.stderr)
        return 1

    # Cabecera
    print(f"Proyecto: {ROOT}")
    print(f"Archivos a empaquetar: {len(files)}")
    print(f"Tests:       {'sí' if not args.no_tests else 'no'}")
    print(f"Estado local: {'sí' if args.keep_state else 'no'}")
    print()

    if args.list or not args.quiet:
        for f in files:
            print(f"  {f}")

    if args.list:
        return 0

    out_name = args.output or _default_output_name()
    out_path = (ROOT / out_name).resolve()
    if out_path.exists():
        print(f"\nYa existe {out_name}, sobreescribiendo…", file=sys.stderr)

    print(f"\nCreando {out_name}…")
    try:
        with zipfile.ZipFile(
            out_path, "w",
            compression=zipfile.ZIP_DEFLATED,
            compresslevel=9,
        ) as zf:
            for rel in files:
                zf.write(ROOT / rel, arcname=rel)
    except OSError as exc:
        print(f"ERROR al escribir el zip: {exc}", file=sys.stderr)
        return 1

    size = out_path.stat().st_size
    print(f"\n✓ {out_name}  ({_human_size(size)})")
    print(f"  {len(files)} archivos comprimidos")
    print(f"  Ruta: {out_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
