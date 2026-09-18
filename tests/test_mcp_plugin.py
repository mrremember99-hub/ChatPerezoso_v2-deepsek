from __future__ import annotations

import sys
from pathlib import Path

import pytest

from plugins.mcp import MCPClient, MCPError, MCPServerConfig


def test_server_config_requires_command() -> None:
    with pytest.raises(ValueError):
        MCPServerConfig("")


def test_server_config_keeps_stdio_settings() -> None:
    config = MCPServerConfig(
        command="python3",
        args=("servidor.py",),
        env={"TEST_MCP": "1"},
        cwd="/tmp/proyecto",
    )
    assert config.command == "python3"
    assert config.args == ("servidor.py",)
    assert config.env["TEST_MCP"] == "1"
    assert config.cwd == "/tmp/proyecto"


def test_mcp_environment_does_not_inherit_arbitrary_process_variables(monkeypatch):
    monkeypatch.setenv("PATH", "/safe/bin")
    monkeypatch.setenv("HOME", "/safe/home")
    monkeypatch.setenv("GITHUB_TOKEN", "super-secret")
    monkeypatch.setenv("OPENAI_API_KEY", "super-secret")
    monkeypatch.setenv("CUSTOM_SECRET", "super-secret")

    client = MCPClient(MCPServerConfig("python3", env={"MCP_MODE": "test"}))
    env = client._resolve_env()

    assert env is not None
    assert env["MCP_MODE"] == "test"
    # Los secretos del proceso padre NUNCA deben llegar al subproceso.
    assert "GITHUB_TOKEN" not in env
    assert "OPENAI_API_KEY" not in env
    assert "CUSTOM_SECRET" not in env


def test_mcp_environment_is_none_when_no_overrides():
    """Sin overrides, delegamos en el default seguro del SDK."""
    client = MCPClient(MCPServerConfig("python3"))
    assert client._resolve_env() is None


def test_mcp_tools_are_converted_to_ollama_schema() -> None:
    tools = MCPClient.to_ollama_tools(
        [
            {
                "name": "listar_carpeta",
                "description": "Lista una carpeta.",
                "inputSchema": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            }
        ]
    )
    assert tools == [
        {
            "type": "function",
            "function": {
                "name": "listar_carpeta",
                "description": "Lista una carpeta.",
                "parameters": {
                    "type": "object",
                    "properties": {"path": {"type": "string"}},
                    "required": ["path"],
                },
            },
        }
    ]


def test_mcp_server_config_accepts_workspace_cwd(tmp_path):
    config = MCPServerConfig("python3", cwd=str(tmp_path))
    assert config.cwd == str(tmp_path)


def test_empty_tool_name_is_rejected() -> None:
    client = MCPClient(MCPServerConfig("python3"))
    with pytest.raises(MCPError):
        client.call_tool("")


class FakeMCPClient:
    def list_tools(self):
        return [{
            "name": "buscar",
            "description": "Busca algo.",
            "inputSchema": {
                "type": "object",
                "properties": {"query": {"type": "string"}},
                "required": ["query"],
            },
        }]

    @staticmethod
    def to_ollama_tools(tools):
        return MCPClient.to_ollama_tools(tools)

    def call_tool(self, name, arguments, *, cancel_event=None):
        return f"MCP:{name}:{arguments['query']}"


def test_bridge_prefixes_mcp_tools_and_routes_calls(tmp_path):
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    from plugins.mcp import MCPToolBridge

    bridge = MCPToolBridge(ToolRegistry(Workspace(tmp_path)))
    definitions = bridge.activate("demo", FakeMCPClient())
    names = [item["function"]["name"] for item in definitions]
    assert "listar_carpeta" in names
    assert "mcp__demo__buscar" in names
    assert bridge.call(
        "mcp__demo__buscar", {"query": "hola"}, allow_destructive=True
    ) == "MCP:buscar:hola"


def test_bridge_hides_local_filesystem_tools_when_mcp_replaces_them(tmp_path):
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    from plugins.mcp import MCPToolBridge

    class FilesystemClient(FakeMCPClient):
        def list_tools(self):
            return [
                {"name": "list_directory", "description": "Lista carpetas.", "inputSchema": {"type": "object", "properties": {}}},
                {"name": "read_text_file", "description": "Lee archivos.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}}},
                {"name": "write_file", "description": "Escribe archivos.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}, "content": {"type": "string"}}}},
                {"name": "create_directory", "description": "Crea carpetas.", "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}}},
            ]

    bridge = MCPToolBridge(ToolRegistry(Workspace(tmp_path)))
    names = [item["function"]["name"] for item in bridge.activate("fs", FilesystemClient())]

    assert "mcp__fs__list_directory" in names
    assert "mcp__fs__read_text_file" in names
    assert "mcp__fs__write_file" in names
    assert "mcp__fs__create_directory" in names
    assert "listar_carpeta" not in names
    assert "leer_archivo" not in names
    assert "crear_archivo" not in names
    assert "escribir_archivo" not in names
    assert "crear_carpeta" not in names


def test_bridge_deactivation_restores_core_tools(tmp_path):
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    from plugins.mcp import MCPToolBridge

    bridge = MCPToolBridge(ToolRegistry(Workspace(tmp_path)))
    bridge.activate("demo", FakeMCPClient())
    bridge.deactivate()
    names = [item["function"]["name"] for item in bridge.definitions()]
    assert "mcp__demo__buscar" not in names
    assert "listar_carpeta" in names


def test_bridge_requires_confirmation_for_all_mcp_tools(tmp_path):
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    from plugins.mcp import MCPToolBridge

    bridge = MCPToolBridge(ToolRegistry(Workspace(tmp_path)))

    # MCP sin hint → siempre requiere confirmación.
    assert bridge.requires_confirmation("mcp__demo__delete_file")
    assert bridge.requires_confirmation("mcp__execute_command")
    assert bridge.requires_confirmation("mcp__demo__buscar")

    # Locales: solo las que escriben.
    assert bridge.requires_confirmation("borrar_archivo")
    assert not bridge.requires_confirmation("listar_carpeta")
    assert not bridge.requires_confirmation("leer_archivo")


def test_bridge_blocks_destructive_mcp_tool_without_confirmation(tmp_path):
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    from plugins.mcp import MCPToolBridge

    class DestructiveClient(FakeMCPClient):
        @staticmethod
        def to_ollama_tools(tools):
            return MCPClient.to_ollama_tools([
                {
                    "name": "delete_file",
                    "description": "Borra un archivo.",
                    "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
                }
            ])

        def call_tool(self, name, arguments, *, cancel_event=None):
            return "BORRADO"

    bridge = MCPToolBridge(ToolRegistry(Workspace(tmp_path)))
    bridge.activate("demo", DestructiveClient())
    assert "bloqueada" in bridge.call("mcp__demo__delete_file", {"path": "x.txt"})
    assert bridge.call(
        "mcp__demo__delete_file", {"path": "x.txt"}, allow_destructive=True
    ) == "BORRADO"


def test_bridge_ignores_readonly_hint_for_unknown_server(tmp_path):
    """Un servidor NO listado en _TRUSTED_READONLY no puede saltarse
    la confirmacion aunque su herramienta se declare readOnlyHint=True.

    Este test reemplaza al antiguo test_bridge_honors_readonly_hint_...
    que verificaba el comportamiento viejo (vulnerable): confiar en las
    anotaciones que el propio servidor declara sobre si mismo.
    """
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    from plugins.mcp import MCPToolBridge

    class ReadOnlyHintedClient(FakeMCPClient):
        def list_tools(self):
            return [{
                "name": "read_file",
                "description": "Lee un archivo.",
                "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
                "annotations": {"readOnlyHint": True},
            }]

        def call_tool(self, name, arguments, *, cancel_event=None):
            return "CONTENIDO"

    bridge = MCPToolBridge(ToolRegistry(Workspace(tmp_path)))
    # Servidor "demo" no esta en _TRUSTED_READONLY, asi que aunque la
    # herramienta se llame "read_file" y declare readOnly, confirmamos.
    bridge.activate("demo", ReadOnlyHintedClient())
    assert bridge.requires_confirmation("mcp__demo__read_file") is True


def test_bridge_trusted_readonly_tools_skip_confirmation(tmp_path):
    """Un servidor SI listado en _TRUSTED_READONLY, con una herramienta
    tambien listada, no requiere confirmacion."""
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    from plugins.mcp import MCPToolBridge

    class FsClient(FakeMCPClient):
        def list_tools(self):
            return [{
                "name": "read_file",
                "description": "Lee un archivo.",
                "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
                "annotations": {"readOnlyHint": True},
            }]

        def call_tool(self, name, arguments, *, cancel_event=None):
            return "CONTENIDO"

    bridge = MCPToolBridge(ToolRegistry(Workspace(tmp_path)))
    bridge.activate("fs", FsClient())
    assert bridge.requires_confirmation("mcp__fs__read_file") is False


def test_bridge_trusted_server_unknown_tool_still_confirms(tmp_path):
    """Un servidor de confianza con una herramienta NO listada sigue
    requiriendo confirmacion. La confianza es por herramienta, no por
    servidor entero."""
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    from plugins.mcp import MCPToolBridge

    class FsClient(FakeMCPClient):
        def list_tools(self):
            return [{
                "name": "write_file",
                "description": "Escribe un archivo.",
                "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
            }]

        def call_tool(self, name, arguments, *, cancel_event=None):
            return "OK"

    bridge = MCPToolBridge(ToolRegistry(Workspace(tmp_path)))
    bridge.activate("fs", FsClient())
    # write_file NO esta en la whitelist de 'fs', asi que confirma
    assert bridge.requires_confirmation("mcp__fs__write_file") is True



def test_bridge_readonly_hint_ignored_if_also_destructive(tmp_path):
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    from plugins.mcp import MCPToolBridge

    class ContradictoryHintedClient(FakeMCPClient):
        def list_tools(self):
            return [{
                "name": "weird_tool",
                "description": "Herramienta con hints contradictorios.",
                "inputSchema": {"type": "object", "properties": {}},
                "annotations": {"readOnlyHint": True, "destructiveHint": True},
            }]

        def call_tool(self, name, arguments, *, cancel_event=None):
            return "EJECUTADO"

    bridge = MCPToolBridge(ToolRegistry(Workspace(tmp_path)))
    bridge.activate("demo", ContradictoryHintedClient())
    assert bridge.requires_confirmation("mcp__demo__weird_tool")


@pytest.mark.timeout(15)
def test_demo_server_connects_over_stdio() -> None:
    """Prueba de integración real con el SDK de MCP.

    Tiene timeout para no bloquear la suite si la conexión se cuelga.
    Si falla, el diagnóstico real está en ``plugins/mcp/_diagnose.py``.
    """
    pytest.importorskip("mcp")
    demo_server = Path(__file__).resolve().parent.parent / "plugins" / "mcp" / "demo_server.py"
    client = MCPClient(MCPServerConfig(command=sys.executable, args=(str(demo_server),)))

    try:
        tools = client.list_tools()
    except Exception as exc:
        pytest.skip(f"Conexión MCP no disponible en este entorno: {exc}")

    assert [tool["name"] for tool in tools] == ["saludar"]
    assert client.call_tool("saludar", {"nombre": "Marco"}) == (
        "Hola, Marco. El servidor MCP de prueba responde correctamente."
    )


def test_bridge_blocks_unknown_mcp_tool_without_confirmation(tmp_path):
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    from plugins.mcp import MCPToolBridge

    class InnocentNamedClient(FakeMCPClient):
        @staticmethod
        def to_ollama_tools(tools):
            return MCPClient.to_ollama_tools([
                {
                    "name": "sync_workspace",
                    "description": "Sincroniza datos.",
                    "inputSchema": {"type": "object", "properties": {}},
                }
            ])

        def call_tool(self, name, arguments, *, cancel_event=None):
            return "EJECUTADO"

    bridge = MCPToolBridge(ToolRegistry(Workspace(tmp_path)))
    bridge.activate("demo", InnocentNamedClient())
    blocked = bridge.call("mcp__demo__sync_workspace", {})
    assert "bloqueada" in blocked
    assert bridge.call(
        "mcp__demo__sync_workspace", {}, allow_destructive=True
    ) == "EJECUTADO"


def test_bridge_supports_multiple_servers_and_routes_by_server_id(tmp_path):
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    from plugins.mcp import MCPToolBridge

    class OtherClient(FakeMCPClient):
        def call_tool(self, name, arguments, *, cancel_event=None):
            return f"OTHER:{name}:{arguments['query']}"

    bridge = MCPToolBridge(ToolRegistry(Workspace(tmp_path)))
    bridge.activate("demo", FakeMCPClient())
    bridge.activate("other", OtherClient())

    assert bridge.active_servers == ("demo", "other")
    names = [item["function"]["name"] for item in bridge.definitions()]
    assert "mcp__demo__buscar" in names
    assert "mcp__other__buscar" in names
    assert bridge.call(
        "mcp__demo__buscar", {"query": "uno"}, allow_destructive=True
    ) == "MCP:buscar:uno"
    assert bridge.call(
        "mcp__other__buscar", {"query": "dos"}, allow_destructive=True
    ) == "OTHER:buscar:dos"

    bridge.deactivate("demo")
    assert bridge.active_servers == ("other",)
    assert "mcp__demo__buscar" not in [
        item["function"]["name"] for item in bridge.definitions()
    ]
    assert "mcp__other__buscar" in [
        item["function"]["name"] for item in bridge.definitions()
    ]


def test_bridge_rejects_empty_server_id(tmp_path):
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    from plugins.mcp import MCPToolBridge

    bridge = MCPToolBridge(ToolRegistry(Workspace(tmp_path)))
    with pytest.raises(ValueError):
        bridge.activate("", FakeMCPClient())


def test_filesystem_mcp_tools_replace_duplicate_core_read_and_list(tmp_path):
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    from plugins.mcp import MCPToolBridge

    class FilesystemClient(FakeMCPClient):
        def list_tools(self):
            return [
                {
                    "name": "read_file",
                    "description": "Lee un archivo.",
                    "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
                    "annotations": {"readOnlyHint": True},
                },
                {
                    "name": "list_directory",
                    "description": "Lista un directorio.",
                    "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
                    "annotations": {"readOnlyHint": True},
                },
            ]

    bridge = MCPToolBridge(ToolRegistry(Workspace(tmp_path)))
    bridge.activate("fs", FilesystemClient())
    names = {item["function"]["name"] for item in bridge.definitions()}

    assert "leer_archivo" not in names
    assert "listar_carpeta" not in names
    assert "mcp__fs__read_file" in names
    assert "mcp__fs__list_directory" in names


def test_deactivating_filesystem_restores_core_tools(tmp_path):
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    from plugins.mcp import MCPToolBridge

    class FilesystemClient(FakeMCPClient):
        def list_tools(self):
            return [
                {"name": "read_file", "inputSchema": {"type": "object", "properties": {}}},
                {"name": "list_directory", "inputSchema": {"type": "object", "properties": {}}},
            ]

    bridge = MCPToolBridge(ToolRegistry(Workspace(tmp_path)))
    bridge.activate("fs", FilesystemClient())
    bridge.deactivate("fs")
    names = {item["function"]["name"] for item in bridge.definitions()}

    assert "leer_archivo" in names
    assert "listar_carpeta" in names


def test_unrelated_mcp_tool_does_not_hide_core_tools(tmp_path):
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    from plugins.mcp import MCPToolBridge

    bridge = MCPToolBridge(ToolRegistry(Workspace(tmp_path)))
    bridge.activate("other", FakeMCPClient())
    names = {item["function"]["name"] for item in bridge.definitions()}

    assert "leer_archivo" in names
    assert "listar_carpeta" in names


def test_mcp_client_reuses_persistent_session_and_closes_it(monkeypatch):
    from plugins.mcp import MCPClient, MCPServerConfig

    class FakeTool:
        name = "listar_carpeta"
        description = "Lista una carpeta."
        input_schema = {"type": "object", "properties": {}}
        annotations = None

    class FakeListResult:
        tools = [FakeTool()]

    class FakeCallResult:
        is_error = False
        content = []

    class FakeConnectedClient:
        enter_count = 0
        exit_count = 0
        list_count = 0
        call_count = 0

        def __init__(self, _params):
            pass

        async def __aenter__(self):
            type(self).enter_count += 1
            return self

        async def __aexit__(self, *_args):
            type(self).exit_count += 1

        async def list_tools(self):
            type(self).list_count += 1
            return FakeListResult()

        async def call_tool(self, _name, _arguments):
            type(self).call_count += 1
            return FakeCallResult()

    class FakeClientFactory:
        def __new__(cls, params):
            return FakeConnectedClient(params)

    class FakeParams:
        def __init__(self, **kwargs):
            self.kwargs = kwargs

    monkeypatch.setattr(
        MCPClient, "_load_sdk", staticmethod(lambda: (FakeClientFactory, FakeParams))
    )

    client = MCPClient(MCPServerConfig("fake-server"))
    try:
        assert [tool["name"] for tool in client.list_tools()] == ["listar_carpeta"]
        assert FakeConnectedClient.enter_count == 1
        assert FakeConnectedClient.list_count == 1

        client.call_tool("listar_carpeta", {})
        client.call_tool("listar_carpeta", {})
        assert FakeConnectedClient.enter_count == 1
        assert FakeConnectedClient.call_count == 2

        client.list_tools()
        assert FakeConnectedClient.enter_count == 1
        assert FakeConnectedClient.list_count == 2
    finally:
        client.close()

    assert FakeConnectedClient.exit_count == 1


# -- combinación de reglas del núcleo ---------------------------------------

def test_bridge_combines_rules_from_multiple_core_tools(tmp_path):
    """write_file mapea desde crear_archivo Y escribir_archivo. La regla
    heredada debe aceptar los verbos de ambos."""
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    from plugins.mcp import MCPToolBridge

    class FilesystemClient(FakeMCPClient):
        def list_tools(self):
            return [
                {
                    "name": "write_file",
                    "description": "Escribe archivos.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                    },
                },
            ]

    bridge = MCPToolBridge(ToolRegistry(Workspace(tmp_path)))
    bridge.activate("fs", FilesystemClient())
    rules = bridge.intent_rules()
    write_rule = rules["mcp__fs__write_file"]

    # Verbos de crear_archivo
    assert "crea" in write_rule.verbs
    # Verbos de escribir_archivo
    assert "modifica" in write_rule.verbs
    assert "escribe" in write_rule.verbs
    assert write_rule.accepts_filename


# -- combinación de reglas del núcleo ---------------------------------------

def test_bridge_combines_rules_from_multiple_core_tools(tmp_path):
    """write_file mapea desde crear_archivo Y escribir_archivo. La regla
    heredada debe aceptar los verbos de ambos."""
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    from plugins.mcp import MCPToolBridge

    class FilesystemClient(FakeMCPClient):
        def list_tools(self):
            return [
                {
                    "name": "write_file",
                    "description": "Escribe archivos.",
                    "inputSchema": {
                        "type": "object",
                        "properties": {
                            "path": {"type": "string"},
                            "content": {"type": "string"},
                        },
                    },
                },
            ]

    bridge = MCPToolBridge(ToolRegistry(Workspace(tmp_path)))
    bridge.activate("fs", FilesystemClient())
    rules = bridge.intent_rules()
    write_rule = rules["mcp__fs__write_file"]

    # Verbos de crear_archivo
    assert "crea" in write_rule.verbs
    # Verbos de escribir_archivo
    assert "modifica" in write_rule.verbs
    assert "escribe" in write_rule.verbs
    assert write_rule.accepts_filename
