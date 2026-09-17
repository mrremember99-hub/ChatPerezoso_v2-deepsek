from __future__ import annotations

import threading
from typing import Any

from core.intent import IntentRule, ToolIntentGate
from core.tools import ToolRegistry

from ._base import MCPError
from .client import MCPClient


class MCPToolBridge:
    """Une las herramientas locales con las de varios servidores MCP.

    Cada servidor recibe un identificador y sus herramientas se exponen como
    ``mcp__{server_id}__{tool}`` para evitar colisiones.
    """

    # Cuando un MCP expone una de estas herramientas con su nombre original,
    # la equivalente del núcleo deja de ofrecerse al modelo.
    _CORE_REPLACEMENTS: dict[str, frozenset[str]] = {
        "listar_carpeta": frozenset({"list_directory"}),
        "leer_archivo": frozenset({"read_text_file", "read_file"}),
        "crear_archivo": frozenset({"write_file"}),
        "escribir_archivo": frozenset({"write_file"}),
        "crear_carpeta": frozenset({"create_directory"}),
    }

    def __init__(self, local_tools: ToolRegistry):
        self.local_tools = local_tools
        self._servers: dict[str, MCPClient] = {}
        self._mcp_names: dict[str, tuple[str, str]] = {}
        self._mcp_readonly: dict[str, bool] = {}
        self._mcp_defs_by_exposed: dict[str, dict[str, Any]] = {}
        self._definitions: list[dict[str, Any]] = list(local_tools.definitions())

    # -- consulta ------------------------------------------------------------

    @property
    def enabled(self) -> bool:
        return bool(self._servers)

    @property
    def active_servers(self) -> tuple[str, ...]:
        return tuple(self._servers)

    # -- activación / desactivación -----------------------------------------

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
            function_copy = dict(function)
            function_copy["name"] = exposed
            item = dict(tool)
            item["function"] = function_copy

            self._mcp_names[exposed] = (server_id, name)
            self._mcp_defs_by_exposed[exposed] = item

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

        self._rebuild_definitions()
        return list(self._definitions)

    def deactivate(self, server_id: str | None = None) -> None:
        removed_names: list[str] = []
        if server_id is None:
            removed_names = list(self._mcp_names.keys())
            clients = list(self._servers.values())
            self._servers.clear()
            self._mcp_names.clear()
            self._mcp_readonly.clear()
            self._mcp_defs_by_exposed.clear()
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
                    removed_names.append(exposed)
                    self._mcp_names.pop(exposed, None)
                    self._mcp_readonly.pop(exposed, None)
                    self._mcp_defs_by_exposed.pop(exposed, None)

        # Limpia las reglas huérfanas del registro global. Sin esto,
        # `tools_for_request` seguiría considerando activas herramientas
        # de un servidor ya desconectado.
        if removed_names:
            ToolIntentGate.unregister_rules(removed_names)
        self._rebuild_definitions()

    # -- definiciones --------------------------------------------------------

    def definitions(self) -> list[dict[str, Any]]:
        return list(self._definitions)

    def _rebuild_definitions(self) -> None:
        active_original_names = {
            original_name for _, original_name in self._mcp_names.values()
        }
        hidden = {
            core_name
            for core_name, mcp_names in self._CORE_REPLACEMENTS.items()
            if active_original_names.intersection(mcp_names)
        }
        core = [
            definition
            for definition in self.local_tools.definitions()
            if definition.get("function", {}).get("name") not in hidden
        ]
        mcp = [
            definition
            for exposed, definition in self._mcp_defs_by_exposed.items()
            if exposed in self._mcp_names
        ]
        self._definitions = core + mcp

    # -- reglas de intención -------------------------------------------------

    def intent_rules(self) -> dict[str, IntentRule]:
        core_rules = self.local_tools.intent_rules()
        # Empezamos con las reglas del núcleo. MCPToolBridge envuelve
        # ToolRegistry: si no incluimos sus reglas, el composite pierde
        # los verbos que activan listar_carpeta, crear_archivo, etc., y
        # el gate bloquea TODO (bug detectado en pruebas end-to-end).
        result: dict[str, IntentRule] = dict(core_rules)
        for exposed, (_, original_name) in self._mcp_names.items():
            inherited = self._inherit_core_rule(original_name, core_rules)
            if inherited is not None:
                result[exposed] = inherited
            else:
                result[exposed] = IntentRule(mcp_explicit_name_required=True)
        return result

    @classmethod
    def _inherit_core_rule(
        cls,
        original_name: str,
        core_rules: dict[str, IntentRule],
    ) -> IntentRule | None:
        matching: list[IntentRule] = []
        for core_name, aliases in cls._CORE_REPLACEMENTS.items():
            if original_name in aliases:
                rule = core_rules.get(core_name)
                if rule is not None:
                    matching.append(rule)
        if not matching:
            return None
        if len(matching) == 1:
            return matching[0]
        verbs = tuple(dict.fromkeys(v for r in matching for v in r.verbs))
        targets = tuple(dict.fromkeys(t for r in matching for t in r.target_words))
        return IntentRule(
            verbs=verbs,
            target_words=targets,
            accepts_filename=any(r.accepts_filename for r in matching),
            is_read_prerequisite=any(r.is_read_prerequisite for r in matching),
        )

    # -- confirmación --------------------------------------------------------

    def requires_confirmation(self, name: str) -> bool:
        if name.startswith("mcp__"):
            hint = self._mcp_readonly.get(name)
            if hint is not None:
                return not hint
            return True
        return self.local_tools.requires_confirmation(name)

    # -- ejecución -----------------------------------------------------------

    def call(
        self,
        name: str,
        arguments: dict[str, Any],
        *,
        allow_destructive: bool = False,
        cancel_event: threading.Event | None = None,
    ) -> str:
        route = self._mcp_names.get(name)
        if route is not None:
            server_id, original_name = route
            client = self._servers.get(server_id)
            if client is None:
                return f"ERROR MCP: servidor «{server_id}» no está activo."
            if self.requires_confirmation(name) and not allow_destructive:
                return (
                    "ERROR: herramienta MCP bloqueada: requiere confirmación "
                    "explícita del usuario."
                )
            try:
                return client.call_tool(
                    original_name, arguments, cancel_event=cancel_event
                )
            except MCPError as exc:
                return f"ERROR MCP: {exc}"
        return self.local_tools.call(
            name, arguments, allow_destructive=allow_destructive
        )
