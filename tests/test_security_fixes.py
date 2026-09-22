"""Tests de los arreglos de seguridad."""
from __future__ import annotations

import threading
from pathlib import Path

import pytest


# -- #1 git show flag injection ---------------------------------------------

def test_git_show_rejects_flag_injection(tmp_path):
    from plugins.git import GitClient, GitError

    subprocess_check = pytest.importorskip("shutil").which("git")
    if not subprocess_check:
        pytest.skip("git no instalado")

    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T", "commit",
         "--allow-empty", "-q", "-m", "init"],
        cwd=tmp_path, check=True,
    )

    client = GitClient(tmp_path)
    with pytest.raises(GitError):
        client.show("--output=/tmp/evil")
    with pytest.raises(GitError):
        client.show("--exec=rm -rf /")
    with pytest.raises(GitError):
        client.show("-p")
    with pytest.raises(GitError):
        client.show("HEAD; rm -rf /")


def test_git_show_accepts_valid_refs(tmp_path):
    from plugins.git import GitClient

    if not pytest.importorskip("shutil").which("git"):
        pytest.skip("git no instalado")

    import subprocess
    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T", "commit",
         "--allow-empty", "-q", "-m", "init"],
        cwd=tmp_path, check=True,
    )

    client = GitClient(tmp_path)
    # Refs válidos: no lanzan.
    assert "init" in client.show("HEAD")
    assert "init" in client.show("HEAD~0")


# -- #2 search cancel_event -------------------------------------------------

def test_search_cancel_event_stops_early(tmp_path):
    from plugins.search import SearchClient

    for i in range(100):
        (tmp_path / f"f{i:03d}.txt").write_text("coincide\n" * 10, encoding="utf-8")

    cancel = threading.Event()
    cancel.set()  # cancelado desde el principio

    result = SearchClient(tmp_path).search(
        "coincide", cancel_event=cancel
    )
    assert "cancelada" in result


def test_search_without_cancel_event_works(tmp_path):
    from plugins.search import SearchClient

    (tmp_path / "a.txt").write_text("hola\n", encoding="utf-8")
    result = SearchClient(tmp_path).search("hola")
    assert "a.txt" in result


def test_search_skips_very_long_lines(tmp_path):
    """Una línea gigante no debe bloquear la búsqueda."""
    from plugins.search import SearchClient

    # Línea de 1 MB (menos que _MAX_FILE_BYTES) que podría ser patológica.
    huge_line = "a" * 500_000 + "b"
    (tmp_path / "huge.txt").write_text(huge_line + "\n", encoding="utf-8")
    (tmp_path / "small.txt").write_text("findme\n", encoding="utf-8")

    result = SearchClient(tmp_path).search("findme")
    assert "small.txt" in result


# -- #3 shell defense in depth ----------------------------------------------

def test_shell_rejects_call_without_allow_destructive(tmp_path):
    from plugins.shell import ShellProvider
    from core.workspace import Workspace

    provider = ShellProvider(Workspace(tmp_path))
    result = provider.call("ejecutar_comando", {"command": "echo hola"})
    assert "confirmación explícita" in result


def test_shell_executes_with_allow_destructive(tmp_path):
    from plugins.shell import ShellProvider
    from core.workspace import Workspace

    provider = ShellProvider(Workspace(tmp_path))
    result = provider.call(
        "ejecutar_comando",
        {"command": "echo hola"},
        allow_destructive=True,
    )
    assert "hola" in result


# -- #4 dialogs escape (no UI test; comprobación del import) ----------------

def test_dialogs_import_html():
    import ui.views.dialogs as dialogs_module
    assert hasattr(dialogs_module, "html")


# -- #5 MCP unregister -------------------------------------------------------

def test_mcp_deactivate_makes_rules_inert(tmp_path):
    """Al desactivar un MCP, sus reglas dejan de estar en el gate.

    Antes se llamaba a ToolIntentGate.unregister_rules(), que borraba
    del registro global. Eso rompía otras instancias de MCPToolBridge
    con las que compartía nombres. Ahora las reglas simplemente
    desaparecen del gate porque el tool ya no está en definitions().
    """
    from core.intent import ToolIntentGate
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    from plugins.mcp import MCPToolBridge

    class FakeClient:
        def list_tools(self):
            return [{
                "name": "buscar",
                "description": "",
                "inputSchema": {"type": "object", "properties": {}},
            }]
        @staticmethod
        def to_ollama_tools(tools):
            return [{"type": "function", "function": {"name": t["name"]}}
                    for t in tools]
        def call_tool(self, *a, **k):
            return "ok"

    bridge = MCPToolBridge(ToolRegistry(Workspace(tmp_path)))
    bridge.activate("demo", FakeClient())

    # Con el servidor activo, la regla está presente en el gate.
    rules_before = bridge.intent_rules()
    assert "mcp__demo__buscar" in rules_before

    bridge.deactivate("demo")

    # Tras desactivar, la regla ya no aparece en las reglas que el
    # bridge declara. El registro global puede conservar copias, pero
    # son inertes porque el tool ya no está en definitions().
    rules_after = bridge.intent_rules()
    assert "mcp__demo__buscar" not in rules_after
    names = {d["function"]["name"] for d in bridge.definitions()}
    assert "mcp__demo__buscar" not in names


# -- #6 config sin duplicado -------------------------------------------------

def test_config_current_agent_not_duplicated():
    import inspect
    from core import config as config_module

    source = inspect.getsource(config_module.AppConfig)
    count = source.count("current_agent")
    assert count == 1, f"current_agent aparece {count} veces en AppConfig"
