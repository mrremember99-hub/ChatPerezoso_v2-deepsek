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
            "numbered": {
                "type": "boolean",
                "description": (
                    "Si true, prefija cada linea con su numero "
                    "(formato '1| texto'). Util antes de usar "
                    "insertar_en_archivo o editar_archivo."
                ),
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
        "required": ["path", "content"],
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
        "name": "editar_archivo",
        "description": (
            "Reemplaza un fragmento exacto dentro de un archivo "
            "existente. Mucho mas eficiente que escribir_archivo "
            "para cambios puntuales: no hay que reescribir el "
            "archivo completo. Falla si old_string no aparece o "
            "si aparece varias veces sin replace_all."
        ),
        "properties": {
            "path": {
                "type": "string",
                "description": "Ruta relativa del archivo.",
            },
            "old_string": {
                "type": "string",
                "description": (
                    "Fragmento EXACTO a reemplazar, con "
                    "indentacion y saltos de linea tal cual "
                    "aparecen en el archivo."
                ),
            },
            "new_string": {
                "type": "string",
                "description": (
                    "Texto que sustituye a old_string. Puede ser "
                    "cadena vacia para borrar el fragmento."
                ),
            },
            "replace_all": {
                "type": "boolean",
                "description": (
                    "Si es true, reemplaza TODAS las "
                    "ocurrencias. Por defecto false (falla si "
                    "old_string no es unico)."
                ),
            },
        },
        "required": ["path", "old_string", "new_string"],
    },
    {
        "name": "insertar_en_archivo",
        "description": (
            "Inserta texto tras una linea concreta del archivo. "
            "No requiere old_string: util para anadir al final "
            "(insert_line=total) o tras una linea especifica. "
            "Usa leer_archivo(numbered=True) para saber que hay "
            "en cada linea."
        ),
        "properties": {
            "path": {
                "type": "string",
                "description": "Ruta relativa del archivo.",
            },
            "insert_line": {
                "type": "integer",
                "description": (
                    "Linea tras la que insertar (1-based). "
                    "0 = al inicio; N = tras la linea N; "
                    "total = al final del archivo."
                ),
            },
            "text": {
                "type": "string",
                "description": (
                    "Texto a insertar. Se anade un salto de "
                    "linea al final si falta."
                ),
            },
        },
        "required": ["path", "insert_line", "text"],
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

        # Normalizar aliases (line_start -> start_line, archivo ->
        # path...) antes de validar. Modelos pequeños inventan
        # nombres; si la intencion es correcta, no rechazamos.
        arguments = _normalise_args(arguments, spec)

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
            numbered = bool(arguments.get("numbered", False))
            return self.workspace.read_file(
                path,
                start_line=start if start is not None else None,
                end_line=end if end is not None else None,
                numbered=numbered,
            )
        if name == "crear_archivo":
            # content es obligatorio (el schema lo declara). Sin
            # este acceso directo, un modelo que omitia content
            # creaba archivos vacios silenciosamente. H7 del out(4).
            result = self.workspace.create_file(
                arguments["path"], arguments["content"]
            )
            return self._with_verification(result, arguments["path"])
        if name == "crear_carpeta":
            return self.workspace.create_folder(arguments["path"])
        if name == "escribir_archivo":
            result = self.workspace.write_file(
                arguments["path"], arguments["content"]
            )
            return self._with_verification(result, arguments["path"])
        if name == "editar_archivo":
            result = self.workspace.edit_file(
                arguments["path"],
                arguments["old_string"],
                arguments["new_string"],
                bool(arguments.get("replace_all", False)),
            )
            return self._with_verification(
                result, arguments["path"]
            )
        if name == "insertar_en_archivo":
            result = self.workspace.insert_in_file(
                arguments["path"],
                arguments["insert_line"],
                arguments["text"],
            )
            return self._with_verification(
                result, arguments["path"]
            )
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
        # Limitar el preview: el contenido completo puede ser de 1 MB
        # y no aporta al modelo. 500 chars bastan para detectar un
        # contenido claramente equivocado sin inflar el historial.
        preview = actual if len(actual) <= 500 else actual[:500] + "\n…(truncado)"
        return f"{result}\n\nContenido verificado en disco:\n---\n{preview}\n---"


# Aliases aceptados por cada argumento canonico. Solo se aplican
# si el canonico NO esta presente y si la tool declara ese
# argumento en su schema. Los modelos pequeños inventan nombres
# (line_start por start_line, archivo por path...) y esta capa
# evita rechazar llamadas que eran semanticamente correctas.
_ALIASES: dict[str, tuple[str, ...]] = {
    "path": (
        "archivo", "nombre", "file", "filename", "ruta",
        "folder", "dir", "carpeta",
        "file_path", "filePath", "filepath", "target",
        "target_file", "targetFile", "pathname",
    ),
    "content": (
        "contenido", "text", "body", "data",
        "code", "contents", "fileContent", "file_content",
    ),
    "old_string": (
        "old_str", "oldText", "old_text", "old_content",
        "oldContent", "original", "search", "find", "old",
        "search_string", "searchString", "target_text",
    ),
    "new_string": (
        "new_str", "newText", "new_text", "new_content",
        "newContent", "replacement", "replace", "new",
        "replacement_text", "replace_string", "replaceString",
    ),
    "start_line": (
        "line_start", "from_line", "desde_linea",
        "start", "startLine", "startline", "from",
    ),
    "end_line": (
        "line_end", "to_line", "hasta_linea",
        "end", "endLine", "endline", "to",
    ),
    "query": (
        "q", "search", "term", "regex", "pattern", "patron",
        "query_string", "queryString", "needle",
    ),
    "command": (
        "cmd", "comando", "shell", "script",
        "shellCommand", "shell_command", "terminalCommand",
        "terminal_command", "command_line", "commandLine",
    ),
    "ref": (
        "reference", "commit", "revision",
        "commit_hash", "commitHash", "refname",
    ),
    "limit": ("max", "n", "count", "max_count", "maxCount"),
    "insert_line": (
        "line", "line_number", "lineNumber", "after_line",
        "afterLine", "position", "line_no", "lineNo",
        "insert_at", "insertAt", "at_line", "atLine",
    ),
    "text": (
        "insert_text", "insertText", "body", "content",
        "contenido", "line_text", "lineText",
    ),
}


def _normalise_args(
    arguments: dict[str, Any], spec: dict[str, Any]
) -> dict[str, Any]:
    """Reemplaza aliases por su nombre canonico (si la tool lo usa).

    Reglas:
      - Solo se remapea si la tool declara el canonico en su schema.
      - Si el canonico ya viene en `arguments`, gana ese; el alias
        se descarta sin error (un modelo no deberia mandar ambos,
        pero no rompemos por eso).
      - Si el alias no esta en la tabla, se mantiene tal cual
        (y la validacion posterior lo rechaza si no esta en schema).
    """
    if not arguments:
        return arguments
    properties = spec.get("properties", {})
    if not properties:
        return arguments
    # Mapa alias -> canonico, solo para canonicos que esta tool usa.
    alias_to_canonical: dict[str, str] = {}
    for canonical, aliases in _ALIASES.items():
        if canonical not in properties:
            continue
        for a in aliases:
            alias_to_canonical[a] = canonical
    if not alias_to_canonical:
        return arguments

    out: dict[str, Any] = {}
    seen_canonical: set[str] = set()
    for k, v in arguments.items():
        canonical = alias_to_canonical.get(k)
        if canonical is None:
            out[k] = v
            if k in properties:
                seen_canonical.add(k)
            continue
        if canonical in seen_canonical:
            # Ya teniamos el canonico: descartar el alias.
            continue
        out[canonical] = v
        seen_canonical.add(canonical)
    return out


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
