from __future__ import annotations

from typing import Any

from .workspace import Workspace


class ToolRegistry:
    """Contrato único entre Ollama y las operaciones del workspace.

    Implementa ``core.tool_provider.ToolProvider``: define/ejecuta las
    herramientas del núcleo. ``plugins.mcp.MCPToolBridge`` implementa el
    mismo contrato para las herramientas de un servidor MCP.
    """

    _CONFIRMATION_REQUIRED = frozenset(
        {"crear_archivo", "crear_carpeta", "escribir_archivo", "borrar_archivo"}
    )

    _SPECS: tuple[dict[str, Any], ...] = (
        {
            "name": "listar_carpeta",
            "description": "Lista el contenido de una carpeta del workspace.",
            "properties": {"path": {"type": "string", "description": "Ruta relativa. Usa '.' para la raíz."}},
            "required": [],
        },
        {
            "name": "leer_archivo",
            "description": "Lee un archivo de texto UTF-8 del workspace.",
            "properties": {"path": {"type": "string", "description": "Ruta relativa del archivo."}},
            "required": ["path"],
        },
        {
            "name": "crear_archivo",
            "description": "Crea un archivo nuevo dentro del workspace. Falla si ya existe.",
            "properties": {
                "path": {"type": "string", "description": "Ruta relativa del archivo."},
                "content": {"type": "string", "description": "Contenido completo del archivo."},
            },
            "required": ["path"],
        },
        {
            "name": "crear_carpeta",
            "description": "Crea una carpeta nueva dentro del workspace. Falla si ya existe.",
            "properties": {"path": {"type": "string", "description": "Ruta relativa de la carpeta."}},
            "required": ["path"],
        },
        {
            "name": "escribir_archivo",
            "description": "Escribe o reemplaza el contenido de un archivo dentro del workspace.",
            "properties": {
                "path": {"type": "string", "description": "Ruta relativa del archivo."},
                "content": {"type": "string", "description": "Contenido completo que sustituirá al anterior."},
            },
            "required": ["path", "content"],
        },
        {
            "name": "borrar_archivo",
            "description": (
                "Borra un archivo del workspace. La aplicación solicitará confirmación explícita "
                "al usuario antes de ejecutar el borrado."
            ),
            "properties": {"path": {"type": "string", "description": "Ruta relativa del archivo."}},
            "required": ["path"],
        },
    )

    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self._handlers = {
            "listar_carpeta": self.workspace.list_dir,
            "leer_archivo": self.workspace.read_file,
            "crear_archivo": self.workspace.create_file,
            "crear_carpeta": self.workspace.create_folder,
            "escribir_archivo": self.workspace.write_file,
            "borrar_archivo": self.workspace.delete_file,
        }
        self._spec_by_name = {spec["name"]: spec for spec in self._SPECS}

    def definitions(self) -> list[dict[str, Any]]:
        """Devuelve siempre el mismo contrato, sin depender del workspace."""
        return [
            self._fn(
                spec["name"],
                spec["description"],
                spec["properties"],
                spec["required"],
            )
            for spec in self._SPECS
        ]

    @classmethod
    def requires_confirmation(cls, name: str) -> bool:
        """Indica si la herramienta puede escribir en el workspace."""
        return name in cls._CONFIRMATION_REQUIRED

    @staticmethod
    def _fn(name: str, description: str, properties: dict, required: list[str]) -> dict:
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

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        allow_destructive: bool = False,
    ) -> str:
        """Ejecuta una herramienta y devuelve un resultado textual estable.

        Las operaciones destructivas requieren autorización explícita de la interfaz.
        """
        spec = self._spec_by_name.get(name)
        handler = self._handlers.get(name)
        if spec is None or handler is None:
            return f"ERROR: herramienta desconocida: {name}"
        if not isinstance(arguments, dict):
            return "ERROR: los argumentos de la herramienta deben ser un objeto."

        for field in spec["required"]:
            if field not in arguments:
                return f"ERROR: falta el argumento requerido: {field}"

        for field, value in arguments.items():
            if field not in spec["properties"]:
                return f"ERROR: argumento no permitido para {name}: {field}"
            if not isinstance(value, str):
                return f"ERROR: el argumento {field} debe ser texto."

        if name in self._CONFIRMATION_REQUIRED and not allow_destructive:
            return "ERROR: operación destructiva bloqueada: requiere confirmación explícita del usuario."

        try:
            if name == "listar_carpeta" and not arguments.get("path"):
                arguments = {**arguments, "path": "."}
            return str(handler(**arguments))
        except Exception as exc:
            return f"ERROR: {exc}"
