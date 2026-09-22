"""Diagnóstico manual de la conexión MCP.

Ejecútalo para ver el estado real de la conexión con el demo_server:

    python3 plugins/mcp/_diagnose.py

Imprime cada paso y tiene timeout global de 20 s.
"""
from __future__ import annotations

import asyncio
import sys
from pathlib import Path


def _log(msg: str) -> None:
    print(msg, flush=True)


def _accepted_fields(cls) -> set[str]:
    fields = getattr(cls, "model_fields", None)
    if isinstance(fields, dict):
        return set(fields.keys())
    fields_v1 = getattr(cls, "__fields__", None)
    if isinstance(fields_v1, dict):
        return set(fields_v1.keys())
    return {"command", "args", "env", "cwd"}


async def _run_diagnostic() -> None:
    _log("[1] importando SDK...")
    from mcp import Client
    from mcp.client.stdio import StdioServerParameters
    _log("    OK")

    _log("[2] inspeccionando campos aceptados...")
    accepted = _accepted_fields(StdioServerParameters)
    _log(f"    acepta: {sorted(accepted)}")

    demo = Path(__file__).resolve().parent / "demo_server.py"
    _log(f"[3] construyendo params para {demo}...")
    from typing import Any
    kwargs: dict[str, Any] = {"command": sys.executable}
    if "args" in accepted:
        kwargs["args"] = [str(demo)]
    params = StdioServerParameters(**kwargs)
    _log(f"    OK: command={params.command!r} args={params.args!r}")

    _log("[4] probando Client(params).__aenter__()...")
    try:
        async with Client(params) as client:
            _log("    OK · client obtenido")
            _log("[5] llamando client.list_tools()...")
            tools = await client.list_tools()
            tool_list = list(getattr(tools, "tools", tools) or [])
            _log(f"    OK · {len(tool_list)} herramienta(s):")
            for t in tool_list:
                _log(f"      · {getattr(t, 'name', '?')}")
    except BaseException as exc:
        _log(f"    FALLO: {type(exc).__name__}: {exc}")
        sub = getattr(exc, "exceptions", None)
        depth = 0
        while isinstance(sub, (list, tuple)) and sub and depth < 10:
            inner = sub[0]
            _log(f"      [{depth}] {type(inner).__name__}: {inner}")
            sub = getattr(inner, "exceptions", None)
            depth += 1


def main() -> int:
    try:
        asyncio.run(asyncio.wait_for(_run_diagnostic(), timeout=20.0))
    except asyncio.TimeoutError:
        _log("[!] TIMEOUT: el diagnóstico tardó más de 20 s.")
    except KeyboardInterrupt:
        _log("\n(interrumpido)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
