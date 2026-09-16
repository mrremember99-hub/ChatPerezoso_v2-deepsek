from __future__ import annotations

from typing import Any

from core.tools import ToolRegistry

from .client import MCPClient, MCPError


class MCPToolBridge:
    """Une las herramientas locales con las herramientas de varios servidores MCP.

    Cada servidor recibe un identificador elegido por el usuario y sus
    herramientas se exponen como ``mcp__{server_id}__{tool}``, evitando
    colisiones entre servidores.
    """

    def __init__(self, local_tools: ToolRegistry):
        self.local_tools = local_tools
        self._servers: dict[str, MCPClient] = {}
        self._mcp_names: dict[str, tuple[str, str]] = {}
        self._mcp_readonly: dict[str, bool] = {}
        self._definitions: list[dict[str, Any]] = local_tools.definitions()

    @property
    def enabled(self) -> bool:
        return bool(self._servers)

    @property
    def active_servers(self) -> tuple[str, ...]:
        return tuple(self._servers)

    def activate(
        self,
        server_id: str,
        client: MCPClient,
        tools: list[dict[str, Any]] | None = None,
    ) -> list[dict[str, Any]]:
        server_id = str(server_id).strip()
        if not server_id:
            raise ValueError("El identificador del servidor MCP no puede estar vacío.")
        if server_id in self._servers:
            raise ValueError(f"Ya existe un servidor MCP llamado «{server_id}».")

        tools = client.list_tools() if tools is None else tools
        converted = client.to_ollama_tools(tools)
        self._servers[server_id] = client

        raw_by_name: dict[str, dict[str, Any]] = {}
        for raw in tools:
            raw_name = str(raw.get("name", "")).strip()
            if raw_name:
                raw_by_name[raw_name] = raw

        for tool in converted:
            function = tool.get("function", {})
            name = str(function.get("name", "")).strip()
            if not name:
                continue
            exposed = f"mcp__{server_id}__{name}"
            function = dict(function)
            function["name"] = exposed
            item = dict(tool)
            item["function"] = function
            self._mcp_names[exposed] = (server_id, name)

            annotations = raw_by_name.get(name, {}).get("annotations") or {}
            if isinstance(annotations, dict):
                read_only = annotations.get("readOnlyHint")
                if read_only is None:
                    read_only = annotations.get("read_only_hint")
                destructive = annotations.get("destructiveHint")
                if destructive is None:
                    destructive = annotations.get("destructive_hint")
                if isinstance(read_only, bool):
                    self._mcp_readonly[exposed] = read_only and destructive is not True

            # Evita una definición duplicada si un servidor devuelve el mismo
            # nombre dos veces.
            existing = {item["function"]["name"] for item in self._definitions}
            if exposed not in existing:
                self._definitions.append(item)

        self._definitions = self._core_definitions() + self._mcp_definitions()
        return list(self._definitions)

    _CORE_REPLACEMENTS: dict[str, frozenset[str]] = {
        # server-filesystem expone estos nombres; cuando están activos, sus
        # equivalentes del núcleo dejan de ofrecerse al modelo para evitar que
        # tenga dos herramientas para la misma operación.
        #
        # Es especialmente importante en escritura: ``write_file`` y
        # ``crear_archivo`` describen operaciones parecidas y, si ambas se
        # ofrecen, el modelo puede elegir la local aunque el usuario haya
        # activado MCP.
        "listar_carpeta": frozenset({"list_directory"}),
        "leer_archivo": frozenset({"read_text_file", "read_file"}),
        "crear_archivo": frozenset({"write_file"}),
        "escribir_archivo": frozenset({"write_file"}),
        "crear_carpeta": frozenset({"create_directory"}),
    }

    def _active_mcp_original_names(self) -> set[str]:
        return {original_name for _, original_name in self._mcp_names.values()}

    def _core_definitions(self) -> list[dict[str, Any]]:
        active_names = self._active_mcp_original_names()
        hidden = {
            core_name
            for core_name, mcp_names in self._CORE_REPLACEMENTS.items()
            if active_names.intersection(mcp_names)
        }
        return [
            definition
            for definition in self.local_tools.definitions()
            if definition.get("function", {}).get("name") not in hidden
        ]

    def _mcp_definitions(self) -> list[dict[str, Any]]:
        definitions: list[dict[str, Any]] = []
        for definition in self._definitions:
            name = str(definition.get("function", {}).get("name", "")).strip()
            if name in self._mcp_names:
                definitions.append(definition)
        return definitions

    def deactivate(self, server_id: str | None = None) -> None:
        if server_id is None:
            clients = list(self._servers.values())
            self._servers.clear()
            self._mcp_names.clear()
            self._mcp_readonly.clear()
            for client in clients:
                close = getattr(client, "close", None)
                if close is not None:
                    close()
        else:
            server_id = str(server_id).strip()
            client = self._servers.pop(server_id, None)
            if client is not None:
                close = getattr(client, "close", None)
                if close is not None:
                    close()
            for exposed, route in list(self._mcp_names.items()):
                if route[0] == server_id:
                    self._mcp_names.pop(exposed, None)
                    self._mcp_readonly.pop(exposed, None)
        self._definitions = self._core_definitions() + self._mcp_definitions()

    def definitions(self) -> list[dict[str, Any]]:
        return list(self._definitions)

    @staticmethod
    def is_destructive(name: str) -> bool:
        """Clasificación heurística informativa; no decide la autorización."""
        lowered = name.lower()
        markers = (
            "delete", "remove", "write", "create", "update", "modify",
            "move", "rename", "execute", "exec", "run", "command", "shell",
            "borrar", "eliminar", "escribir", "crear", "actualizar", "mover", "renombrar",
            "ejecutar", "comando",
        )
        return any(marker in lowered for marker in markers)

    @staticmethod
    def requires_confirmation(name: str) -> bool:
        """MCP se considera no confiable por defecto: requiere confirmación."""
        return name.startswith("mcp__")

    def needs_confirmation(self, name: str) -> bool:
        hint = self._mcp_readonly.get(name)
        if hint is not None:
            return not hint
        if self.requires_confirmation(name):
            return True
        return self.local_tools.requires_confirmation(name)

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        allow_destructive: bool = False,
    ) -> str:
        route = self._mcp_names.get(name)
        if route is not None:
            server_id, original_name = route
            client = self._servers.get(server_id)
            if client is None:
                return f"ERROR MCP: servidor «{server_id}» no está activo."
            if self.needs_confirmation(name) and not allow_destructive:
                return "ERROR: herramienta MCP bloqueada: requiere confirmación explícita del usuario."
            try:
                return client.call_tool(original_name, arguments)
            except MCPError as exc:
                return f"ERROR MCP: {exc}"
        return self.local_tools.call(
            name, arguments, allow_destructive=allow_destructive
        )
