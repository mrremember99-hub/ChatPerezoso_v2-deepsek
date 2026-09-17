"""Analiza, comprueba y limpia el proyecto ChatPerezoso.

Hace tres cosas:

1. VERIFICA que todos los archivos esperados existen y que los módulos
   del proyecto importan sin error.
2. ANALIZA qué hay de más: caches, temporales, archivos legacy que
   quedaron en la raíz cuando el código se movió a core/ y ui/.
3. LIMPIA lo que sobra, siempre con confirmación explícita.

Modos:
    python scripts/cleanup.py               # solo análisis (recomendado)
    python scripts/cleanup.py --apply       # borra caches y temporales
    python scripts/cleanup.py --aggressive  # + legacy (con confirmación)
    python scripts/cleanup.py --aggressive --yes  # sin preguntar
    python scripts/cleanup.py --json        # salida en JSON
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


ROOT = Path(__file__).resolve().parent.parent


# -- listas de archivos esperados --------------------------------------------

# Archivos imprescindibles del proyecto. Si alguno falta, lo avisamos.
# (ruta relativa : descripción corta)
EXPECTED_FILES: dict[str, str] = {
    # raíz
    "bootstrap.py": "arranque",
    "main.py": "punto de entrada",
    "pyproject.toml": "entry points de plugins",
    "requirements.txt": "dependencias base",
    "requirements-dev.txt": "dependencias de desarrollo",
    ".gitignore": "exclusiones de git",

    # core
    "core/__init__.py": "",
    "core/agents.py": "agentes",
    "core/composite_tools.py": "composición de providers",
    "core/config.py": "configuración",
    "core/history.py": "historial",
    "core/intent.py": "reglas de intención",
    "core/mcp_servers.py": "registro MCP",
    "core/ollama.py": "cliente Ollama",
    "core/plugins_registry.py": "descubrimiento de plugins",
    "core/tool_provider.py": "protocolo de provider",
    "core/tool_result.py": "resultado estructurado",
    "core/tools.py": "herramientas del núcleo",
    "core/workspace.py": "workspace",

    # ui
    "ui/__init__.py": "",
    "ui/design.py": "tokens de diseño",
    "ui/diagnostics.py": "estadísticas",
    "ui/theme.py": "estilos QSS",
    "ui/widgets.py": "widgets base",
    "ui/workers.py": "workers",

    # ui/controllers
    "ui/controllers/__init__.py": "",
    "ui/controllers/agent_controller.py": "",
    "ui/controllers/app_controller.py": "",
    "ui/controllers/chat_controller.py": "",
    "ui/controllers/diagnostics_controller.py": "",
    "ui/controllers/mcp_controller.py": "",
    "ui/controllers/model_controller.py": "",

    # ui/rendering
    "ui/rendering/__init__.py": "",
    "ui/rendering/plain_text.py": "",
    "ui/rendering/protocol.py": "",

    # ui/views
    "ui/views/__init__.py": "",
    "ui/views/chat_panel.py": "",
    "ui/views/dialogs.py": "",
    "ui/views/diagnostics_panel.py": "",
    "ui/views/main_window.py": "",
    "ui/views/sidebar.py": "",

    # plugins
    "plugins/__init__.py": "",
    "plugins/git/__init__.py": "",
    "plugins/git/client.py": "",
    "plugins/git/provider.py": "",
    "plugins/mcp/__init__.py": "",
    "plugins/mcp/_base.py": "",
    "plugins/mcp/bridge.py": "",
    "plugins/mcp/client.py": "",
    "plugins/mcp/requirements.txt": "",
    "plugins/search/__init__.py": "",
    "plugins/search/client.py": "",
    "plugins/search/provider.py": "",
    "plugins/shell/__init__.py": "",
    "plugins/shell/client.py": "",
    "plugins/shell/provider.py": "",

    # scripts
    "scripts/health_check.py": "",
}


# Archivos que quedaron en la raíz cuando el código se movió a core/ y ui/.
# Solo se borran con --aggressive.
LEGACY_FILES: dict[str, str] = {
    # (raíz) : (dónde vive ahora)
    "agents.py": "core/agents.py",
    "composite_tools.py": "core/composite_tools.py",
    "config.py": "core/config.py",
    "history.py": "core/history.py",
    "intent.py": "core/intent.py",
    "ollama.py": "core/ollama.py",
    "tool_provider.py": "core/tool_provider.py",
    "tools.py": "core/tools.py",
    "workspace.py": "core/workspace.py",
    "workers.py": "ui/workers.py",
    "widgets.py": "ui/widgets.py",
    "design.py": "ui/design.py",
    "theme.py": "ui/theme.py",
    "diagnostics.py": "ui/diagnostics.py",
    "sidebar.py": "ui/views/sidebar.py",
    "dialogs.py": "ui/views/dialogs.py",
    "chat_panel.py": "ui/views/chat_panel.py",
    "main_window.py": "ui/views/main_window.py",
    "diagnostics_panel.py": "ui/views/diagnostics_panel.py",
}


# Archivos que JAMÁS se borran (estado del usuario o el venv).
NEVER_DELETE: set[str] = {
    "config.json",
    "agents.json",
    "mcp_servers.json",
    "history.json",
    "workspace",
    ".venv",
    ".git",
    ".gitignore",
}


# -- categorías de limpieza --------------------------------------------------

CACHE_DIRS: set[str] = {
    "__pycache__",
    ".pytest_cache",
    ".mypy_cache",
    ".ruff_cache",
    ".tox",
    ".coverage_html",
    "htmlcov",
}

CACHE_FILES: set[str] = {
    ".DS_Store",
    "._.DS_Store",
    "Thumbs.db",
}

# Extensiones de archivos temporales que se borran sin piedad.
CACHE_SUFFIXES: tuple[str, ...] = (".pyc", ".pyo", ".pyd")


# -- estructuras de datos ----------------------------------------------------

@dataclass
class Finding:
    category: str        # "cache" | "legacy" | "unknown" | "missing"
    path: str            # ruta relativa
    detail: str = ""     # por qué se clasifica así
    size: int = 0        # bytes (si aplica)


@dataclass
class Report:
    findings: list[Finding] = field(default_factory=list)
    missing_imports: list[tuple[str, str]] = field(default_factory=list)

    def add(self, f: Finding) -> None:
        self.findings.append(f)

    def by_category(self, cat: str) -> list[Finding]:
        return [f for f in self.findings if f.category == cat]

    @property
    def cache_size(self) -> int:
        return sum(f.size for f in self.by_category("cache"))

    @property
    def legacy_size(self) -> int:
        return sum(f.size for f in self.by_category("legacy"))

    @property
    def missing_files(self) -> list[Finding]:
        return self.by_category("missing")


# -- verificación -------------------------------------------------------------

def check_expected_files() -> list[Finding]:
    """Devuelve las rutas esperadas que faltan."""
    missing: list[Finding] = []
    for rel, desc in EXPECTED_FILES.items():
        if not (ROOT / rel).exists():
            detail = desc or "(sin descripción)"
            missing.append(Finding("missing", rel, detail))
    return missing


def check_imports() -> list[tuple[str, str]]:
    """Intenta importar los módulos clave. Devuelve [(módulo, error)]."""
    modules = [
        "core.ollama", "core.tools", "core.workspace", "core.agents",
        "core.config", "core.history", "core.intent",
        "core.tool_result", "core.tool_provider", "core.composite_tools",
        "core.mcp_servers", "core.plugins_registry",
        "ui.workers", "ui.design", "ui.theme",
        "ui.rendering.plain_text", "ui.rendering.protocol",
        "ui.controllers.app_controller",
        "ui.controllers.chat_controller",
        "ui.controllers.mcp_controller",
        "ui.views.sidebar",
        "plugins.git", "plugins.search", "plugins.shell",
    ]
    errors: list[tuple[str, str]] = []
    # Nos aseguramos de que la raíz está en sys.path para importar el proyecto.
    if str(ROOT) not in sys.path:
        sys.path.insert(0, str(ROOT))
    for mod in modules:
        try:
            if importlib.util.find_spec(mod) is None:
                errors.append((mod, "no encontrado"))
                continue
            # No ejecutamos el import real (algunos como ollama abren httpx).
            # find_spec es suficiente para saber que el módulo existe.
        except Exception as exc:
            errors.append((mod, str(exc)))
    return errors


# -- análisis -----------------------------------------------------------------

def _size_of(path: Path) -> int:
    if path.is_file():
        try:
            return path.stat().st_size
        except OSError:
            return 0
    total = 0
    try:
        for root, _, files in os.walk(path):
            for name in files:
                try:
                    total += (Path(root) / name).stat().st_size
                except OSError:
                    pass
    except OSError:
        pass
    return total


def find_cache_dirs() -> list[Finding]:
    findings: list[Finding] = []
    for root, dirs, _ in os.walk(ROOT):
        # No entramos en el venv ni en .git: son ajenos al proyecto.
        dirs[:] = [
            d for d in dirs
            if d not in {".venv", "venv", ".git"} and d not in NEVER_DELETE
        ]
        for d in list(dirs):
            if d in CACHE_DIRS:
                path = Path(root) / d
                rel = path.relative_to(ROOT)
                findings.append(Finding(
                    "cache", str(rel),
                    detail=f"directorio de caché ({d})",
                    size=_size_of(path),
                ))
                dirs.remove(d)  # no bajar dentro: se borra entero
    return findings


def find_cache_files() -> list[Finding]:
    findings: list[Finding] = []
    for root, dirs, files in os.walk(ROOT):
        dirs[:] = [
            d for d in dirs
            if d not in {".venv", "venv", ".git"} and d not in NEVER_DELETE
        ]
        # Saltamos los directorios ya detectados como cache (evita duplicados).
        dirs[:] = [d for d in dirs if d not in CACHE_DIRS]
        for name in files:
            path = Path(root) / name
            if name in CACHE_FILES or name.endswith(CACHE_SUFFIXES):
                rel = path.relative_to(ROOT)
                findings.append(Finding(
                    "cache", str(rel),
                    detail="archivo de caché temporal",
                    size=_size_of(path),
                ))
    return findings


def find_legacy_files() -> list[Finding]:
    findings: list[Finding] = []
    for rel, now_at in LEGACY_FILES.items():
        path = ROOT / rel
        if path.is_file() and (ROOT / now_at).exists():
            findings.append(Finding(
                "legacy", rel,
                detail=f"movido a {now_at}",
                size=_size_of(path),
            ))
    return findings


def find_unknown_root_files() -> list[Finding]:
    """Archivos sueltos en la raíz que no reconocemos.

    Solo se reportan para que el usuario decida. Nunca se borran
    automáticamente.
    """
    known = set(EXPECTED_FILES.keys()) | set(LEGACY_FILES.keys()) | NEVER_DELETE
    known.update({
        # Scripts de arranque y limpieza
        "setup.command", "setup.sh", "setup.bat",
        "cleanup.command", "run.command",
        # Documentación
        "README.md", "README-mac.md", "CHANGELOG.md", "LICENSE",
        "PLAN_V2.md", "TODO.md",
        # Configuración del proyecto
        "Makefile", "pytest.ini", "tox.ini", "setup.cfg",
        "config.example.json", "mcp_servers.example.json",
        # Artefactos de pip install -e . (se regeneran solos)
        "chatperezoso.egg-info",
        "chatperezoso.egg-link",
        # Carpetas del propio proyecto
        "tests",
        "docs",
        "examples",
    })

    findings: list[Finding] = []
    try:
        for entry in sorted(ROOT.iterdir()):
            name = entry.name
            if name.startswith("."):
                continue
            if name in known:
                continue
            if entry.is_dir():
                if name in {"core", "ui", "plugins", "scripts",
                            "__pycache__", ".venv", "workspace"}:
                    continue
                findings.append(Finding(
                    "unknown", name,
                    detail="directorio no reconocido en la raíz",
                    size=_size_of(entry),
                ))
            else:
                findings.append(Finding(
                    "unknown", name,
                    detail="archivo no reconocido en la raíz",
                    size=_size_of(entry),
                ))
    except OSError:
        pass
    return findings


def build_report() -> Report:
    report = Report()
    for f in check_expected_files():
        report.add(f)
    for f in find_cache_dirs():
        report.add(f)
    for f in find_cache_files():
        report.add(f)
    for f in find_legacy_files():
        report.add(f)
    for f in find_unknown_root_files():
        report.add(f)
    report.missing_imports = check_imports()
    return report


# -- presentación -------------------------------------------------------------

def _c(code: str, text: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"\033[{code}m{text}\033[0m"


def green(t): return _c("32", t)
def red(t):   return _c("31", t)
def yellow(t): return _c("33", t)
def dim(t):   return _c("2", t)
def bold(t):  return _c("1", t)


def fmt_size(n: int) -> str:
    for unit in ("B", "KB", "MB", "GB"):
        if n < 1024:
            return f"{n:.0f} {unit}" if unit == "B" else f"{n:.1f} {unit}"
        n /= 1024
    return f"{n:.1f} TB"


def print_report(report: Report) -> None:
    print(f"\n{bold('ChatPerezoso · Análisis de limpieza')}")
    print(dim(f"  {ROOT}"))

    # 1. Archivos faltantes
    missing = report.missing_files
    print(f"\n{bold('Verificación de archivos esperados')}")
    if missing:
        for f in missing:
            print(f"  {red('✗')} {f.path}  {dim('(' + f.detail + ')')}")
    else:
        print(f"  {green('✓')} Todos los archivos esperados están en su sitio.")

    # 2. Imports
    print(f"\n{bold('Verificación de módulos')}")
    if report.missing_imports:
        for mod, err in report.missing_imports:
            print(f"  {red('✗')} {mod}: {err}")
    else:
        print(f"  {green('✓')} Todos los módulos del proyecto se localizan.")

    # 3. Cachés
    caches = report.by_category("cache")
    print(f"\n{bold('Cachés y temporales')}")
    if caches:
        print(f"  {yellow('!')} {len(caches)} elemento(s) · "
              f"{fmt_size(report.cache_size)}")
        for f in caches[:10]:
            print(f"    · {f.path}  {dim('(' + fmt_size(f.size) + ')')}")
        if len(caches) > 10:
            print(f"    … y {len(caches) - 10} más")
    else:
        print(f"  {green('✓')} Nada que limpiar.")

    # 4. Legacy
    legacy = report.by_category("legacy")
    print(f"\n{bold('Archivos legacy (movidos a otra carpeta)')}")
    if legacy:
        print(f"  {yellow('!')} {len(legacy)} elemento(s) · "
              f"{fmt_size(report.legacy_size)}")
        for f in legacy:
            print(f"    · {f.path}  {dim('→ ' + f.detail)}")
    else:
        print(f"  {green('✓')} Nada que limpiar.")

    # 5. Desconocidos
    unknown = report.by_category("unknown")
    print(f"\n{bold('Archivos desconocidos en la raíz')}")
    if unknown:
        print(f"  {yellow('!')} {len(unknown)} elemento(s)")
        for f in unknown:
            print(f"    · {f.path}  {dim('(' + fmt_size(f.size) + ')')}")
        print(f"  {dim('No se borrarán automáticamente. Revísalos tú.')}")
    else:
        print(f"  {green('✓')} Nada inesperado.")

    # Resumen final
    print(f"\n{bold('Resumen')}")
    total_cache = report.cache_size
    total_legacy = report.legacy_size
    print(f"  Archivos faltantes: {len(missing)}")
    print(f"  Cachés: {len(caches)} · {fmt_size(total_cache)}")
    print(f"  Legacy: {len(legacy)} · {fmt_size(total_legacy)}")
    print(f"  Desconocidos: {len(unknown)}")


# -- limpieza -----------------------------------------------------------------

def _confirm(prompt: str, assume_yes: bool) -> bool:
    if assume_yes:
        return True
    answer = input(f"{prompt} [y/N] ").strip().lower()
    return answer in ("y", "yes", "s", "sí", "si")


def _delete(path: Path) -> bool:
    """Borra un archivo o directorio. Devuelve True si tuvo éxito."""
    try:
        if path.is_dir():
            shutil.rmtree(path)
        else:
            path.unlink()
        return True
    except OSError as exc:
        print(f"  {red('✗')} {path.relative_to(ROOT)}: {exc}")
        return False


def apply_cleanup(
    report: Report,
    *,
    include_legacy: bool,
    assume_yes: bool,
) -> tuple[int, int]:
    """Ejecuta la limpieza. Devuelve (borrados, errores)."""
    deleted = 0
    errors = 0

    # Cachés (siempre)
    caches = report.by_category("cache")
    if caches:
        print(f"\n{bold('Borrando cachés y temporales')}")
        if not _confirm(
            f"  ¿Borrar {len(caches)} caché(s) ({fmt_size(report.cache_size)})?",
            assume_yes,
        ):
            print(dim("  Omitido."))
        else:
            for f in caches:
                if _delete(ROOT / f.path):
                    print(f"  {green('✓')} {f.path}")
                    deleted += 1
                else:
                    errors += 1

    # Legacy (solo con --aggressive)
    if include_legacy:
        legacy = report.by_category("legacy")
        if legacy:
            print(f"\n{bold('Borrando archivos legacy')}")
            print(dim(
                "  Estos archivos son duplicados: el código vive ahora en "
                "core/ o ui/."
            ))
            for f in legacy:
                print(f"    · {f.path}  {dim('→ ' + f.detail)}")
            if not _confirm(
                f"  ¿Borrar {len(legacy)} archivo(s) legacy "
                f"({fmt_size(report.legacy_size)})?",
                assume_yes,
            ):
                print(dim("  Omitido."))
            else:
                for f in legacy:
                    if _delete(ROOT / f.path):
                        print(f"  {green('✓')} {f.path}")
                        deleted += 1
                    else:
                        errors += 1
    else:
        legacy = report.by_category("legacy")
        if legacy:
            print(f"\n{dim('Archivos legacy detectados. Usa --aggressive para borrarlos.')}")

    # Desconocidos (nunca automático)
    unknown = report.by_category("unknown")
    if unknown:
        print(f"\n{bold('Archivos desconocidos')}")
        print(dim(
            "  No se borran automáticamente. Si sobra alguno, bórralo tú."
        ))
        for f in unknown:
            print(f"    · {f.path}")

    return deleted, errors


# -- salida JSON --------------------------------------------------------------

def dump_json(report: Report) -> None:
    payload = {
        "root": str(ROOT),
        "missing": [
            {"path": f.path, "detail": f.detail}
            for f in report.missing_files
        ],
        "missing_imports": [
            {"module": m, "error": e} for m, e in report.missing_imports
        ],
        "cache": [
            {"path": f.path, "size": f.size, "detail": f.detail}
            for f in report.by_category("cache")
        ],
        "legacy": [
            {"path": f.path, "size": f.size, "detail": f.detail}
            for f in report.by_category("legacy")
        ],
        "unknown": [
            {"path": f.path, "size": f.size, "detail": f.detail}
            for f in report.by_category("unknown")
        ],
        "totals": {
            "cache_bytes": report.cache_size,
            "legacy_bytes": report.legacy_size,
        },
    }
    print(json.dumps(payload, ensure_ascii=False, indent=2))


# -- main ---------------------------------------------------------------------

def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(
        description="Analiza y limpia el proyecto ChatPerezoso.",
    )
    p.add_argument("--apply", action="store_true",
                   help="borra caches y temporales")
    p.add_argument("--aggressive", action="store_true",
                   help="borra también archivos legacy (pide confirmación)")
    p.add_argument("--yes", "-y", action="store_true",
                   help="no pide confirmación")
    p.add_argument("--json", action="store_true",
                   help="salida en JSON (sin colores)")
    return p.parse_args()


def main() -> int:
    args = parse_args()
    report = build_report()

    if args.json:
        dump_json(report)
        return 0

    print_report(report)

    if args.apply or args.aggressive:
        deleted, errors = apply_cleanup(
            report,
            include_legacy=args.aggressive,
            assume_yes=args.yes,
        )
        print(f"\n{bold('Resultado')}")
        print(f"  {green('✓')} {deleted} elemento(s) borrado(s)")
        if errors:
            print(f"  {red('✗')} {errors} error(es)")
        return 0 if errors == 0 else 1

    print(f"\n{dim('Modo análisis. Nada se ha borrado.')}")
    print(f"{dim('Usa')} python scripts/cleanup.py --apply "
          f"{dim('para borrar cachés.')}")
    print(f"{dim('Usa')} python scripts/cleanup.py --aggressive "
          f"{dim('para borrar también legacy.')}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())