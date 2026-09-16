from __future__ import annotations

import re
import unicodedata
from typing import Any


class ToolIntentGate:
    """Heurística de intención para el uso de herramientas.

    Decide, a partir del último mensaje del usuario, si procede ofrecer
    herramientas al modelo y si una llamada de herramienta concreta se
    corresponde con lo que el usuario pidió.

    Importante: esto NO es un mecanismo de seguridad. Es un filtro
    anti-alucinación para reducir llamadas de herramientas no solicitadas.
    El control de seguridad real frente a operaciones destructivas es la
    confirmación explícita en la interfaz (ver ``core.tools`` y
    ``plugins.mcp.bridge``), que se aplica siempre, pase o no esta barrera.

    Vive fuera de ``OllamaClient`` a propósito: un cliente HTTP no debería
    contener reglas de idioma natural en español. Aislarlas aquí permite
    ajustarlas, sustituirlas o desactivarlas sin tocar el transporte.
    """

    # Única fuente de verdad para los verbos que activan cada herramienta.
    _ACTION_VERBS: dict[str, tuple[str, ...]] = {
        "listar_carpeta": ("lista", "listar", "muestra", "mostrar", "contenido"),
        "leer_archivo": ("lee", "leer", "abre", "abrir"),
        "crear_archivo": ("crea", "crear", "cree", "generar", "genera"),
        "crear_carpeta": ("crea", "crear", "cree", "generar", "genera"),
        "escribir_archivo": (
            "escribe", "escribir", "edita", "editar", "actualiza", "actualizar",
            "reemplaza", "reemplazar", "cambia", "cambiar", "modifica", "modificar",
        ),
        "borrar_archivo": ("borra", "borrar", "elimina", "eliminar"),
    }

    # Detecta un nombre de archivo con extensión (p.ej. "meses_año.txt") como
    # mención equivalente a la palabra "archivo", que el usuario a menudo omite
    # cuando ya está nombrando el fichero explícitamente.
    _FILENAME_PATTERN = re.compile(
        r"\b[\w\-]+\.(?:txt|md|markdown|csv|tsv|json|py|js|ts|jsx|tsx|html|htm|css"
        r"|xml|yaml|yml|log|pdf|docx?|xlsx?|ini|cfg|conf|sh|png|jpe?g|gif|svg|webp|toml)\b",
        re.IGNORECASE,
    )

    # Para MCP no podemos inferir la intención a partir de la descripción de
    # la herramienta. Exigimos que el usuario indique una acción explícita
    # junto con el nombre de la herramienta.
    _MCP_ACTION_VERBS: tuple[str, ...] = (
        "usa", "usar", "utiliza", "utilizar", "llama", "llamar",
        "invoca", "invocar", "ejecuta", "ejecutar", "realiza", "realizar",
    )

    # Alias semánticos para las herramientas del servidor-filesystem. El bridge
    # oculta las herramientas locales equivalentes cuando están activas, así que
    # la barrera de intención debe reconocerlas como operaciones de workspace.
    _MCP_WORKSPACE_ALIASES: dict[str, tuple[str, ...]] = {
        # server-filesystem: read_file es legacy; read_text_file es el nombre
        # recomendado actualmente. Ambos representan la lectura de un archivo.
        "read_file": ("leer_archivo",),
        "read_text_file": ("leer_archivo",),
        "list_directory": ("listar_carpeta",),
        # write_file cubre tanto crear un archivo nuevo como sobrescribirlo.
        "write_file": ("crear_archivo", "escribir_archivo"),
    }

    # Leer es un paso previo permitido cuando el usuario ha pedido editar,
    # modificar o reemplazar un archivo existente. No convierte leer_archivo
    # en una autorización genérica para otras herramientas.
    _READ_PREREQUISITE_VERBS: tuple[str, ...] = (
        "escribe", "escribir", "edita", "editar", "actualiza", "actualizar",
        "reemplaza", "reemplazar", "cambia", "cambiar", "modifica", "modificar",
    )

    _TARGET_WORDS: tuple[str, ...] = ("archivo", "fichero", "carpeta", "directorio", "workspace")

    # Patrones conservadores de negación. La negación se evalúa para la
    # herramienta concreta que el modelo propone; así "no borres viejo.txt,
    # pero crea nuevo.txt" puede autorizar la creación sin autorizar el borrado.
    _NEGATION_WORD = re.compile(r"\bno\b", re.IGNORECASE)

    # "crea una carpeta" y "crea un archivo" comparten verbo ("crea"): sin
    # distinguir el tipo de objetivo por herramienta, cualquiera de las dos
    # palabras autorizaría cualquiera de las dos herramientas y el modelo
    # podría acabar creando un archivo cuando se le pidió una carpeta (o al
    # revés). Cada herramienta solo acepta las palabras de su propio dominio.
    _FILE_TARGET_WORDS: tuple[str, ...] = ("archivo", "fichero")
    _FOLDER_TARGET_WORDS: tuple[str, ...] = ("carpeta", "directorio")
    _GENERIC_TARGET_WORDS: tuple[str, ...] = ("workspace",)
    _TARGET_WORDS_BY_TOOL: dict[str, tuple[str, ...]] = {
        "listar_carpeta": _FOLDER_TARGET_WORDS,
        "leer_archivo": _FILE_TARGET_WORDS,
        "crear_archivo": _FILE_TARGET_WORDS,
        "crear_carpeta": _FOLDER_TARGET_WORDS,
        "escribir_archivo": _FILE_TARGET_WORDS,
        "borrar_archivo": _FILE_TARGET_WORDS,
    }
    # Herramientas cuyo objetivo puede nombrarse con un nombre de archivo con
    # extensión (p.ej. "notas.txt") sin decir la palabra "archivo".
    _FILE_TOOLS: frozenset[str] = frozenset(
        {"leer_archivo", "crear_archivo", "escribir_archivo", "borrar_archivo"}
    )

    @classmethod
    def tools_for_request(
        cls, tools: list[dict[str, Any]] | None, last_user_text: str
    ) -> list[dict[str, Any]] | None:
        """Expone herramientas solo ante una petición explícita sobre el workspace."""
        if not tools:
            return None
        if not (
            cls._mentions_workspace_operation(last_user_text)
            or cls._mentions_mcp_tool(last_user_text, tools)
        ):
            return None
        return tools

    @classmethod
    def tool_is_requested(cls, name: str, text: str) -> bool:
        """Segunda barrera frente a tool calls nativos ajenos a la petición externa."""
        normalised = cls._normalise(text)
        tokens = set(normalised.split())
        if name.startswith("mcp__"):
            if cls._is_negated_mcp_request(name, text):
                return False
            direct_name = cls._normalise(name)
            if direct_name in tokens and bool(tokens.intersection(cls._MCP_ACTION_VERBS)):
                return True
            original_name = name.rsplit("__", 1)[-1]
            aliases = cls._MCP_WORKSPACE_ALIASES.get(original_name)
            if aliases is not None:
                return any(cls.tool_is_requested(alias, text) for alias in aliases)
            return False

        verbs = cls._ACTION_VERBS.get(name)
        if verbs is None:
            return False
        if cls._is_negated_tool_request(name, text):
            return False
        if not cls._mentions_target_for(name, text, normalised):
            return False
        if tokens.intersection(verbs):
            return True
        return name == "leer_archivo" and bool(tokens.intersection(cls._READ_PREREQUISITE_VERBS))

    @classmethod
    def _is_negated_tool_request(cls, name: str, text: str) -> bool:
        """Devuelve True si la petición niega la acción de ``name``."""
        verbs = cls._ACTION_VERBS.get(name, ())
        if not verbs:
            return False
        normalised = cls._normalise(text)
        for verb in verbs:
            pattern = rf"\bno\b(?:\s+\w+){{0,3}}\s+{re.escape(verb)}\b"
            if re.search(pattern, normalised, re.IGNORECASE):
                return True
        return False

    @classmethod
    def _is_negated_mcp_request(cls, name: str, text: str) -> bool:
        """Detecta una negación explícita de una herramienta MCP nombrada."""
        normalised = cls._normalise(text)
        direct_name = cls._normalise(name)
        if not direct_name:
            return False
        # "no uses mcp__demo__saludar" / "no quiero que uses ..."
        for verb in cls._MCP_ACTION_VERBS:
            pattern = rf"\bno\b(?:\s+\w+){{0,3}}\s+{re.escape(verb)}\s+{re.escape(direct_name)}\b"
            if re.search(pattern, normalised, re.IGNORECASE):
                return True
        original_name = name.rsplit("__", 1)[-1]
        aliases = cls._MCP_WORKSPACE_ALIASES.get(original_name)
        return bool(aliases) and any(cls._is_negated_tool_request(alias, text) for alias in aliases)

    @classmethod
    def _mentions_target(cls, text: str, normalised: str) -> bool:
        """Cierto si el mensaje nombra un archivo/carpeta en general, por
        palabra o por nombre con extensión. Filtro grueso: no distingue
        archivo de carpeta (para eso está ``_mentions_target_for``)."""
        return any(word in normalised for word in cls._TARGET_WORDS) or bool(
            cls._FILENAME_PATTERN.search(text)
        )

    @classmethod
    def _mentions_target_for(cls, name: str, text: str, normalised: str) -> bool:
        """Cierto si el mensaje nombra el tipo de objetivo que espera esta
        herramienta en concreto (archivo vs. carpeta), no solo un objetivo
        genérico del workspace."""
        words = cls._TARGET_WORDS_BY_TOOL.get(name, cls._TARGET_WORDS) + cls._GENERIC_TARGET_WORDS
        if any(word in normalised for word in words):
            return True
        return name in cls._FILE_TOOLS and bool(cls._FILENAME_PATTERN.search(text))

    @classmethod
    def _mentions_workspace_operation(cls, text: str) -> bool:
        normalised = cls._normalise(text)
        actions = {verb for verbs in cls._ACTION_VERBS.values() for verb in verbs}
        return cls._mentions_target(text, normalised) and any(action in normalised for action in actions)

    @classmethod
    def _mentions_mcp_tool(cls, text: str, tools: list[dict[str, Any]]) -> bool:
        """Activa MCP solo ante una solicitud explícita de uso de la herramienta."""
        normalised = cls._normalise(text)
        tokens = set(normalised.split())
        if not tokens.intersection(cls._MCP_ACTION_VERBS):
            return False
        return any(
            (name := str(item.get("function", {}).get("name", ""))).startswith("mcp__")
            and cls._normalise(name) in tokens
            for item in tools
        )

    @staticmethod
    def _normalise(text: str) -> str:
        return "".join(
            char for char in unicodedata.normalize("NFD", text.lower())
            if unicodedata.category(char) != "Mn"
        )
