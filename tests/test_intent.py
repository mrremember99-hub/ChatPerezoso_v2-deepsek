from core.intent import ToolIntentGate


def test_tools_for_request_none_without_tools():
    assert ToolIntentGate.tools_for_request(None, "lista la carpeta") is None
    assert ToolIntentGate.tools_for_request([], "lista la carpeta") is None


def test_tools_for_request_blocked_for_informative_question():
    tools = [{"type": "function", "function": {"name": "crear_archivo"}}]
    assert ToolIntentGate.tools_for_request(tools, "¿qué es el diseño editorial?") is None


def test_tools_for_request_allowed_for_explicit_action():
    tools = [{"type": "function", "function": {"name": "crear_archivo"}}]
    assert ToolIntentGate.tools_for_request(tools, "crea el archivo nota.txt") == tools


def test_tool_is_requested_requires_target_and_verb():
    assert ToolIntentGate.tool_is_requested("borrar_archivo", "borra notas.txt")
    assert not ToolIntentGate.tool_is_requested("borrar_archivo", "explícame qué es borrar")


def test_tool_is_requested_mcp_needs_name_and_action_verb():
    assert ToolIntentGate.tool_is_requested("mcp__saludar", "usa mcp__saludar para saludar")
    assert not ToolIntentGate.tool_is_requested("mcp__saludar", "¿qué hace mcp__saludar?")


def test_read_is_allowed_as_prerequisite_for_edit():
    assert ToolIntentGate.tool_is_requested("leer_archivo", "edita notas.txt y cambia el título")


def test_create_folder_request_does_not_authorize_create_file():
    text = "crea una carpeta llamada fotos"
    assert ToolIntentGate.tool_is_requested("crear_carpeta", text)
    assert not ToolIntentGate.tool_is_requested("crear_archivo", text)


def test_create_file_request_does_not_authorize_create_folder():
    text = "crea el archivo notas.txt"
    assert ToolIntentGate.tool_is_requested("crear_archivo", text)
    assert not ToolIntentGate.tool_is_requested("crear_carpeta", text)


def test_filesystem_mcp_read_alias_uses_core_workspace_intent():
    assert ToolIntentGate.tool_is_requested("mcp__fs__read_file", "lee el archivo notas.txt")


def test_filesystem_mcp_list_alias_uses_core_workspace_intent():
    assert ToolIntentGate.tool_is_requested("mcp__fs__list_directory", "lista la carpeta documentos")


def test_tool_is_requested_rejects_explicit_negation():
    assert not ToolIntentGate.tool_is_requested("borrar_archivo", "no borres archivo.txt")
    assert not ToolIntentGate.tool_is_requested("borrar_archivo", "no quiero que borres archivo.txt")
    assert not ToolIntentGate.tool_is_requested("crear_archivo", "no crees archivo.txt")


def test_tool_name_mention_does_not_authorize_execution():
    assert not ToolIntentGate.tool_is_requested(
        "borrar_archivo", "¿qué hace borrar_archivo con un archivo?"
    )
    assert not ToolIntentGate.tool_is_requested(
        "leer_archivo", "el texto menciona leer_archivo para explicar su funcionamiento"
    )


def test_mcp_tool_name_mention_does_not_expose_or_authorize_tool():
    tools = [{"type": "function", "function": {"name": "mcp__fs__read_text_file"}}]
    assert ToolIntentGate.tools_for_request(
        tools, "¿qué hace mcp__fs__read_text_file?"
    ) is None
    assert not ToolIntentGate.tool_is_requested(
        "mcp__fs__read_text_file", "¿qué hace mcp__fs__read_text_file?"
    )


def test_mcp_explicit_named_request_is_still_allowed():
    tools = [{"type": "function", "function": {"name": "mcp__demo__saludar"}}]
    assert ToolIntentGate.tools_for_request(
        tools, "usa mcp__demo__saludar para saludar"
    ) == tools


def test_negation_is_scoped_to_the_proposed_tool():
    text = "no borres viejo.txt, pero crea nuevo.txt"
    assert not ToolIntentGate.tool_is_requested("borrar_archivo", text)
    assert ToolIntentGate.tool_is_requested("crear_archivo", text)


def test_negated_named_mcp_tool_is_rejected():
    assert not ToolIntentGate.tool_is_requested(
        "mcp__demo__saludar", "no uses mcp__demo__saludar"
    )
