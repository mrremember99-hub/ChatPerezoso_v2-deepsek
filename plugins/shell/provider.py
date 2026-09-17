"""ToolProvider del plugin shell."""
from __future__ import annotations

import threading
from typing import Any

from core.intent import IntentRule
from core.workspace import Workspace

from .client import (
    DEFAULT_TIMEOUT_SECONDS,
    MAX_TIMEOUT_SECONDS,
    ShellClient,
    ShellError,
)


_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "ejecutar_comando",
        "description": (
            "Ejecuta un comando de shell simple dentro del workspace. "
            "No permite encadenar comandos ni usar pipes, redirecciones o "
            "metacaracteres. La aplicación siempre pedirá confirmación "
            "explícita al usuario antes de ejecutarlo."
        ),
        "properties": {
            "command": {
                "type": "string",
                "description": (
                    "Comando y argumentos, sin metacaracteres de shell. "
                    "Ejemplos: «ls -la», «pytest -q», «python -m compileall .»."
                ),
            },
            "cwd": {
                "type": "string",
                "description": "Ruta relativa del directorio de trabajo. Por defecto «.».",
            },
            "timeout_seconds": {
                "type": "integer",
                "description": (
                    f"Máximo tiempo de ejecución en segundos. "
                    f"Por defecto {DEFAULT_TIMEOUT_SECONDS}, máximo {MAX_TIMEOUT_SECONDS}."
                ),
            },
        },
        "required": ["command"],
    },
)


class ShellProvider:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.client = ShellClient(workspace.root)

    def definitions(self) -> list[dict[str, Any]]:
        return [
            self._fn(
                spec["name"],
                spec["description"],
                spec["properties"],
                spec["required"],
            )
            for spec in _SPECS
        ]

    def requires_confirmation(self, name: str) -> bool:
        # Siempre. Sin excepciones. Es la única garantía de seguridad
        # frente a comandos que el usuario no esperaba.
        return name == "ejecutar_comando"

    def intent_rules(self) -> dict[str, IntentRule]:
        return {
            "ejecutar_comando": IntentRule(
                verbs=(
                    "ejecuta", "ejecutar",
                    "corre", "correr",
                    "lanza", "lanzar",
                    "compila", "compilar",
                    "instala", "instalar",
                    "testea",
                    "run",
                ),
                target_words=(
                    "comando", "shell", "terminal", "script", "scripts",
                    "tests", "pruebas", "proyecto", "dependencias",
                ),
                requires_target=False,
            ),
        }

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        allow_destructive: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> str:
        if name != "ejecutar_comando":
            return f"ERROR: herramienta desconocida: {name}"
        # Defensa en profundidad: aunque `requires_confirmation()` siempre
        # devuelve True y el ChatWorker ya pide confirmación, verificamos
        # también aquí. Si algún llamante futuro invoca el provider sin
        # pasar por la UI, la operación se bloquea igualmente.
        if not allow_destructive:
            return (
                "ERROR: ejecutar_comando requiere confirmación explícita "
                "del usuario."
            )

        command = arguments.get("command")
        if not isinstance(command, str) or not command.strip():
            return "ERROR: el argumento command es obligatorio y debe ser texto."

        cwd = arguments.get("cwd", ".")
        if not isinstance(cwd, str):
            return "ERROR: el argumento cwd debe ser texto."

        try:
            timeout = int(arguments.get("timeout_seconds", DEFAULT_TIMEOUT_SECONDS))
        except (TypeError, ValueError):
            timeout = DEFAULT_TIMEOUT_SECONDS

        try:
            return self.client.execute(
                command,
                cwd=cwd,
                timeout=timeout,
                cancel_event=cancel_event,
            )
        except ShellError as exc:
            return f"ERROR: {exc}"

    @staticmethod
    def _fn(
        name: str,
        description: str,
        properties: dict,
        required: list[str],
    ) -> dict[str, Any]:
        return {
            "type": "function",
            "function": {
                "name": name,
                "description": description,
                "parameters": {
                    "type": "object",
                    "properties": properties,
                    "required": required,
                },
            },
        }
