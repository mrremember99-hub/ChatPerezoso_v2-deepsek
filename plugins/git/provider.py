"""ToolProvider del plugin Git.

Expone tres herramientas de solo lectura. Cuando el workspace no es un
repositorio Git, ``definitions()`` devuelve lista vacía y el plugin queda
invisible para el modelo.
"""
from __future__ import annotations

import threading
from typing import Any

from core.intent import IntentRule
from core.workspace import Workspace

from .client import GitClient, GitError


_GIT_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "git_status",
        "description": (
            "Muestra el estado del repositorio Git del workspace: rama actual "
            "y archivos modificados, añadidos o sin seguimiento."
        ),
        "properties": {},
        "required": [],
    },
    {
        "name": "git_diff",
        "description": (
            "Muestra las diferencias entre el estado actual del workspace y el "
            "último commit. Puede limitarse a los cambios preparados (staged) o "
            "a una ruta concreta."
        ),
        "properties": {
            "staged": {
                "type": "boolean",
                "description": "Si es true, muestra solo los cambios preparados.",
            },
            "path": {
                "type": "string",
                "description": "Ruta relativa opcional para limitar el diff.",
            },
        },
        "required": [],
    },
    {
        "name": "git_log",
        "description": "Muestra los últimos commits del repositorio.",
        "properties": {
            "limit": {
                "type": "integer",
                "description": "Número máximo de commits a mostrar. Por defecto 10, máximo 100.",
            }
        },
        "required": [],
    },
    {
        "name": "git_show",
        "description": (
            "Muestra el contenido completo de un commit: mensaje, autor y "
            "diff de los cambios. Con stat=True solo la cabecera y el resumen."
        ),
        "properties": {
            "ref": {
                "type": "string",
                "description": (
                    "Hash, rama o 'HEAD'. Por defecto 'HEAD'."
                ),
            },
            "stat": {
                "type": "boolean",
                "description": "Si es true, solo muestra el resumen de cambios.",
            },
        },
        "required": [],
    },
)


_GIT_TARGET_WORDS: tuple[str, ...] = (
    "repo", "repositorio", "git", "proyecto", "rama", "branch",
    "commit", "commits", "cambio", "cambios", "historial", "log",
)


class GitProvider:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.client = GitClient(workspace.root)
        self._available = self.client.is_repo()

    # -- ToolProvider --------------------------------------------------------

    def definitions(self) -> list[dict[str, Any]]:
        if not self._available:
            return []
        return [
            self._fn(
                spec["name"],
                spec["description"],
                spec["properties"],
                spec["required"],
            )
            for spec in _GIT_SPECS
        ]

    def requires_confirmation(self, name: str) -> bool:
        # Todas las herramientas expuestas son de solo lectura.
        return False

    def intent_rules(self) -> dict[str, IntentRule]:
        """Reglas de intención para las tres herramientas Git.

        Las herramientas Git son de solo lectura y sus verbos son muy
        específicos ("diff", "log", "status"), así que no requieren
        mencionar explícitamente el repositorio: basta con el verbo. Por
        eso se usa ``requires_target=False``. ``target_words`` queda como
        contexto adicional para futuras reglas que sí lo necesiten.
        """
        return {
            "git_status": IntentRule(
                verbs=("status", "estado", "cambios"),
                target_words=_GIT_TARGET_WORDS,
                requires_target=False,
            ),
            "git_diff": IntentRule(
                verbs=("diff", "diferencias", "cambios"),
                target_words=_GIT_TARGET_WORDS,
                requires_target=False,
            ),
            "git_log": IntentRule(
                verbs=("log", "historial", "commits"),
                target_words=_GIT_TARGET_WORDS,
                requires_target=False,
            ),
            "git_show": IntentRule(
                verbs=("show", "muestra", "detalle", "detalles"),
                target_words=_GIT_TARGET_WORDS,
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
        del allow_destructive, cancel_event
        try:
            if name == "git_status":
                return self.client.status()
            if name == "git_diff":
                staged = self._coerce_bool(arguments.get("staged", False))
                path = arguments.get("path")
                if path is not None and not isinstance(path, str):
                    return "ERROR: el argumento path debe ser texto."
                return self.client.diff(staged=staged, path=path)
            if name == "git_log":
                limit = self._coerce_int(
                    arguments.get("limit", 10), default=10, minimum=1, maximum=100
                )
                return self.client.log(limit=limit)
            if name == "git_show":
                ref = arguments.get("ref", "HEAD")
                if not isinstance(ref, str):
                    ref = "HEAD"
                stat = self._coerce_bool(arguments.get("stat", False))
                return self.client.show(ref=ref, stat=stat)
        except GitError as exc:
            return f"ERROR: {exc}"
        return f"ERROR: herramienta desconocida: {name}"

    # -- helpers -------------------------------------------------------------

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

    @staticmethod
    def _coerce_bool(value: Any) -> bool:
        if isinstance(value, bool):
            return value
        if isinstance(value, str):
            return value.strip().lower() in {"1", "true", "yes", "sí", "si"}
        return bool(value)

    @staticmethod
    def _coerce_int(value: Any, *, default: int, minimum: int, maximum: int) -> int:
        try:
            number = int(value)
        except (TypeError, ValueError):
            return default
        return max(minimum, min(maximum, number))
