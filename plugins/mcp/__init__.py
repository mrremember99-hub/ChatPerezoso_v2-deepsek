"""Plugin opcional para conectar ChatPerezoso con servidores MCP."""

from .bridge import MCPToolBridge
from .client import MCPClient, MCPError, MCPServerConfig

__all__ = ["MCPClient", "MCPError", "MCPServerConfig", "MCPToolBridge"]
