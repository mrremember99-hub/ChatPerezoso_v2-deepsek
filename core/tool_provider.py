from __future__ import annotations

from typing import Any, Protocol, runtime_checkable


@runtime_checkable
class ToolProvider(Protocol):
    """Contrato único que debe cumplir cualquier fuente de herramientas.

    ``ToolRegistry`` (núcleo) y ``MCPToolBridge`` (plugin) implementan esta
    misma interfaz. La UI y ``OllamaClient`` solo conocen este contrato, no
    la implementación concreta: cualquier plugin futuro que quiera exponer
    herramientas al modelo solo necesita cumplirlo.
    """

    def definitions(self) -> list[dict[str, Any]]:
        """Especificaciones de herramientas en formato Ollama (function calling)."""
        ...

    def call(self, name: str, arguments: dict[str, Any], *, allow_destructive: bool = False) -> str:
        """Ejecuta una herramienta por nombre y devuelve un resultado textual."""
        ...

    def requires_confirmation(self, name: str) -> bool:
        """Cierto si la operación necesita confirmación explícita del usuario."""
        ...
