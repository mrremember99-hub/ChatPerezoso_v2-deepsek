from __future__ import annotations

import threading
from typing import Any, Protocol, runtime_checkable

from .intent import IntentRule


@runtime_checkable
class ToolProvider(Protocol):
    """Contrato único que debe cumplir cualquier fuente de herramientas.

    ``ToolRegistry`` (núcleo), ``GitProvider`` (plugin) y
    ``MCPToolBridge`` (plugin) implementan esta interfaz.
    """

    def definitions(self) -> list[dict[str, Any]]:
        """Especificaciones de herramientas en formato Ollama."""
        ...

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        allow_destructive: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> str:
        """Ejecuta una herramienta por nombre y devuelve un resultado textual."""
        ...

    def requires_confirmation(self, name: str) -> bool:
        """Cierto si la operación necesita confirmación explícita del usuario."""
        ...

    def intent_rules(self) -> dict[str, IntentRule]:
        """Reglas de intención para cada herramienta que expone.

        El catálogo devuelto debe usar los mismos nombres que
        ``definitions()``. Si un provider no implementa este método, se
        asume que sus herramientas no pueden ser autorizadas por texto:
        el filtro anti-alucinación las bloquea siempre.
        """
        ...
