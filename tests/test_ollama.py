import pytest

from core.intent import ToolIntentGate
from core.ollama import OllamaClient


@pytest.fixture(autouse=True)
def _register_core_rules(tmp_path):
    """Registra las reglas del núcleo para que los tests que pasan listas
    de definiciones (en vez de un ToolProvider) encuentren las reglas."""
    from core.tools import ToolRegistry
    from core.workspace import Workspace

    rules = ToolRegistry(Workspace(tmp_path)).intent_rules()
    ToolIntentGate.register_rules(rules)
    # Añade la regla MCP para los nombres usados en los tests.
    from core.intent import IntentRule
    ToolIntentGate.register_rules({
        "mcp__saludar": IntentRule(mcp_explicit_name_required=True),
        "mcp__fs__list_directory": ToolRegistry(Workspace(tmp_path)).intent_rules()["listar_carpeta"],
        "mcp__fs__read_text_file": ToolRegistry(Workspace(tmp_path)).intent_rules()["leer_archivo"],
        "mcp__fs__write_file": ToolRegistry(Workspace(tmp_path)).intent_rules()["crear_archivo"],
    })



def test_chat_without_tools(monkeypatch):
    client = OllamaClient()
    chunks = []

    def fake_stream(model, messages, tools, on_text, cancel_event=None, options=None):
        assert model == "test-model"
        assert tools is None
        on_text("Hola")
        return {"role": "assistant", "content": "Hola"}

    monkeypatch.setattr(client, "_stream", fake_stream)
    result = client.chat("test-model", [{"role": "user", "content": "Hola"}], None, chunks.append, lambda *_: "")

    assert result == "Hola"
    assert chunks == ["Hola"]


def test_chat_executes_tool_and_continues(monkeypatch):
    client = OllamaClient()
    calls = []
    responses = iter([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "listar_carpeta", "arguments": {"path": "."}}}],
        },
        {"role": "assistant", "content": "Hecho."},
    ])

    def fake_stream(model, messages, tools, on_text, cancel_event=None, options=None):
        calls.append((model, len(messages), tools is not None))
        return next(responses)

    monkeypatch.setattr(client, "_stream", fake_stream)
    tool_calls = []
    result = client.chat(
        "test-model",
        [{"role": "user", "content": "Lista la carpeta"}],
        [{"type": "function", "function": {"name": "listar_carpeta"}}],
        lambda text: None,
        lambda name, args: tool_calls.append((name, args)) or "[FILE] README.md",
    )

    assert result == "Hecho."
    assert tool_calls == [("listar_carpeta", {"path": "."})]
    assert calls == [("test-model", 2, True), ("test-model", 4, True)]


def test_chat_keeps_tools_on_ollama_tool_error(monkeypatch):
    client = OllamaClient()
    calls = []

    def fake_stream(model, messages, tools, on_text, cancel_event=None, options=None):
        calls.append((messages, tools))
        raise Exception("should not be reached")

    # The client must not silently disable tools after an API/tool error.
    monkeypatch.setattr(client, "_stream", fake_stream)
    try:
        client.chat(
            "test-model",
            [{"role": "user", "content": "Crea un archivo"}],
            [{"type": "function", "function": {"name": "crear_archivo"}}],
            lambda text: None,
            lambda name, args: "",
        )
    except Exception as exc:
        assert str(exc) == "should not be reached"
    assert calls
    assert calls[0][1] is not None
    assert calls[0][0][0]["role"] == "system"


def test_chat_does_not_execute_textual_tool_call(monkeypatch):
    client = OllamaClient()
    responses = iter([{"role": "assistant", "content": "Solicito la herramienta crear_archivo."}])
    calls = []

    def fake_stream(model, messages, tools, on_text, cancel_event=None, options=None):
        return next(responses)

    monkeypatch.setattr(client, "_stream", fake_stream)
    result = client.chat(
        "test-model",
        [{"role": "user", "content": "¿Qué es el diseño editorial?"}],
        [{"type": "function", "function": {"name": "crear_archivo"}}],
        lambda text: None,
        lambda name, args: calls.append((name, args)) or "[FILE] prueba.txt",
    )
    assert result == "Solicito la herramienta crear_archivo."
    assert calls == []


def test_chat_hides_tools_for_informative_request(monkeypatch):
    client = OllamaClient()
    stream_tools = []

    def fake_stream(model, messages, tools, on_text, cancel_event=None, options=None):
        stream_tools.append(tools)
        return {"role": "assistant", "content": "El diseño editorial organiza contenido."}

    monkeypatch.setattr(client, "_stream", fake_stream)
    result = client.chat(
        "test-model",
        [{"role": "user", "content": "¿Qué es el diseño editorial?"}],
        [{"type": "function", "function": {"name": "crear_archivo"}}],
        lambda _text: None,
        lambda *_: "",
    )

    assert result
    assert stream_tools == [None]


def test_chat_exposes_explicitly_named_mcp_tool(monkeypatch):
    client = OllamaClient()
    stream_tools = []
    mcp_tools = [{"type": "function", "function": {"name": "mcp__saludar"}}]

    def fake_stream(model, messages, tools, on_text, cancel_event=None, options=None):
        stream_tools.append(tools)
        return {"role": "assistant", "content": "Hola."}

    monkeypatch.setattr(client, "_stream", fake_stream)
    client.chat(
        "test-model",
        [{"role": "user", "content": "Usa mcp__saludar para saludar a Marco."}],
        mcp_tools,
        lambda _text: None,
        lambda *_: "",
    )

    assert stream_tools == [mcp_tools]


def test_chat_blocks_unsolicited_native_write_tool_call(monkeypatch):
    client = OllamaClient()
    responses = iter([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "crear_archivo", "arguments": {"path": "intruso.txt"}}}],
        },
        {"role": "assistant", "content": "Respuesta informativa."},
    ])
    called = []

    monkeypatch.setattr(client, "_stream", lambda *args, **kwargs: next(responses))
    result = client.chat(
        "test-model",
        [{"role": "user", "content": "¿Qué es el diseño editorial?"}],
        [{"type": "function", "function": {"name": "crear_archivo"}}],
        lambda _text: None,
        lambda name, arguments: called.append((name, arguments)) or "no debe ejecutarse",
    )

    assert result == "Respuesta informativa."
    assert called == []


def test_chat_allows_explicit_native_workspace_request(monkeypatch):
    client = OllamaClient()
    responses = iter([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "crear_archivo", "arguments": {"path": "nota.txt"}}}],
        },
        {"role": "assistant", "content": "Preparado."},
    ])
    called = []

    monkeypatch.setattr(client, "_stream", lambda *args, **kwargs: next(responses))
    result = client.chat(
        "test-model",
        [{"role": "user", "content": "Crea el archivo nota.txt en el workspace."}],
        [{"type": "function", "function": {"name": "crear_archivo"}}],
        lambda _text: None,
        lambda name, arguments: called.append((name, arguments)) or "Archivo creado: nota.txt",
    )

    assert result == "Preparado."
    assert called == [("crear_archivo", {"path": "nota.txt"})]


def test_chat_cancellation_before_stream(monkeypatch):
    import threading
    from core.ollama import OllamaCancelled

    client = OllamaClient()
    cancelled = threading.Event()
    cancelled.set()

    def fake_stream(*args, **kwargs):
        raise AssertionError("no debe abrirse el stream si ya está cancelado")

    monkeypatch.setattr(client, "_stream", fake_stream)
    try:
        client.chat("test-model", [{"role": "user", "content": "Hola"}], None, lambda _: None, lambda *_: "", cancel_event=cancelled)
    except OllamaCancelled:
        pass
    else:
        raise AssertionError("se esperaba OllamaCancelled")


def test_mentions_workspace_operation_accepts_cambiar_y_modificar():
    from core.intent import ToolIntentGate

    assert ToolIntentGate._mentions_workspace_operation("cambia el archivo notas.txt")
    assert ToolIntentGate._mentions_workspace_operation("modifica el archivo notas.txt")


def test_textual_tool_call_detection():
    tool_names = {"leer_archivo", "escribir_archivo"}
    assert OllamaClient._textual_tool_call_name(
        '{"name": "leer_archivo", "parameters": {"path": "x.txt"}}', tool_names
    ) == "leer_archivo"
    assert OllamaClient._textual_tool_call_name("Solicito la herramienta crear_archivo.", tool_names) is None
    assert OllamaClient._textual_tool_call_name(
        '{"name": "otra_cosa", "parameters": {}}', tool_names
    ) is None
    # Detector relajado: un JSON con "name" de una herramienta conocida
    # cuenta como llamada textual, aunque no traiga "parameters".
    assert OllamaClient._textual_tool_call_name(
        '{"name": "leer_archivo"}', tool_names
    ) == "leer_archivo"


def test_stream_flags_textual_tool_call_without_showing_it(monkeypatch):
    import json as json_module

    class FakeResponse:
        def raise_for_status(self):
            return None

        def iter_lines(self):
            yield json_module.dumps({
                "message": {"content": '{"name": "leer_archivo", "parameters": {"path": "x.txt"}}'},
                "done": False,
            })
            yield json_module.dumps({"message": {}, "done": True})

        def iter_bytes(self, chunk_size=4096):
            # _stream pide bytes y los parte por \n para comprobar el
            # cancel_event con más frecuencia. Este método replica el
            # comportamiento de httpx real, devolviendo cada línea del
            # stream con su salto de línea correspondiente.
            lines = [
                json_module.dumps({
                    "message": {"content": '{"name": "leer_archivo", "parameters": {"path": "x.txt"}}'},
                    "done": False,
                }),
                json_module.dumps({"message": {}, "done": True}),
            ]
            for line in lines:
                yield (line + "\n").encode("utf-8")

    class FakeStreamCtx:
        def __enter__(self):
            return FakeResponse()

        def __exit__(self, *args):
            return False

    import httpx as httpx_module
    monkeypatch.setattr(httpx_module, "stream", lambda *a, **k: FakeStreamCtx())

    client = OllamaClient()
    seen = []
    message = client._stream(
        "test-model",
        [{"role": "user", "content": "edita x.txt"}],
        [{"type": "function", "function": {"name": "leer_archivo"}}],
        seen.append,
    )

    assert message["_textual_tool_name"] == "leer_archivo"
    assert seen == []


def test_chat_retries_once_after_textual_tool_call_then_succeeds(monkeypatch):
    client = OllamaClient()
    responses = iter([
        {"role": "assistant", "content": "", "_textual_tool_name": "leer_archivo"},
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "leer_archivo", "arguments": {"path": "x.txt"}}}],
        },
        {"role": "assistant", "content": "Listo."},
    ])
    calls = []

    def fake_stream(model, messages, tools, on_text, cancel_event=None, options=None):
        return next(responses)

    monkeypatch.setattr(client, "_stream", fake_stream)
    result = client.chat(
        "test-model",
        [{"role": "user", "content": "edita x.txt, cambia a por b"}],
        [{"type": "function", "function": {"name": "leer_archivo"}}],
        lambda _text: None,
        lambda name, args: calls.append((name, args)) or "contenido",
    )

    assert result == "Listo."
    assert calls == [("leer_archivo", {"path": "x.txt"})]


def test_chat_gives_up_after_repeated_textual_tool_call(monkeypatch):
    client = OllamaClient()
    responses = iter([
        {"role": "assistant", "content": "", "_textual_tool_name": "leer_archivo"},
        {"role": "assistant", "content": "", "_textual_tool_name": "leer_archivo"},
    ])

    def fake_stream(model, messages, tools, on_text, cancel_event=None, options=None):
        return next(responses)

    monkeypatch.setattr(client, "_stream", fake_stream)
    seen = []
    result = client.chat(
        "test-model",
        [{"role": "user", "content": "edita x.txt, cambia a por b"}],
        [{"type": "function", "function": {"name": "leer_archivo"}}],
        seen.append,
        lambda *_: "",
    )

    assert "No se pudo completar" in result
    assert seen == [result]


def test_chat_exposes_tools_for_cambiar_archivo(monkeypatch):
    client = OllamaClient()
    stream_tools = []

    def fake_stream(model, messages, tools, on_text, cancel_event=None, options=None):
        stream_tools.append(tools)
        return {"role": "assistant", "content": "Hecho."}

    monkeypatch.setattr(client, "_stream", fake_stream)
    client.chat(
        "test-model",
        [{"role": "user", "content": "Cambia el archivo notas.txt y pon 'hola'"}],
        [{"type": "function", "function": {"name": "escribir_archivo"}}],
        lambda _text: None,
        lambda *_: "",
    )

    assert stream_tools and stream_tools[0] is not None


def test_chat_blocks_tool_name_mentioned_without_request(monkeypatch):
    client = OllamaClient()
    responses = iter([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "crear_archivo", "arguments": {"path": "intruso.txt"}}}],
        },
        {"role": "assistant", "content": "No hace falta modificar nada."},
    ])
    called = []
    monkeypatch.setattr(client, "_stream", lambda *args, **kwargs: next(responses))
    result = client.chat(
        "test-model",
        [{"role": "user", "content": "¿Qué hace la herramienta crear_archivo?"}],
        [{"type": "function", "function": {"name": "crear_archivo"}}],
        lambda _text: None,
        lambda name, arguments: called.append((name, arguments)) or "NO DEBE EJECUTARSE",
    )
    assert result == "No hace falta modificar nada."
    assert called == []


def test_chat_requires_action_for_explicit_mcp_name(monkeypatch):
    client = OllamaClient()
    responses = iter([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "mcp__saludar", "arguments": {"nombre": "Marco"}}}],
        },
        {"role": "assistant", "content": "Hecho."},
    ])
    called = []
    monkeypatch.setattr(client, "_stream", lambda *args, **kwargs: next(responses))
    result = client.chat(
        "test-model",
        [{"role": "user", "content": "¿Qué hace mcp__saludar?"}],
        [{"type": "function", "function": {"name": "mcp__saludar"}}],
        lambda _text: None,
        lambda name, arguments: called.append((name, arguments)) or "NO DEBE EJECUTARSE",
    )
    assert result == "Hecho."
    assert called == []


def test_chat_allows_mcp_name_with_explicit_action(monkeypatch):
    client = OllamaClient()
    responses = iter([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{"function": {"name": "mcp__saludar", "arguments": {"nombre": "Marco"}}}],
        },
        {"role": "assistant", "content": "Hecho."},
    ])
    called = []
    monkeypatch.setattr(client, "_stream", lambda *args, **kwargs: next(responses))
    result = client.chat(
        "test-model",
        [{"role": "user", "content": "Usa mcp__saludar para saludar a Marco."}],
        [{"type": "function", "function": {"name": "mcp__saludar"}}],
        lambda _text: None,
        lambda name, arguments: called.append((name, arguments)) or "Hola, Marco",
    )
    assert result == "Hecho."
    assert called == [("mcp__saludar", {"nombre": "Marco"})]


def test_chat_sends_tool_name_with_mcp_tool_result(monkeypatch):
    client = OllamaClient()
    seen_histories = []
    responses = iter([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [{
                "function": {
                    "name": "mcp__fs__list_directory",
                    "arguments": {"path": "."},
                }
            }],
        },
        {"role": "assistant", "content": "He encontrado los archivos."},
    ])

    def fake_stream(model, messages, tools, on_text, cancel_event=None, options=None):
        seen_histories.append([dict(message) for message in messages])
        return next(responses)

    monkeypatch.setattr(client, "_stream", fake_stream)
    result = client.chat(
        "test-model",
        [{"role": "user", "content": "lista los archivos de mi workspace"}],
        [{"type": "function", "function": {"name": "mcp__fs__list_directory"}}],
        lambda _text: None,
        lambda name, arguments: "a.txt\nb.txt",
    )

    assert result == "He encontrado los archivos."
    tool_messages = [m for m in seen_histories[1] if m.get("role") == "tool"]
    assert tool_messages == [{
        "role": "tool",
        "content": "a.txt\nb.txt",
        "tool_name": "mcp__fs__list_directory",
    }]


def test_textual_shell_tool_call_detection():
    tool_names = {"mcp__fs__read_text_file", "mcp__fs__write_file"}
    assert OllamaClient._textual_tool_call_name(
        "$ mcp__fs__read_text_file README.md", tool_names
    ) == "mcp__fs__read_text_file"
    assert OllamaClient._textual_tool_call_name(
        "texto normal\n$ mcp__fs__write_file prueba.txt", tool_names
    ) == "mcp__fs__write_file"
    assert OllamaClient._textual_tool_call_name(
        "$ otra_herramienta README.md", tool_names
    ) is None


def test_mcp_filesystem_aliases_allow_read_text_and_write_file(tmp_path):
    """Las herramientas MCP que son alias heredan las reglas combinadas del
    núcleo. write_file combina crear_archivo y escribir_archivo, así que
    autoriza tanto "crea" como "modifica"."""
    from core.intent import ToolIntentGate
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    from plugins.mcp.bridge import MCPToolBridge

    registry = ToolRegistry(Workspace(tmp_path))
    core_rules = registry.intent_rules()
    combined_write = MCPToolBridge._inherit_core_rule("write_file", core_rules)
    assert combined_write is not None

    rules = {
        "mcp__fs__read_text_file": core_rules["leer_archivo"],
        "mcp__fs__write_file": combined_write,
    }
    ToolIntentGate.register_rules(rules)
    gate = ToolIntentGate(rules)

    assert gate.tool_is_requested("mcp__fs__read_text_file", "lee README.md")
    assert gate.tool_is_requested(
        "mcp__fs__write_file", 'crea un archivo llamado prueba.txt con el texto "hola"'
    )
    assert gate.tool_is_requested(
        "mcp__fs__write_file", "modifica el archivo README.md"
    )
    assert gate.tool_is_requested(
        "mcp__fs__write_file", "escribe el archivo README.md con contenido nuevo"
    )
