"""ToolProvider del plugin de verificación de código.

Cuatro niveles: sintaxis, calidad (ruff+mypy), secretos, conflictos.
El hook post-escritura llama a `verificar_archivo`, que ejecuta los
cuatro y devuelve un informe consolidado. Silencio si todo OK.
"""
from __future__ import annotations

import threading
from typing import Any

from core.workspace import Workspace, WorkspaceError
from core.intent import IntentRule

from .client import verify_all


_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "verificar_codigo",
        "description": (
            "Verifica un archivo del workspace en cuatro niveles: "
            "sintaxis (ast), calidad (ruff+mypy si están instalados), "
            "secretos hardcodeados y marcadores de conflicto git. "
            "Devuelve OK si no encuentra nada."
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

_PATH_KEYS = ("archivo", "nombre", "path", "ruta")


class VerificadorProvider:
    """Provider del plugin verificador."""

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

    def intent_rules(self) -> dict[str, IntentRule]:
        """Regla para verificar_codigo.

        Verbos de verificacion + target obligatorio (archivo,
        fichero, codigo, o un nombre de archivo con extension).

        Sin esta regla, ToolIntentGate.tool_is_requested devuelve
        False (regla ausente = bloqueo) y la tool queda inaccesible
        aunque el modelo la invoque. H4 del informe out(1).
        """
        return {
            "verificar_codigo": IntentRule(
                verbs=(
                    "verifica", "verificar",
                    "comprueba", "comprobar",
                    "valida", "validar",
                    "check", "verify",
                ),
                target_words=(
                    "archivo", "fichero", "codigo", "código",
                ),
                accepts_filename=True,
            ),
        }

    def requires_confirmation(self, name: str) -> bool:
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
        if name != "verificar_codigo":
            return f"ERROR: herramienta desconocida: {name}"
        rel = self._extract_path(arguments)
        if rel is None:
            return "ERROR: falta el argumento 'archivo'."
        out = self.verificar_archivo(rel)
        if not out:
            return f"OK: {rel} sin problemas detectados."
        return out

    # -- API interna para el hook del worker ---------------------------------

    def verificar_archivo(self, rel_path: str) -> str:
        """Ejecuta los 4 niveles. Cadena vacía si todo OK."""
        try:
            full = self.workspace._path(rel_path)
        except WorkspaceError as exc:
            return f"ERROR: {exc}"
        if not full.exists() or not full.is_file():
            return f"ERROR: archivo no encontrado: {rel_path}"

        results = verify_all(full)
        total = sum(len(v) for v in results.values())
        if total == 0:
            return ""

        lines = [f"{rel_path}: {total} problema(s)"]
        for issue in results["syntax"]:
            lines.append(f"  · {issue.format(rel_path)}")
        for issue in results["quality"]:
            lines.append(f"  · {issue.format(rel_path)}")
        for issue in results["secret"]:
            lines.append(f"  · {issue.format(rel_path)}")
        for issue in results["conflict"]:
            lines.append(f"  · {issue.format(rel_path)}")
        return "\n".join(lines)

    @staticmethod
    def _extract_path(arguments: dict[str, Any]) -> str | None:
        for key in _PATH_KEYS:
            value = arguments.get(key)
            if isinstance(value, str) and value.strip():
                return value.strip()
        return None