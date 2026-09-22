from __future__ import annotations

import logging
import threading
from typing import Any

from .intent import IntentRule
from .workspace import Workspace, WorkspaceError


logger = logging.getLogger(__name__)


_CONFIRMATION_REQUIRED = frozenset(
    {"crear_archivo", "crear_carpeta", "escribir_archivo", "borrar_archivo"}
)

_SPECS: tuple[dict[str, Any], ...] = (
    {
        "name": "listar_carpeta",
        "description": "Lista el contenido de una carpeta del workspace.",
        "properties": {
            "path": {
                "type": "string",
                "description": "Ruta relativa. Usa '.' para la raíz.",
            },
            "recursive": {
                "type": "boolean",
                "description": (
                    "Si es true, muestra el árbol completo con indentación "
                    "hasta 6 niveles. Por defecto false (solo el primer nivel)."
                ),
            },
        },
        "required": [],
    },
    {
        "name": "leer_archivo",
        "description": (
            "Lee un archivo de texto UTF-8 del workspace. Admite un rango de "
            "líneas para archivos grandes."
        ),
        "properties": {
            "path": {"type": "string", "description": "Ruta relativa del archivo."},
            "start_line": {
                "type": "integer",
                "description": "Primera línea a leer (1-indexada). Opcional.",
            },
            "end_line": {
                "type": "integer",
                "description": "Última línea a leer (inclusive). Opcional.",
            },
        },
        "required": ["path"],
    },
    {
        "name": "crear_archivo",
        "description": "Crea un archivo nuevo dentro del workspace. Falla si ya existe.",
        "properties": {
            "path": {"type": "string", "description": "Ruta relativa del archivo."},
            "content": {
                "type": "string",
                "description": "Contenido completo del archivo.",
            },
        },
        "required": ["path"],
    },
    {
        "name": "crear_carpeta",
        "description": "Crea una carpeta nueva dentro del workspace. Falla si ya existe.",
        "properties": {
            "path": {"type": "string", "description": "Ruta relativa de la carpeta."}
        },
        "required": ["path"],
    },
    {
        "name": "escribir_archivo",
        "description": "Escribe o reemplaza el contenido de un archivo dentro del workspace.",
        "properties": {
            "path": {"type": "string", "description": "Ruta relativa del archivo."},
            "content": {
                "type": "string",
                "description": "Contenido completo que sustituirá al anterior.",
            },
        },
        "required": ["path", "content"],
    },
    {
        "name": "borrar_archivo",
        "description": (
            "Borra un archivo del workspace. La aplicación solicitará confirmación explícita "
            "al usuario antes de ejecutar el borrado."
        ),
        "properties": {
            "path": {"type": "string", "description": "Ruta relativa del archivo."}
        },
        "required": ["path"],
    },
)


# Verbos que autorizan operaciones de escritura en el workspace. Se
# usa una tupla compartida para no repetir la lista en crear_archivo y
# escribir_archivo. Incluye tanto verbos de "sistema de archivos"
# (crea, escribe) como verbos de "programación" (implementa, añade):
# un usuario que dice "implementa una función X" está pidiendo
# implícitamente que se escriba código en algún archivo.
_WRITE_VERBS: tuple[str, ...] = (
    # Sistema de archivos
    "crea", "crear", "cree", "generar", "genera",
    "escribe", "escribir", "edita", "editar",
    "actualiza", "actualizar", "reemplaza", "reemplazar",
    "cambia", "cambiar", "modifica", "modificar",
    # Programación
    "implementa", "implementar", "programa", "programar",
    "desarrolla", "desarrollar", "añade", "añadir",
    "agrega", "agregar", "incluye", "incluir",
    "refactoriza", "refactorizar", "corrige", "corregir",
    "arregla", "arreglar", "completa", "completar",
)

# Palabras-objetivo que indican que la operación de escritura va sobre
# código, no solo sobre un archivo literal. Permite que "implementa una
# función que convierta X" autorice crear_archivo sin nombrar un
# archivo concreto.
_CODE_TARGETS: tuple[str, ...] = (
    "archivo", "fichero", "workspace", "proyecto",
    "script", "módulo", "modulo", "función", "funcion",
    "clase", "método", "metodo", "código", "codigo",
    "programa", "programita", "endpoint", "ruta",
    "componente", "servicio", "utilidad", "helper",
)


_RULES: dict[str, IntentRule] = {
    "listar_carpeta": IntentRule(
        verbs=("lista", "listar", "muestra", "mostrar", "contenido", "árbol", "arbol"),
        target_words=("carpeta", "directorio", "workspace", "proyecto"),
    ),
    "leer_archivo": IntentRule(
        verbs=("lee", "leer", "abre", "abrir"),
        target_words=("archivo", "fichero", "workspace"),
        accepts_filename=True,
        is_read_prerequisite=True,
    ),
    "crear_archivo": IntentRule(
        verbs=_WRITE_VERBS,
        target_words=_CODE_TARGETS,
        accepts_filename=True,
    ),
    "crear_carpeta": IntentRule(
        verbs=("crea", "crear", "cree", "generar", "genera"),
        target_words=("carpeta", "directorio"),
    ),
    "escribir_archivo": IntentRule(
        verbs=_WRITE_VERBS,
        target_words=_CODE_TARGETS,
        accepts_filename=True,
    ),
    "borrar_archivo": IntentRule(
        verbs=("borra", "borrar", "elimina", "eliminar"),
        target_words=("archivo", "fichero"),
        accepts_filename=True,
    ),
}


class ToolRegistry:
    def __init__(self, workspace: Workspace):
        self.workspace = workspace
        self._spec_by_name = {spec["name"]: spec for spec in _SPECS}

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

    def intent_rules(self) -> dict[str, IntentRule]:
        return dict(_RULES)

    def requires_confirmation(self, name: str) -> bool:
        return name in _CONFIRMATION_REQUIRED

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
        cancel_event: threading.Event | None = None,
    ) -> str:
        # `cancel_event` se acepta por compatibilidad con el Protocol
        # ToolProvider, pero las operaciones locales son I/O sincrono
        # sobre el workspace del usuario (lectura de archivos, listado)
        # y terminan en milisegundos. Cancelarlas requeriria threading
        # por operacion, que añade mas complejidad que beneficio. Los
        # providers que si tienen operaciones largas (search, shell,
        # git) implementan su propio cancel.
        del cancel_event
        spec = self._spec_by_name.get(name)
        if spec is None:
            return f"ERROR: herramienta desconocida: {name}"
        if not isinstance(arguments, dict):
            return "ERROR: los argumentos de la herramienta deben ser un objeto."

        for field in spec["required"]:
            if field not in arguments:
                return f"ERROR: falta el argumento requerido: {field}"

        for field, value in arguments.items():
            if field not in spec["properties"]:
                return f"ERROR: argumento no permitido para {name}: {field}"

        # Validación de tipos por campo, según el schema.
        for field, value in arguments.items():
            declared = spec["properties"][field].get("type", "string")
            if not _matches_type(value, declared):
                return f"ERROR: el argumento {field} debe ser {declared}."

        if name in _CONFIRMATION_REQUIRED and not allow_destructive:
            return (
                "ERROR: operación destructiva bloqueada: requiere confirmación "
                "explícita del usuario."
            )

        try:
            return self._dispatch(name, arguments)
        except WorkspaceError as exc:
            # Errores esperados del filesystem: mensaje limpio al
            # modelo, sin traza. Son validaciones de Workspace.
            return f"ERROR: {exc}"
        except Exception as exc:
            # Excepcion inesperada: suele ser un bug del codigo.
            # Loguear la traza completa y devolver un mensaje opaco
            # al modelo (no deberia ver detalles de implementacion).
            logger.exception(
                "Error inesperado en tool %s (%r)", name, arguments,
            )
            return f"ERROR interno en {name}: {type(exc).__name__}"

    def _dispatch(self, name: str, arguments: dict[str, Any]) -> str:
        if name == "listar_carpeta":
            path = arguments.get("path") or "."
            recursive = bool(arguments.get("recursive", False))
            return self.workspace.list_dir(path, recursive=recursive)
        if name == "leer_archivo":
            path = arguments["path"]
            start = arguments.get("start_line")
            end = arguments.get("end_line")
            return self.workspace.read_file(
                path,
                start_line=start if start is not None else None,
                end_line=end if end is not None else None,
            )
        if name == "crear_archivo":
            result = self.workspace.create_file(
                arguments["path"], arguments.get("content", "")
            )
            return self._with_verification(result, arguments["path"])
        if name == "crear_carpeta":
            return self.workspace.create_folder(arguments["path"])
        if name == "escribir_archivo":
            result = self.workspace.write_file(
                arguments["path"], arguments["content"]
            )
            return self._with_verification(result, arguments["path"])
        if name == "borrar_archivo":
            return self.workspace.delete_file(arguments["path"])
        return f"ERROR: herramienta desconocida: {name}"

    def _with_verification(self, result: str, path: str) -> str:
        """Añade al resultado el contenido real leído del disco tras crear o
        escribir un archivo. El modelo (y el usuario, en la caja de resultado
        de la herramienta) ven lo que hay de verdad, no lo que el modelo cree
        haber escrito — evita que un error de contenido pase desapercibido.
        """
        try:
            actual = self.workspace.read_file(path)
        except Exception:
            return result
        preview = actual if len(actual) <= 2000 else actual[:2000] + "\n…(truncado)"
        return f"{result}\n\nContenido verificado en disco:\n---\n{preview}\n---"


def _matches_type(value: Any, declared: str) -> bool:
    if declared == "string":
        return isinstance(value, str)
    if declared == "boolean":
        return isinstance(value, bool)
    if declared == "integer":
        return isinstance(value, int) and not isinstance(value, bool)
    if declared == "number":
        return isinstance(value, (int, float)) and not isinstance(value, bool)
    if declared == "array":
        return isinstance(value, list)
    if declared == "object":
        return isinstance(value, dict)
    # Tipo desconocido: rechazar antes que aceptar a ciegas. Un
    # provider con un spec mal formado no debe colar argumentos de
    # cualquier tipo a una tool.
    return False
