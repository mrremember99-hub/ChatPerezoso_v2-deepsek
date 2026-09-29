"""Diagnóstico en profundidad del proyecto.

A diferencia del bootstrap, este script comprueba el estado real de los
subsistemas (config, agentes, MCP, plugins, permisos, workspace) e
imprime un informe detallado. No modifica nada.

Uso:
    python scripts/health_check.py

Exit code:
    0 si todas las secciones estan OK.
    1 si alguna seccion detecto un problema real (parseo fallido,
      workspace inexistente, Ollama no accesible, etc.).
    El informe visual no cambia; esto solo permite componer el
    script en `cmd && otro` o en un CI informal.
"""
from __future__ import annotations

import json
import shutil
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


def line(label: str, value: str) -> None:
    print(f"  {label:<28} {value}")


def section(title: str) -> None:
    print(f"\n\033[1m{title}\033[0m")
    print("─" * max(len(title), 40))


def check_config() -> bool:
    section("Configuración")
    config_file = ROOT / "config.json"
    if not config_file.exists():
        line("config.json", "no existe (se creará al arrancar)")
        return True
    try:
        data = json.loads(config_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        line("config.json", f"ERROR: {exc}")
        return False
    for key in ("ollama_host", "model", "workspace", "current_agent"):
        line(key, str(data.get(key, "—")))
    return True


def check_agents() -> bool:
    section("Agentes")
    agents_file = ROOT / "agents.json"
    if not agents_file.exists():
        line("agents.json", "no existe (se generarán defaults)")
        return True
    try:
        data = json.loads(agents_file.read_text(encoding="utf-8"))
    except (OSError, ValueError) as exc:
        line("agents.json", f"ERROR: {exc}")
        return False
    agents = data.get("agents", [])
    line("total", str(len(agents)))
    for agent in agents:
        allowed = agent.get("allowed_tools")
        if allowed is None:
            tools = "todas"
        else:
            tools = f"{len(allowed)} explícitas"
        line(f"· {agent.get('name', '?')}", tools)
    return True


def check_workspace() -> bool:
    section("Workspace")
    config_file = ROOT / "config.json"
    workspace = ROOT / "workspace"
    if config_file.exists():
        try:
            data = json.loads(config_file.read_text(encoding="utf-8"))
            workspace = Path(data.get("workspace", workspace))
        except (OSError, ValueError):
            pass
    line("ruta", str(workspace))
    line("existe", "sí" if workspace.exists() else "NO")
    line("es carpeta", "sí" if workspace.is_dir() else "NO")
    line("legible", "sí" if _readable(workspace) else "—")
    line("escribible", "sí" if _readable(workspace) and _writable(workspace) else "—")
    return workspace.exists() and workspace.is_dir() and _readable(workspace)


def _readable(path: Path) -> bool:
    """True si path es carpeta y podemos listar su contenido.

    No usamos os.access: con root siempre devuelve True. Probamos
    listar, que es lo que realmente nos importa.
    """
    if not path.is_dir():
        return False
    try:
        next(path.iterdir(), None)
        return True
    except OSError:
        return False


def _writable(path: Path) -> bool:
    probe = path / ".chatperezoso_probe"
    try:
        probe.write_text("", encoding="utf-8")
        probe.unlink()
        return True
    except OSError:
        return False


def check_mcp() -> None:
    section("MCP")
    mcp_file = ROOT / "mcp_servers.json"
    if not mcp_file.exists():
        line("mcp_servers.json", "no existe")
    else:
        try:
            data = json.loads(mcp_file.read_text(encoding="utf-8"))
            servers = data.get("servers", [])
            line("servidores configurados", str(len(servers)))
            for s in servers:
                line(f"· {s.get('id', '?')}",
                     f"{'activo' if s.get('enabled') else 'inactivo'}")
        except (OSError, ValueError) as exc:
            line("mcp_servers.json", f"ERROR: {exc}")
            return False
    line("SDK mcp instalado", "sí" if _module_exists("mcp") else "no")
    line("npx disponible", shutil.which("npx") or "no")
    return True


def check_plugins() -> bool:
    section("Plugins")
    try:
        from importlib.metadata import entry_points
        eps = list(entry_points(group="chatperezoso.plugins"))
        if not eps:
            line("entry points", "ninguno (instala con `pip install -e .`)")
            return False
        line("entry points", str(len(eps)))
        for ep in eps:
            line(f"· {ep.name}", ep.value)
        return True
    except Exception as exc:
        line("entry points", f"ERROR: {exc}")
        return False


def check_ollama() -> bool:
    section("Ollama")
    try:
        import httpx
    except ImportError:
        line("cliente httpx", "no instalado")
        return False
    host = "http://localhost:11434"
    config_file = ROOT / "config.json"
    if config_file.exists():
        try:
            data = json.loads(config_file.read_text(encoding="utf-8"))
            host = data.get("ollama_host", host)
        except (OSError, ValueError):
            pass
    line("host", host)
    try:
        response = httpx.get(f"{host}/api/tags", timeout=3)
        response.raise_for_status()
        models = [m.get("name", "") for m in response.json().get("models", [])]
        line("estado", "accesible")
        line("modelos", str(len(models)))
        for m in models[:5]:
            line(f"· {m}", "")
        if len(models) > 5:
            line("...", f"y {len(models) - 5} más")
        return True
    except Exception as exc:
        line("estado", f"no accesible: {exc}")
        return False


def check_local_state() -> bool:
    section("Estado local")
    for name in ("config.json", "agents.json", "mcp_servers.json",
                 "history.json"):
        path = ROOT / name
        if path.exists():
            size = path.stat().st_size
            line(name, f"existe · {size} bytes")
        else:
            line(name, "no existe")
    return True


def _module_exists(name: str) -> bool:
    import importlib.util
    return importlib.util.find_spec(name) is not None


def main() -> int:
    print("\n\033[1mChatPerezoso · Health Check\033[0m")
    print(f"  Raíz: {ROOT}")
    # X2.3b (auditoria externa 2026-09-29, P3#4): recogemos el
    # resultado de cada check y devolvemos 1 si alguno fallo. La
    # salida visual no cambia; esto permite usarlo en `&&` o CI
    # informal.
    resultados = [
        ("config", check_config()),
        ("agentes", check_agents()),
        ("workspace", check_workspace()),
        ("mcp", check_mcp()),
        ("plugins", check_plugins()),
        ("ollama", check_ollama()),
        ("estado local", check_local_state()),
    ]
    print()
    fallos = [nombre for nombre, ok in resultados if not ok]
    if fallos:
        print(
            f"  \033[1mResultado:\033[0m "
            f"problemas en: {', '.join(fallos)}"
        )
        return 1
    print("  \033[1mResultado:\033[0m todo OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
