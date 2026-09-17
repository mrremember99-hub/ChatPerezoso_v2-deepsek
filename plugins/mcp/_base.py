"""Tipos base del plugin MCP que NO dependen del SDK.

``MCPError`` y ``MCPServerConfig`` viven aquí, separados de ``client.py``,
para que ``plugins.mcp`` sea importable sin tener el SDK instalado. El núcleo
puede arrancar sin MCP: solo se lanzará un error claro cuando el usuario
intente activar un servidor y el SDK no esté disponible.
"""
from __future__ import annotations

from dataclasses import dataclass, field


class MCPError(RuntimeError):
    """Error controlado del adaptador MCP."""


@dataclass(frozen=True)
class MCPServerConfig:
    """Configuración mínima de un servidor MCP por stdio."""

    command: str
    args: tuple[str, ...] = ()
    env: dict[str, str] = field(default_factory=dict)
    cwd: str | None = None

    def __post_init__(self) -> None:
        if not self.command.strip():
            raise ValueError("El comando del servidor MCP no puede estar vacío.")
