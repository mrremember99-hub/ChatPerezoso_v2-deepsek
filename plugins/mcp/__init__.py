"""Plugin MCP. Importable sin el SDK instalado.

El núcleo puede arrancar sin MCP: ``MCPError``, ``MCPServerConfig``,
``MCPClient`` y ``MCPToolBridge`` no requieren el SDK a nivel de import.
El error por SDK ausente solo aparece al intentar conectar con un servidor.
"""
from ._base import MCPError, MCPServerConfig
from .bridge import MCPToolBridge
from .client import MCPClient

__all__ = ["MCPError", "MCPServerConfig", "MCPClient", "MCPToolBridge"]
