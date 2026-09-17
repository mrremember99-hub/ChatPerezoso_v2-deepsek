"""Arranque de ChatPerezoso en macOS.

Detecta el entorno (arquitectura, Homebrew, Xcode CLT, Ollama, Node),
crea lo que falte de forma idempotente, y arranca la app. Si algo no
está, sugiere el comando exacto de Homebrew para instalarlo.

Uso:
    python3 bootstrap.py              # prepara y arranca
    python3 bootstrap.py --check      # solo diagnóstico
    python3 bootstrap.py --reset --yes
    python3 bootstrap.py --no-launch
    python3 bootstrap.py --install    # instala dependencias que falten
"""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
import platform
import shutil
import subprocess
import sys
from dataclasses import dataclass, field
from pathlib import Path


# -- rutas -------------------------------------------------------------------

ROOT = Path(__file__).resolve().parent
WORKSPACE_DIR = ROOT / "workspace"
CONFIG_FILE = ROOT / "config.json"
AGENTS_FILE = ROOT / "agents.json"
MCP_FILE = ROOT / "mcp_servers.json"
HISTORY_FILE = ROOT / "history.json"
VENV_DIR = ROOT / ".venv"


IS_MAC = platform.system() == "Darwin"
IS_ARM = platform.machine() == "arm64"


# -- color (respeta NO_COLOR y TTY) ------------------------------------------

def _c(code: str, text: str) -> str:
    if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
        return text
    return f"\033[{code}m{text}\033[0m"


def green(t: str) -> str: return _c("32", t)
def red(t: str) -> str:   return _c("31", t)
def yellow(t: str) -> str: return _c("33", t)
def bold(t: str) -> str:  return _c("1", t)
def dim(t: str) -> str:   return _c("2", t)


def _h(title: str) -> None:
    print(f"\n{bold(title)}")
    print(dim("─" * max(len(title), 40)))


# -- notificaciones nativas de macOS -----------------------------------------

def notify(title: str, message: str) -> None:
    """Notificación de Centro de Notificaciones (solo en Mac, best-effort)."""
    if not IS_MAC:
        return
    try:
        subprocess.run(
            ["osascript", "-e",
             f'display notification {json.dumps(message)} with title {json.dumps(title)}'],
            check=False,
            capture_output=True,
            timeout=3,
        )
    except Exception:
        pass


# -- modelo de diagnóstico ---------------------------------------------------

@dataclass
class Check:
    name: str
    ok: bool
    detail: str = ""
    fix: str = ""            # comando o instrucción para arreglarlo
    fix_cmd: str = ""        # comando copiable (brew, etc.)
    blocking: bool = False   # si falla, no se puede arrancar


@dataclass
class Report:
    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> None:
        self.checks.append(check)

    @property
    def ok(self) -> bool:
        return all(c.ok for c in self.checks)

    @property
    def blocking_failures(self) -> list[Check]:
        return [c for c in self.checks if not c.ok and c.blocking]

    def print(self) -> None:
        for c in self.checks:
            icon = green("✓") if c.ok else red("✗")
            line = f"  {icon} {c.name}"
            if c.detail:
                line += f"  {dim('(' + c.detail + ')')}"
            print(line)
            if not c.ok and c.fix:
                print(f"      {yellow('→')} {c.fix}")
            if not c.ok and c.fix_cmd:
                print(f"        {dim('$')} {c.fix_cmd}")


# -- comprobaciones base -----------------------------------------------------

def check_macos() -> Check:
    if not IS_MAC:
        return Check(
            "macOS",
            True,
            detail=f"{platform.system()} (bootstrap funciona igual)",
        )
    version = platform.mac_ver()[0]
    arch = "Apple Silicon" if IS_ARM else "Intel"
    return Check("macOS", True, detail=f"{version} · {arch}")


def check_python() -> Check:
    v = sys.version_info
    if v < (3, 11):
        return Check(
            "Python ≥ 3.11",
            False,
            detail=f"encontrado {v.major}.{v.minor}.{v.micro}",
            fix="Instala Homebrew Python y vuelve a crear el venv.",
            fix_cmd="brew install python@3.12",
            blocking=True,
        )
    in_venv = sys.prefix != getattr(sys, "base_prefix", sys.prefix)
    return Check(
        "Python ≥ 3.11",
        True,
        detail=f"{v.major}.{v.minor}.{v.micro}"
               + (" · venv activo" if in_venv else " · fuera de venv"),
    )


def check_xcode_clt() -> Check:
    """En Mac, compilar wheels nativas requiere Xcode Command Line Tools."""
    if not IS_MAC:
        return Check("Xcode CLT", True, detail="no aplica")
    try:
        result = subprocess.run(
            ["xcode-select", "-p"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode == 0 and result.stdout.strip():
            return Check("Xcode Command Line Tools", True,
                         detail=result.stdout.strip())
    except Exception:
        pass
    return Check(
        "Xcode Command Line Tools", False,
        detail="no instaladas",
        fix="Instálalas una sola vez; tardará unos minutos.",
        fix_cmd="xcode-select --install",
    )


def check_homebrew() -> Check:
    if not IS_MAC:
        return Check("Homebrew", True, detail="no aplica")
    brew = shutil.which("brew")
    if brew:
        return Check("Homebrew", True, detail=brew)
    return Check(
        "Homebrew", False,
        detail="no instalado",
        fix="Instala Homebrew (gestor de paquetes de macOS).",
        fix_cmd=('/bin/bash -c "$(curl -fsSL '
                 'https://raw.githubusercontent.com/Homebrew/install/HEAD/install.sh)"'),
    )


def check_ollama(host: str) -> Check:
    ollama_bin = shutil.which("ollama")
    try:
        import httpx
    except ImportError:
        return Check("Ollama", False, detail="httpx no instalado",
                     fix="instala las dependencias primero",
                     blocking=True)
    try:
        response = httpx.get(f"{host}/api/tags", timeout=3)
        response.raise_for_status()
        models = response.json().get("models", [])
        detail = f"{len(models)} modelo(s) · {host}"
        if not models:
            return Check(
                "Ollama", True, detail=detail + " · sin modelos",
                fix="Descarga un modelo para poder chatear.",
                fix_cmd="ollama pull llama3.1",
            )
        return Check("Ollama", True, detail=detail)
    except Exception:
        if ollama_bin:
            return Check(
                "Ollama", False,
                detail="binario encontrado pero servidor no responde",
                fix="Arranca Ollama en otra terminal.",
                fix_cmd="ollama serve",
            )
        return Check(
            "Ollama", False,
            detail="no instalado ni accesible",
            fix="Instala Ollama desde Homebrew (versión de línea de comandos).",
            fix_cmd="brew install ollama",
        )


def check_node_if_needed() -> Check:
    """Node solo hace falta si se quiere el plugin MCP de archivos (npx)."""
    npx = shutil.which("npx")
    if npx:
        version = ""
        try:
            r = subprocess.run(["node", "--version"], capture_output=True,
                               text=True, timeout=3)
            version = r.stdout.strip()
        except Exception:
            pass
        return Check("Node / npx", True,
                     detail=f"{version} · {npx}".strip(" ·"))
    return Check(
        "Node / npx", True,   # no bloqueante
        detail="no encontrado · plugin MCP deshabilitado",
        fix="Solo si quieres activar el plugin MCP de archivos.",
        fix_cmd="brew install node",
    )


def check_dependencies() -> list[Check]:
    required = ["PySide6", "httpx", "psutil", "regex"]
    optional = ["mcp"]
    checks: list[Check] = []
    for module in required:
        found = importlib.util.find_spec(module) is not None
        checks.append(Check(
            module, found,
            fix="instala las dependencias del proyecto",
            fix_cmd="pip install -r requirements.txt",
            blocking=True,
        ))
    for module in optional:
        found = importlib.util.find_spec(module) is not None
        checks.append(Check(
            f"{module} (opcional)", True,
            detail="instalado" if found else "no instalado · plugin MCP deshabilitado",
        ))
    return checks


# -- preparación de estado local ---------------------------------------------

def ensure_workspace() -> Check:
    if WORKSPACE_DIR.is_dir():
        return Check("Workspace", True, detail=str(WORKSPACE_DIR))
    try:
        WORKSPACE_DIR.mkdir(parents=True, exist_ok=True)
        readme = WORKSPACE_DIR / "README.md"
        if not readme.exists():
            readme.write_text(
                "# Workspace de ChatPerezoso\n\n"
                "Espacio de trabajo por defecto del agente. "
                "Puedes cambiarlo desde la app.\n",
                encoding="utf-8",
            )
        return Check("Workspace", True, detail="creado")
    except OSError as exc:
        return Check("Workspace", False, fix=str(exc), blocking=True)


def ensure_config() -> Check:
    if CONFIG_FILE.exists():
        try:
            json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
            return Check("config.json", True)
        except (OSError, ValueError) as exc:
            return Check(
                "config.json", False, detail=str(exc)[:60],
                fix="Regenera el estado local.",
                fix_cmd="python3 bootstrap.py --reset --yes",
                blocking=True,
            )
    default = {
        "ollama_host": "http://localhost:11434",
        "model": "",
        "workspace": str(WORKSPACE_DIR),
        "width": 1180,
        "height": 760,
        "temperature": 0.7,
        "num_ctx": 0,
        "current_agent": "",
    }
    try:
        CONFIG_FILE.write_text(
            json.dumps(default, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return Check("config.json", True, detail="creado")
    except OSError as exc:
        return Check("config.json", False, fix=str(exc), blocking=True)


def ensure_agents() -> Check:
    if AGENTS_FILE.exists():
        try:
            data = json.loads(AGENTS_FILE.read_text(encoding="utf-8"))
            agents = data.get("agents", [])
            return Check("agents.json", True, detail=f"{len(agents)} agente(s)")
        except (OSError, ValueError) as exc:
            return Check(
                "agents.json", False, detail=str(exc)[:60],
                fix="Borra el archivo y vuelve a arrancar.",
                fix_cmd="rm agents.json",
            )
    return Check("agents.json", True, detail="se generará al arrancar")


def ensure_mcp_servers() -> Check:
    if MCP_FILE.exists():
        try:
            data = json.loads(MCP_FILE.read_text(encoding="utf-8"))
            n = len(data.get("servers", []))
            return Check("mcp_servers.json", True,
                         detail=f"{n} servidor(es)")
        except (OSError, ValueError) as exc:
            return Check(
                "mcp_servers.json", False, detail=str(exc)[:60],
                fix="Borra el archivo y vuelve a arrancar.",
                fix_cmd="rm mcp_servers.json",
            )
    default = {
        "servers": [{
            "id": "fs",
            "label": "Archivos del workspace",
            "command": "npx",
            "args": [
                "-y",
                "@modelcontextprotocol/server-filesystem",
                str(WORKSPACE_DIR),
            ],
            "env": {},
            "enabled": False,
        }],
    }
    try:
        MCP_FILE.write_text(
            json.dumps(default, ensure_ascii=False, indent=2),
            encoding="utf-8",
        )
        return Check("mcp_servers.json", True,
                     detail="creado (servidor de archivos, desactivado)")
    except OSError as exc:
        return Check("mcp_servers.json", False, fix=str(exc))


# -- instalación opcional ----------------------------------------------------

def install_missing(report: Report) -> None:
    """Sugiere/ejecuta los `fix_cmd` de brew para lo que falte."""
    _h("Instalación de dependencias del sistema")
    missing = [c for c in report.checks
               if not c.ok and c.fix_cmd.startswith("brew ")]
    if not missing:
        print("  Nada que instalar con Homebrew.")
        return

    print("  Se necesitan instalar los siguientes paquetes:")
    for c in missing:
        print(f"    · {c.name}: {dim(c.fix_cmd)}")

    answer = input("\n  ¿Ejecutar los `brew install` ahora? [y/N] ").strip().lower()
    if answer not in ("y", "yes", "s", "sí", "si"):
        print("  Omitido. Puedes copiarlos manualmente.")
        return

    for c in missing:
        print(f"\n  → {c.fix_cmd}")
        try:
            subprocess.run(c.fix_cmd.split(), check=False)
        except Exception as exc:
            print(red(f"    ✗ Falló: {exc}"))


# -- reset -------------------------------------------------------------------

def reset_local_state(confirm: bool) -> None:
    _h("Reset de estado local")
    targets = [CONFIG_FILE, AGENTS_FILE, MCP_FILE, HISTORY_FILE]
    existing = [p for p in targets if p.exists()]
    if not existing:
        print("  No hay nada que borrar.")
        return
    for p in existing:
        print(f"    · {p.name}")
    if not confirm:
        print(f"\n  Usa {bold('--reset --yes')} para confirmar.")
        return
    for p in existing:
        try:
            p.unlink()
        except OSError as exc:
            print(red(f"  ✗ {p.name}: {exc}"))
    print(f"\n  {green('✓')} {len(existing)} archivo(s) eliminado(s).")


# -- arranque ----------------------------------------------------------------

def launch() -> int:
    _h("Arrancando ChatPerezoso")
    os.chdir(ROOT)
    try:
        # Importamos aquí para no depender de PySide6 en modo --check.
        from main import main as app_main
        code = app_main()
        if code == 0:
            notify("ChatPerezoso", "Sesión finalizada.")
        return code
    except ImportError as exc:
        print(red(f"\n✗ No se pudo importar la app: {exc}"))
        print("  Revisa que las dependencias estén instaladas.")
        return 2
    except KeyboardInterrupt:
        print(dim("\n(interrumpido)"))
        return 130
    except Exception as exc:
        print(red(f"\n✗ Error inesperado: {exc}"))
        notify("ChatPerezoso", f"Error: {exc}")
        return 1


# -- main --------------------------------------------------------------------

def read_host() -> str:
    if not CONFIG_FILE.exists():
        return "http://localhost:11434"
    try:
        return json.loads(CONFIG_FILE.read_text(encoding="utf-8")).get(
            "ollama_host", "http://localhost:11434"
        )
    except (OSError, ValueError):
        return "http://localhost:11434"


def build_report() -> Report:
    r = Report()
    r.add(check_macos())
    r.add(check_python())
    if IS_MAC:
        r.add(check_xcode_clt())
        r.add(check_homebrew())
    for c in check_dependencies():
        r.add(c)
    r.add(ensure_workspace())
    r.add(ensure_config())
    r.add(ensure_agents())
    r.add(ensure_mcp_servers())
    r.add(check_ollama(read_host()))
    r.add(check_node_if_needed())
    return r


def parse_args() -> argparse.Namespace:
    p = argparse.ArgumentParser(description="Bootstrap de ChatPerezoso (macOS).")
    p.add_argument("--check", action="store_true",
                   help="solo diagnóstico, no arranca")
    p.add_argument("--no-launch", action="store_true",
                   help="prepara pero no arranca")
    p.add_argument("--reset", action="store_true",
                   help="borra config/agents/mcp/history locales")
    p.add_argument("--yes", action="store_true",
                   help="confirmación para --reset")
    p.add_argument("--install", action="store_true",
                   help="ofrece ejecutar `brew install` de lo que falte")
    return p.parse_args()


def main() -> int:
    args = parse_args()

    print(f"\n{bold('ChatPerezoso · Bootstrap')}")
    print(f"  Python {platform.python_version()} · "
          f"{platform.system()} {platform.release()}"
          + (f" · {'arm64' if IS_ARM else 'x86_64'}" if IS_MAC else ""))

    if args.reset:
        reset_local_state(confirm=args.yes)
        return 0

    report = build_report()

    _h("Diagnóstico")
    report.print()

    if args.install:
        install_missing(report)

    if report.blocking_failures:
        n = len(report.blocking_failures)
        print(f"\n{red(f'✗ {n} problema(s) bloqueante(s).')}")
        print("  Aplica los `→` de arriba y vuelve a intentarlo.")
        notify("ChatPerezoso", f"{n} problema(s) bloqueante(s) en el arranque.")
        return 1

    # Avisos no bloqueantes
    warnings = [c for c in report.checks if not c.ok and not c.blocking]
    if warnings:
        print(f"\n{yellow('⚠')} {len(warnings)} aviso(s) no bloqueante(s).")

    if args.check:
        return 0
    if args.no_launch:
        print(f"\n{green('✓')} Entorno preparado.")
        return 0

    return launch()


if __name__ == "__main__":
    raise SystemExit(main())