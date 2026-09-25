"""ToolProvider del plugin de búsqueda."""
from __future__ import annotations

import threading
from typing import Any

from core.intent import IntentRule
from core.workspace import Workspace

from .client import SearchClient, SearchError


_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "buscar_en_workspace",
        "description": (
            "Busca un patrón de texto (expresión regular) en los archivos del "
            "workspace. Devuelve coincidencias con ruta, número de línea y "
            "contenido. Ignora archivos binarios y directorios de caché."
        ),
        "properties": {
            "query": {
                "type": "string",
                "description": "Patrón a buscar. Se interpreta como expresión regular.",
            },
            "path": {
                "type": "string",
                "description": "Ruta relativa desde la que buscar. Por defecto la raíz.",
            },
            "case_sensitive": {
                "type": "boolean",
                "description": "Si es true, la búsqueda distingue mayúsculas. Por defecto false.",
            },
            "max_matches": {
                "type": "integer",
                "description": "Máximo de coincidencias a devolver. Por defecto 50, máximo 200.",
            },
            "extensions": {
                "type": "array",
                "items": {"type": "string"},
                "description": (
                    "Lista opcional de extensiones a considerar. Por ejemplo "
                    "[py, md]. Si se omite, se buscan todos los archivos de texto."
                ),
            },
        },
        "required": ["query"],
    },
)


class SearchProvider:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self.client = SearchClient(workspace.root)

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
        return False

    def intent_rules(self) -> dict[str, IntentRule]:
        return {
            "buscar_en_workspace": IntentRule(
                # Verbos fuertes: "busca X" autoriza aunque X no sea
                # una palabra de target_words (es una búsqueda real).
                verbs=("busca", "buscar", "encuentra", "encontrar"),
                # Verbos débiles: "¿dónde está X?" solo autoriza si X
                # parece del proyecto (target_word o filename). Evita
                # falsos positivos como "¿dónde está Asturias?".
                weak_verbs=("dónde", "donde"),
                target_words=(
                    "workspace", "proyecto", "archivo", "archivos",
                    "fichero", "ficheros", "código", "codigo",
                    "función", "funcion", "funciones", "funcions",
                    "clase", "clases", "método", "metodo",
                    "métodos", "metodos", "variable", "variables",
                    "definición", "definicion", "implementación",
                    "implementacion",
                ),
                # Los verbos fuertes NO exigen target, así que la
                # regla no lo exige globalmente. Los débiles lo exigen
                # por su propia rama en el gate.
                requires_target=False,
                # Habilita "¿dónde está main.py?".
                accepts_filename=True,
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
        del allow_destructive
        if name != "buscar_en_workspace":
            return f"ERROR: herramienta desconocida: {name}"

        query = arguments.get("query")
        if not isinstance(query, str):
            return "ERROR: el argumento query es obligatorio y debe ser texto."

        path = arguments.get("path", ".")
        if not isinstance(path, str):
            return "ERROR: el argumento path debe ser texto."

        case_sensitive = bool(arguments.get("case_sensitive", False))

        try:
            max_matches = int(arguments.get("max_matches", 50))
        except (TypeError, ValueError):
            max_matches = 50

        raw_extensions = arguments.get("extensions")
        extensions: list[str] | None = None
        if isinstance(raw_extensions, list):
            extensions = [e for e in raw_extensions if isinstance(e, str)]

        try:
            return self.client.search(
                query,
                path=path,
                case_sensitive=case_sensitive,
                max_matches=max_matches,
                extensions=extensions,
                cancel_event=cancel_event,
            )
        except SearchError as exc:
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
