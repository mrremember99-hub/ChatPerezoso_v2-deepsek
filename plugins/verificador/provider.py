"""ToolProvider del plugin de verificación de código."""
from __future__ import annotations

import threading
from pathlib import Path
from typing import Any

from core.workspace import Workspace, WorkspaceError

from .client import check_syntax


_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "verificar_sintaxis",
        "description": (
            "Verifica la sintaxis de un archivo del workspace sin "
            "ejecutarlo. Soporta Python. Otros lenguajes devuelven OK "
            "silenciosamente. Útil para confirmar que un archivo "
            "recién escrito compila antes de seguir."
        ),
        "properties": {
            "archivo": {
                "type": "string",
                "description": "Ruta relativa al workspace.",
            },
        },
        "required": ["archivo"],
    },
)

# Claves aceptadas en arguments para identificar el archivo. Los specs
# de crear_archivo/escribir_archivo usan "nombre", pero otros modelos
# pueden enviar "archivo", "path" o "ruta".
_PATH_KEYS = ("archivo", "nombre", "path", "ruta")


class VerificadorProvider:
    """Provider del plugin verificador.

    Expone una única tool (`verificar_sintaxis`) siempre visible al
    modelo. El hook automático del worker se gestiona fuera: este
    provider solo ejecuta la verificación cuando se le pide.
    """

    def __init__(self, workspace: Workspace):
        self.workspace = workspace

    # -- catálogo ------------------------------------------------------------

    def definitions(self) -> list[dict[str, Any]]:
        return [self._fn(spec) for spec in _SPECS]

    @staticmethod
    def _fn(spec: dict[str, Any]) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": spec["name"],
                "description": spec["description"],
                "parameters": {
                    "type": "object",
                    "properties": spec["properties"],
                    "required": list(spec["required"]),
                },
            },
        }

    def intent_rules(self):
        return {}

    def requires_confirmation(self, name: str) -> bool:
        # Verificar sintaxis es de solo lectura: nunca pide confirmación.
        return False

    # -- ejecución -----------------------------------------------------------

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        allow_destructive: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> str:
        if name != "verificar_sintaxis":
            return f"ERROR: herramienta desconocida: {name}"
        rel = self._extract_path(arguments)
        if rel is None:
            return "ERROR: falta el argumento 'archivo'."
        return self.verificar_archivo(rel)

    # -- API interna para el hook del worker ---------------------------------

    def verificar_archivo(self, rel_path: str) -> str:
        """Verifica un archivo y devuelve texto formateado.

        Cadena vacía si todo está bien o si el lenguaje no está
        soportado. Texto con los errores si los hay.
        """
        try:
            full = self.workspace._path(rel_path)
        except WorkspaceError as exc:
            return f"ERROR: {exc}"
        if not full.exists() or not full.is_file():
            return f"ERROR: archivo no encontrado: {rel_path}"

        issues = check_syntax(full)
        if not issues:
            return ""
        lines = [f"{rel_path}: {len(issues)} problema(s)"]
        for issue in issues:
            lines.append(f"  · {issue.format(rel_path)}")
        return "\n".join(lines)

    @staticmethod
    def _extract_path(arguments: dict[str, Any]) -> str | None:
        for key in _PATH_KEYS:
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None
