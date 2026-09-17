from __future__ import annotations

from core.intent import IntentRule, ToolIntentGate
from core.tools import ToolRegistry
from core.workspace import Workspace


def _gate(tmp_path) -> ToolIntentGate:
    registry = ToolRegistry(Workspace(tmp_path))
    rules = registry.intent_rules()
    ToolIntentGate.register_rules(rules)
    return ToolIntentGate(rules)


def test_tools_for_request_none_without_tools(tmp_path):
    gate = _gate(tmp_path)
    assert gate.tools_for_request(None, "lista la carpeta") is None
    assert gate.tools_for_request([], "lista la carpeta") is None


def test_tools_for_request_blocked_for_informative_question(tmp_path):
    gate = _gate(tmp_path)
    tools = [{"type": "function", "function": {"name": "crear_archivo"}}]
    assert gate.tools_for_request(tools, "¿qué es el diseño editorial?") is None


def test_tools_for_request_allowed_for_explicit_action(tmp_path):
    gate = _gate(tmp_path)
    tools = [{"type": "function", "function": {"name": "crear_archivo"}}]
    assert gate.tools_for_request(tools, "crea el archivo nota.txt") == tools


def test_tool_is_requested_requires_target_and_verb(tmp_path):
    gate = _gate(tmp_path)
    assert gate.tool_is_requested("borrar_archivo", "borra notas.txt")
    assert not gate.tool_is_requested("borrar_archivo", "explícame qué es borrar")


def test_tool_is_requested_mcp_needs_name_and_action_verb():
    gate = ToolIntentGate({
        "mcp__saludar": IntentRule(mcp_explicit_name_required=True),
    })
    assert gate.tool_is_requested("mcp__saludar", "usa mcp__saludar para saludar")
    assert not gate.tool_is_requested("mcp__saludar", "¿qué hace mcp__saludar?")


def test_read_is_allowed_as_prerequisite_for_edit(tmp_path):
    gate = _gate(tmp_path)
    assert gate.tool_is_requested("leer_archivo", "edita notas.txt y cambia el título")


def test_create_folder_request_does_not_authorize_create_file(tmp_path):
    gate = _gate(tmp_path)
    text = "crea una carpeta llamada fotos"
    assert gate.tool_is_requested("crear_carpeta", text)
    assert not gate.tool_is_requested("crear_archivo", text)


def test_create_file_request_does_not_authorize_create_folder(tmp_path):
    gate = _gate(tmp_path)
    text = "crea el archivo notas.txt"
    assert gate.tool_is_requested("crear_archivo", text)
    assert not gate.tool_is_requested("crear_carpeta", text)


def test_filesystem_mcp_read_alias_uses_core_workspace_intent(tmp_path):
    gate = _gate(tmp_path)
    core_rule = gate.rules["leer_archivo"]
    gate.rules["mcp__fs__read_file"] = core_rule
    assert gate.tool_is_requested("mcp__fs__read_file", "lee el archivo notas.txt")


def test_tool_is_requested_rejects_explicit_negation(tmp_path):
    gate = _gate(tmp_path)
    assert not gate.tool_is_requested("borrar_archivo", "no borres archivo.txt")
    assert not gate.tool_is_requested(
        "borrar_archivo", "no quiero que borres archivo.txt"
    )
    assert not gate.tool_is_requested("crear_archivo", "no crees archivo.txt")


def test_tool_name_mention_does_not_authorize_execution(tmp_path):
    gate = _gate(tmp_path)
    assert not gate.tool_is_requested(
        "borrar_archivo", "¿qué hace borrar_archivo con un archivo?"
    )
    assert not gate.tool_is_requested(
        "leer_archivo", "el texto menciona leer_archivo para explicar su funcionamiento"
    )


def test_mcp_tool_name_mention_does_not_expose_or_authorize_tool(tmp_path):
    gate = _gate(tmp_path)
    tools = [{"type": "function", "function": {"name": "mcp__fs__read_text_file"}}]
    assert gate.tools_for_request(tools, "¿qué hace mcp__fs__read_text_file?") is None
    gate.rules["mcp__fs__read_text_file"] = IntentRule(mcp_explicit_name_required=True)
    assert not gate.tool_is_requested(
        "mcp__fs__read_text_file", "¿qué hace mcp__fs__read_text_file?"
    )


def test_mcp_explicit_named_request_is_still_allowed(tmp_path):
    gate = _gate(tmp_path)
    tools = [{"type": "function", "function": {"name": "mcp__demo__saludar"}}]
    assert gate.tools_for_request(tools, "usa mcp__demo__saludar para saludar") == tools


def test_negation_is_scoped_to_the_proposed_tool(tmp_path):
    gate = _gate(tmp_path)
    text = "no borres viejo.txt, pero crea nuevo.txt"
    assert not gate.tool_is_requested("borrar_archivo", text)
    assert gate.tool_is_requested("crear_archivo", text)


def test_negated_named_mcp_tool_is_rejected():
    gate = ToolIntentGate({
        "mcp__demo__saludar": IntentRule(mcp_explicit_name_required=True),
    })
    assert not gate.tool_is_requested(
        "mcp__demo__saludar", "no uses mcp__demo__saludar"
    )


def test_unknown_tool_is_never_authorized(tmp_path):
    gate = _gate(tmp_path)
    assert not gate.tool_is_requested("inventada", "usa inventada")
