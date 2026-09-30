#!/usr/bin/env python3
"""Genera dossiers de codigo para auditoria externa.

Uso:
    python3 scripts/audit_dossiers.py              # completo
    python3 scripts/audit_dossiers.py --no-tests   # saltar pytest/mypy/ruff
    python3 scripts/audit_dossiers.py --out DIR    # directorio alternativo

Salida (por defecto): audit/YYYY-MM-DD/
    00_context.md       HEAD + estado + handoff + spec harness
    P1_core.md          codigo de core/ (sin harness)
    P2_harness.md       core/harness/
    P3_ui_plugins.md    ui/ + plugins/
    P4_scripts.md       scripts/ (sin tests)
    README.md           instrucciones para el auditor externo

No incluye tests por defecto (--include-tests para incluirlos).
Avisa si algun grupo supera MAX_GROUP_BYTES.
"""
from __future__ import annotations

import argparse
import shutil
import subprocess
import sys
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
DEFAULT_OUT = ROOT / "audit"

# Umbral de aviso: si un dossier supera este tamano, se avisa.
# 500 KB ~ 125k tokens. Modelos modernos lo aceptan.
MAX_GROUP_BYTES = 500_000

# Exclusiones globales (aplican a todos los grupos).
EXCLUDE_DIRS = frozenset({
    "__pycache__", ".git", ".venv", "venv", "node_modules",
    ".pytest_cache", ".mypy_cache", ".ruff_cache", ".tox",
    "build", "dist", ".idea", ".vscode",
})
EXCLUDE_SUFFIXES = frozenset({".pyc", ".pyo", ".so", ".dylib"})

# Ficheros que siempre se excluyen (test_*). Si --include-tests,
# se incluyen.
TEST_PREFIXES = ("test_",)
TEST_DIR_NAMES = frozenset({"tests", "test"})

GROUPS: list[dict] = [
    {
        "id": "P1_core",
        "name": "Core (sin harness)",
        "description": (
            "Nucleo de ChatPerezoso: cliente Ollama, workspace, "
            "tools, config, persistencia, intenciones, plugins "
            "registry, RAG, AST index, context window, shutdown. "
            "SIN core/harness/ (dossier aparte)."
        ),
        "globs": ["core/**/*.py"],
        "exclude_globs": ["core/harness/**/*.py"],
    },
    {
        "id": "P2_harness",
        "name": "Harness v3",
        "description": (
            "Harness de agente: eventos, session, loop detection, "
            "health monitoring, durable execution (EventLog + "
            "checkpoints), tool schema compiler, VRR-Stop, "
            "completion verification."
        ),
        "globs": ["core/harness/**/*.py"],
        "exclude_globs": [],
    },
    {
        "id": "P3_ui_plugins",
        "name": "UI + Plugins",
        "description": (
            "Interfaz Qt (vistas, controllers, workers, rendering) "
            "y plugins (shell, git, search, mcp, verificador)."
        ),
        "globs": ["ui/**/*.py", "plugins/**/*.py"],
        "exclude_globs": [],
    },
    {
        "id": "P4_scripts",
        "name": "Scripts",
        "description": (
            "Scripts de eval, benchmarks, bootstrap, tracker "
            "OVERPAPER, rag_bench."
        ),
        "globs": ["scripts/**/*.py", "bootstrap.py", "main.py"],
        "exclude_globs": [],
    },
]


def _is_excluded(path: Path, *, include_tests: bool) -> bool:
    """True si el path debe excluirse del dossier."""
    parts = set(path.parts)
    if parts & EXCLUDE_DIRS:
        return True
    if path.suffix in EXCLUDE_SUFFIXES:
        return True
    if not include_tests:
        if path.name.startswith(TEST_PREFIXES):
            return True
        if parts & TEST_DIR_NAMES:
            return True
    return False


def _matches_any(rel: str, globs: list[str]) -> bool:
    """True si el path relativo matchea alguno de los globs."""
    from fnmatch import fnmatch
    for g in globs:
        # Convertir "core/**/*.py" a algo que fnmatch entienda:
        # fnmatch no soporta **, asi que probamos con y sin.
        cands = [g, g.replace("**/", ""), g.replace("**", "*")]
        if any(fnmatch(rel, c) for c in cands):
            return True
    return False


def _discover_group(
    group: dict, *, include_tests: bool,
) -> list[Path]:
    """Lista los .py del grupo, filtrados y ordenados."""
    out: list[Path] = []
    for glob in group["globs"]:
        # Path.glob soporta ** en Python 3.12.
        for p in ROOT.glob(glob):
            if not p.is_file():
                continue
            if _is_excluded(p, include_tests=include_tests):
                continue
            rel = str(p.relative_to(ROOT))
            if any(
                _matches_any(rel, [eg])
                for eg in group.get("exclude_globs", [])
            ):
                continue
            if p not in out:
                out.append(p)
    return sorted(out)


def _run_cmd(cmd: list[str], *, timeout: int = 60) -> str:
    """Ejecuta un comando y devuelve stdout+stderr."""
    try:
        proc = subprocess.run(
            cmd,
            cwd=ROOT,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
        out = proc.stdout
        if proc.stderr.strip():
            out += "\n[stderr]\n" + proc.stderr
        return out
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"[error ejecutando {cmd}: {exc}]"


def _git_head() -> str:
    return _run_cmd(
        ["git", "rev-parse", "HEAD"], timeout=5,
    ).strip()


def _git_log(n: int = 10) -> str:
    return _run_cmd(
        ["git", "log", f"-{n}", "--oneline"], timeout=5,
    ).strip()


def _git_status() -> str:
    return _run_cmd(
        ["git", "status", "--short"], timeout=5,
    ).strip() or "(limpio)"


def _render_group(
    group: dict, files: list[Path], *, include_tests: bool,
) -> str:
    """Construye el .md completo de un grupo."""
    lines: list[str] = []
    lines.append(f"# {group['id']} — {group['name']}")
    lines.append("")
    lines.append(group["description"])
    lines.append("")
    total_lines = 0
    total_bytes = 0
    for p in files:
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError) as exc:
            lines.append(f"### {p.relative_to(ROOT)} — ERROR: {exc}")
            continue
        n_lines = text.count("\n") + 1
        total_lines += n_lines
        total_bytes += len(text.encode("utf-8"))
    lines.append(
        f"**Totales**: {len(files)} ficheros, "
        f"{total_lines} lineas, {total_bytes} bytes."
    )
    lines.append("")
    # Indice.
    lines.append("## Indice")
    lines.append("")
    for p in files:
        rel = str(p.relative_to(ROOT))
        try:
            n = p.read_text(encoding="utf-8").count("\n") + 1
        except (OSError, UnicodeDecodeError):
            n = 0
        lines.append(f"- `{rel}` ({n} lineas)")
    lines.append("")
    # Contenido.
    for p in files:
        rel = str(p.relative_to(ROOT))
        try:
            text = p.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        n = text.count("\n") + 1
        lines.append("---")
        lines.append("")
        lines.append(f"### `{rel}` ({n} lineas)")
        lines.append("")
        lines.append("```python")
        lines.append(text.rstrip())
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


def _render_context(
    *, include_tests: bool, no_meta: bool, out_dir: Path,
) -> str:
    """Construye 00_context.md con el estado del proyecto."""
    lines: list[str] = []
    lines.append("# ChatPerezoso v2 — Contexto para auditoria externa")
    lines.append("")
    lines.append(
        "Este dossier es una **foto del proyecto** para que un "
        "auditor externo pueda leer el codigo sin acceso al repo."
    )
    lines.append("")
    lines.append(f"**Generado**: {datetime.now().isoformat(timespec='seconds')}")
    lines.append(f"**HEAD**: `{_git_head()}`")
    lines.append("")
    lines.append("## Ultimos commits")
    lines.append("")
    lines.append("```")
    lines.append(_git_log(10))
    lines.append("```")
    lines.append("")
    lines.append("## Estado del arbol")
    lines.append("")
    lines.append("```")
    lines.append(_git_status())
    lines.append("```")
    lines.append("")
    if not no_meta:
        lines.append("## pytest")
        lines.append("")
        lines.append("```")
        lines.append(_run_cmd(
            ["python3", "-m", "pytest", "-q", "tests/"],
            timeout=120,
        )[-500:])
        lines.append("```")
        lines.append("")
        lines.append("## mypy")
        lines.append("")
        lines.append("```")
        lines.append(_run_cmd(["mypy"], timeout=120)[-500:])
        lines.append("```")
        lines.append("")
        lines.append("## ruff")
        lines.append("")
        lines.append("```")
        lines.append(_run_cmd(
            ["ruff", "check", "core/", "ui/", "plugins/", "scripts/",
             "--output-format=concise"],
            timeout=60,
        )[:2000])
        lines.append("```")
        lines.append("")
    # Documentacion interna relevante.
    lines.append("## Documentacion interna del proyecto")
    lines.append("")
    for doc in [
        ROOT / "docs" / "harness-v3.md",
        ROOT / "docs" / "handoff-2026-09-30.md",
    ]:
        if not doc.exists():
            continue
        try:
            text = doc.read_text(encoding="utf-8")
        except OSError:
            continue
        lines.append(f"### `{doc.relative_to(ROOT)}`")
        lines.append("")
        lines.append("```markdown")
        lines.append(text.rstrip())
        lines.append("```")
        lines.append("")
    return "\n".join(lines)


_README_AUDITOR = """\
# Dossiers de auditoria — ChatPerezoso v2

Este directorio contiene una foto del codigo de ChatPerezoso v2
para auditoria externa. NO es el repo completo: solo codigo de
produccion (sin tests por defecto) y documentos clave.

## Orden sugerido de lectura

1. **`00_context.md`** — HEAD, ultimos commits, salida de
   pytest/mypy/ruff, handoff y spec del harness. Leer primero.
2. **`P1_core.md`** — nucleo: cliente Ollama, workspace, tools,
   config, persistencia, RAG, AST index, context window.
3. **`P2_harness.md`** — harness de agente (lo mas nuevo):
   eventos, session, loop detection, health, durable, schemas,
   VRR, completion.
4. **`P3_ui_plugins.md`** — interfaz Qt y plugins.
5. **`P4_scripts.md`** — eval, benchmarks, bootstrap, tracker.

## Que se espera de la auditoria

- Bugs reales (no estilo).
- Invariantes de seguridad rotas.
- Contratos rotos entre modulos.
- Codigo muerto.
- Problemas de concurrencia.

## Que NO aplicar

- Refactors cosmeticos.
- Cambios de estilo.
- Optimizaciones sin datos.
- Sugerencias que ignoren los invariantes documentados en
  `docs/harness-v3.md`.

## Como citar hallazgos

Usar nomenclatura **`P#N`** (dossier + numero). Ejemplo: `P2#3`
= tercer hallazgo del dossier `P2_harness.md`. Coincide con la
convencion usada en la auditoria externa anterior (X1-X5).
"""


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--out", type=Path, default=None,
        help="Directorio de salida (default: audit/YYYY-MM-DD/)",
    )
    parser.add_argument(
        "--include-tests", action="store_true",
        help="Incluir tests en los dossiers.",
    )
    parser.add_argument(
        "--no-meta", action="store_true",
        help="No correr pytest/mypy/ruff (mas rapido).",
    )
    args = parser.parse_args()

    out_dir = args.out or (
        DEFAULT_OUT / datetime.now().strftime("%Y-%m-%d")
    )
    if out_dir.exists():
        print(f"[audit] {out_dir} ya existe; sobreescribiendo")
        shutil.rmtree(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)

    print(f"[audit] generando en {out_dir}")
    print(f"[audit] include_tests={args.include_tests} "
          f"no_meta={args.no_meta}")

    # 1. Metadata.
    context = _render_context(
        include_tests=args.include_tests,
        no_meta=args.no_meta,
        out_dir=out_dir,
    )
    ctx_path = out_dir / "00_context.md"
    ctx_path.write_text(context, encoding="utf-8")
    print(f"[audit] OK {ctx_path.name} "
          f"({len(context.encode('utf-8'))} bytes)")

    # 2. Grupos.
    for group in GROUPS:
        files = _discover_group(
            group, include_tests=args.include_tests,
        )
        if not files:
            print(f"[audit] SKIP {group['id']} (sin ficheros)")
            continue
        body = _render_group(
            group, files, include_tests=args.include_tests,
        )
        size = len(body.encode("utf-8"))
        warn = ""
        if size > MAX_GROUP_BYTES:
            warn = (
                f"  AVISO: {size} bytes > {MAX_GROUP_BYTES}. "
                "Considerar dividir el grupo."
            )
        path = out_dir / f"{group['id']}.md"
        path.write_text(body, encoding="utf-8")
        print(f"[audit] OK {path.name} "
              f"({len(files)} ficheros, {size} bytes){warn}")

    # 3. README para el auditor.
    readme = out_dir / "README.md"
    readme.write_text(_README_AUDITOR, encoding="utf-8")
    print(f"[audit] OK {readme.name}")

    print("\n[audit] Listo. Siguiente paso:")
    print(f"  ls -la {out_dir}")
    print("  Abrir 00_context.md y repartir los P*.md al auditor.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
