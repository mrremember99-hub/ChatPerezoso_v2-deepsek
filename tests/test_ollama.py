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
    """_mentions_workspace_operation ahora es método de instancia.

    Antes era classmethod y leía el registro global. Ahora usa
    self.rules, así que hay que construir un gate con las reglas
    del núcleo antes de llamarlo.
    """
    from core.intent import ToolIntentGate
    from core.tools import ToolRegistry
    from core.workspace import Workspace
    import tempfile

    with tempfile.TemporaryDirectory() as td:
        rules = ToolRegistry(Workspace(td)).intent_rules()
        gate = ToolIntentGate(rules)
        assert gate._mentions_workspace_operation("cambia el archivo notas.txt")
        assert gate._mentions_workspace_operation("modifica el archivo notas.txt")


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
    import httpx

    class FakeResponse:
        def raise_for_status(self):
            return None

        async def aiter_bytes(self, chunk_size=1024):
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
        async def __aenter__(self):
            return FakeResponse()

        async def __aexit__(self, *args):
            return False

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            pass
        async def __aenter__(self):
            return self
        async def __aexit__(self, *args):
            return False
        def stream(self, *a, **k):
            return FakeStreamCtx()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = OllamaClient()
    seen = []
    message = client._stream(
        "test-model",
        [{"role": "user", "content": "edita x.txt"}],
        [{"type": "function", "function": {"name": "leer_archivo"}}],
        seen.append,
    )

    # Investigacion 2026-09-26 (§5): el JSON limpio ahora se
    # recupera como tool_call nativo en vez de solo marcar el
    # nombre. El stream no emite nada al usuario (JSON filtrado).
    assert message.get("tool_calls"), message
    assert message["tool_calls"][0]["function"]["name"] == "leer_archivo"
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


def test_chat_native_mode_does_not_duplicate_text_in_on_text(monkeypatch):
    """Regresion: en modo nativo, el texto se muestra UNA vez."""
    import json as _json
    import httpx
    from core import model_capabilities

    model_capabilities.clear_cache()

    class _ShowResponse:
        def raise_for_status(self): return None
        def json(self): return {"capabilities": ["tools"]}

    monkeypatch.setattr(httpx, "post", lambda *a, **k: _ShowResponse())

    lineas = [
        _json.dumps({"message": {"content": "Hola"}, "done": False}),
        _json.dumps({"message": {}, "done": True}),
    ]

    class _FakeResponse:
        def raise_for_status(self): return None
        async def aiter_bytes(self, chunk_size=1024):
            for linea in lineas:
                yield (linea + "\n").encode("utf-8")

    class _FakeCtx:
        async def __aenter__(self): return _FakeResponse()
        async def __aexit__(self, *a): return False

    class _FakeAsyncClient:
        def __init__(self, *a, **k): pass
        async def __aenter__(self): return self
        async def __aexit__(self, *a): return False
        def stream(self, *a, **k): return _FakeCtx()

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)

    from core.ollama import OllamaClient
    client = OllamaClient()
    chunks = []
    result = client.chat(
        "test-native",
        [{"role": "user", "content": "Hola"}],
        None,
        chunks.append,
        lambda *_: "",
    )

    assert result == "Hola"
    assert chunks == ["Hola"], f"Texto duplicado: {chunks}"

# -- parche AD: tool_calls en el historial ---------------------------------

def test_chat_history_includes_tool_calls_in_assistant_message(monkeypatch):
    """El mensaje assistant del historial debe llevar tool_calls.

    Sin esto, el chat template del modelo ve un mensaje `tool`
    huérfano en la ronda 2 y vuelve a llamar a la misma herramienta.
    """
    client = OllamaClient()
    seen_histories = []
    responses = iter([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "listar_carpeta", "arguments": {"path": "."}}}
            ],
        },
        {"role": "assistant", "content": "Hecho."},
    ])

    def fake_stream(model, messages, tools, on_text, cancel_event=None, options=None):
        # Guardar copia del historial tal como lo recibe el modelo.
        seen_histories.append([dict(m) for m in messages])
        return next(responses)

    monkeypatch.setattr(client, "_stream", fake_stream)
    client.chat(
        "test-model",
        [{"role": "user", "content": "lista la carpeta"}],
        [{"type": "function", "function": {"name": "listar_carpeta"}}],
        lambda _text: None,
        lambda name, arguments: "archivo1.txt\narchivo2.txt",
    )

    # La segunda llamada al modelo debe llevar en el historial:
    #   1. user
    #   2. assistant con tool_calls no vacíos
    #   3. tool con el resultado
    assert len(seen_histories) >= 2
    second_round = seen_histories[1]
    assistant_messages = [
        m for m in second_round if m.get("role") == "assistant"
    ]
    assert assistant_messages, "No hay mensaje assistant en la ronda 2"
    # El mensaje assistant debe llevar tool_calls.
    has_tool_calls = any(
        m.get("tool_calls") for m in assistant_messages
    )
    assert has_tool_calls, (
        "El mensaje assistant del historial no lleva tool_calls. "
        "Esto provoca que el modelo reejecute la tool en bucle."
    )


def test_chat_history_assistant_tool_calls_match_executed(monkeypatch):
    """Los tool_calls del historial deben coincidir con los ejecutados."""
    client = OllamaClient()
    seen_histories = []
    responses = iter([
        {
            "role": "assistant",
            "content": "",
            "tool_calls": [
                {"function": {"name": "listar_carpeta", "arguments": {"path": "sub"}}}
            ],
        },
        {"role": "assistant", "content": "OK."},
    ])

    def fake_stream(model, messages, tools, on_text, cancel_event=None, options=None):
        seen_histories.append([dict(m) for m in messages])
        return next(responses)

    monkeypatch.setattr(client, "_stream", fake_stream)
    client.chat(
        "test-model",
        [{"role": "user", "content": "lista la sub"}],
        [{"type": "function", "function": {"name": "listar_carpeta"}}],
        lambda _text: None,
        lambda name, arguments: "a.txt",
    )

    second_round = seen_histories[1]
    assistant_msgs = [m for m in second_round if m.get("role") == "assistant"]
    assert assistant_msgs
    tool_calls = assistant_msgs[0].get("tool_calls") or []
    assert len(tool_calls) == 1
    assert tool_calls[0]["function"]["name"] == "listar_carpeta"
    assert tool_calls[0]["function"]["arguments"] == {"path": "sub"}




def test_chat_does_not_mutate_caller_system_message(monkeypatch):
    """Regresión: _inject_system_prompts mutaba el dict del llamante.

    `chat()` documenta que los mensajes de entrada quedan intactos.
    Con la implementación anterior, el system message se modificaba
    in-place y el dict del llamante quedaba corrompido: en el
    siguiente turno, el prompt acumulaba contenido del turno anterior.
    """
    client = OllamaClient()
    system_msg = {"role": "system", "content": "base"}
    user_msg = {"role": "user", "content": "hola"}
    messages = [system_msg, user_msg]

    sent_messages = []

    def fake_stream(model, msgs, tools, on_text, cancel_event=None,
                    options=None):
        sent_messages.append([dict(m) for m in msgs])
        return {"role": "assistant", "content": "ok"}

    monkeypatch.setattr(client, "_stream", fake_stream)
    client.chat(
        "test-model",
        messages,
        None,
        lambda _: None,
        lambda *_: "",
        system_prompt="extra",
    )

    assert system_msg == {"role": "system", "content": "base"}
    assert messages[0] is system_msg
    assert len(messages) == 2

    assert sent_messages
    sent_system = sent_messages[0][0]
    assert sent_system["role"] == "system"
    assert sent_system["content"] == "base\n\nextra"


def test_chat_does_not_mutate_caller_messages_list(monkeypatch):
    """Complementario: chat() no debe alterar la lista original."""
    client = OllamaClient()
    messages = [
        {"role": "system", "content": "s"},
        {"role": "user", "content": "u"},
    ]
    original_len = len(messages)
    original_ids = [id(m) for m in messages]

    def fake_stream(model, msgs, tools, on_text, cancel_event=None,
                    options=None):
        return {"role": "assistant", "content": "ok"}

    monkeypatch.setattr(client, "_stream", fake_stream)
    client.chat(
        "test-model", messages, None, lambda _: None, lambda *_: "",
        system_prompt="extra",
    )

    assert len(messages) == original_len
    assert [id(m) for m in messages] == original_ids


# -- Prompt de tools: vía intermedia (auditoria 2026-09-27) ------------

def test_tool_system_prompt_no_tiene_bloques_eliminados():
    """Los bloques eliminados en la vía intermedia no vuelven."""
    from core.ollama import OllamaClient
    tools = [{"function": {"name": "leer_archivo"}}]
    prompt = OllamaClient._tool_system_prompt(tools)
    assert "## PROHIBICIONES ABSOLUTAS" not in prompt
    assert "## LLAMADAS NATIVAS" not in prompt


def test_tool_system_prompt_mantiene_titulos_criticos():
    """Los bloques que aportan siguen presentes."""
    from core.ollama import OllamaClient
    tools = [{"function": {"name": "leer_archivo"}}]
    prompt = OllamaClient._tool_system_prompt(tools)
    assert "## HERRAMIENTAS PERMITIDAS" in prompt
    assert "## CUÁNDO USAR HERRAMIENTAS" in prompt
    assert "## RUTAS" in prompt
    assert "## ORDEN DE OPERACIONES" in prompt
    assert "leer_archivo" in prompt


def test_tool_system_prompt_mantiene_prohibiciones_clave():
    """Las prohibiciones clave siguen, aunque sin sección propia."""
    from core.ollama import OllamaClient
    tools = [{"function": {"name": "leer_archivo"}}]
    prompt = OllamaClient._tool_system_prompt(tools)
    assert "CERRADO" in prompt
    assert "PROHIBIDAS" in prompt
    assert "Nunca inventes nombres" in prompt
    assert "JSON de herramientas" in prompt


# -- embeddings (C1) --------------------------------------------------------


class _FakeEmbedResponse:
    def __init__(self, payload, status=200):
        self._payload = payload
        self.status_code = status

    def raise_for_status(self):
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError(
                "boom", request=None, response=self,
            )

    def json(self):
        return self._payload


def _install_embed_fake(monkeypatch, *, payload=None, raises=None, assert_payload=None):
    """Instala un FakeAsyncClient que responde a /api/embed.

    Devuelve una lista donde el cliente guarda cada (url, json) para
    inspeccionar despues.
    """
    import httpx

    calls: list = []

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            self.is_closed = False

        async def aclose(self):
            self.is_closed = True

        async def post(self, url, json=None, timeout=None):
            calls.append((url, json))
            if assert_payload is not None:
                assert_payload(url, json)
            if raises is not None:
                raise raises
            return _FakeEmbedResponse(payload or {})

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)
    return calls


def test_embed_basico(monkeypatch):
    calls = _install_embed_fake(
        monkeypatch,
        payload={"embeddings": [[0.1, 0.2], [0.3, 0.4]]},
    )

    client = OllamaClient()
    try:
        result = client.embed(["hola", "mundo"])
    finally:
        client.shutdown()

    assert result == [[0.1, 0.2], [0.3, 0.4]]
    assert len(calls) == 1
    url, payload = calls[0]
    assert url.endswith("/api/embed")
    assert payload["model"] == "nomic-embed-text"
    assert payload["input"] == ["hola", "mundo"]


def test_embed_modelo_custom(monkeypatch):
    calls = _install_embed_fake(
        monkeypatch,
        payload={"embeddings": [[1.0]]},
    )

    client = OllamaClient()
    try:
        client.embed(["x"], model="otro-embed")
    finally:
        client.shutdown()

    assert calls[0][1]["model"] == "otro-embed"


def test_embed_lista_vacia_no_llama(monkeypatch):
    calls = _install_embed_fake(monkeypatch, payload={"embeddings": []})

    client = OllamaClient()
    try:
        assert client.embed([]) == []
    finally:
        client.shutdown()

    assert calls == []


def test_embed_respuesta_sin_embeddings(monkeypatch):
    _install_embed_fake(monkeypatch, payload={})

    client = OllamaClient()
    try:
        assert client.embed(["x"]) == []
    finally:
        client.shutdown()


def test_embed_embeddings_no_lista(monkeypatch):
    _install_embed_fake(monkeypatch, payload={"embeddings": "no"})

    client = OllamaClient()
    try:
        assert client.embed(["x"]) == []
    finally:
        client.shutdown()


def test_embed_filtra_vectores_no_lista(monkeypatch):
    _install_embed_fake(
        monkeypatch,
        payload={"embeddings": [[1.0, 2.0], None, "nope", [3.0]]},
    )

    client = OllamaClient()
    try:
        assert client.embed(["a", "b", "c", "d"]) == [[1.0, 2.0], [3.0]]
    finally:
        client.shutdown()


def test_embed_http_error_lanza_ollama_error(monkeypatch):
    import httpx as _httpx
    from core.ollama import OllamaError

    _install_embed_fake(
        monkeypatch,
        raises=_httpx.ConnectError("ollama caido"),
    )

    client = OllamaClient()
    try:
        with pytest.raises(OllamaError):
            client.embed(["x"])
    finally:
        client.shutdown()


def test_embed_convierte_a_float(monkeypatch):
    _install_embed_fake(
        monkeypatch,
        payload={"embeddings": [[1, 2, 3]]},  # ints
    )

    client = OllamaClient()
    try:
        result = client.embed(["x"])
    finally:
        client.shutdown()

    assert result == [[1.0, 2.0, 3.0]]
    assert all(isinstance(v, float) for v in result[0])


def test_embed_timeout_argumento(monkeypatch):
    """El timeout se propaga a la llamada httpx."""
    import httpx
    seen = {}

    class FakeResponse:
        status_code = 200
        def raise_for_status(self): pass
        def json(self): return {"embeddings": [[0.0]]}

    class FakeAsyncClient:
        def __init__(self, *a, **k):
            self.is_closed = False
        async def aclose(self): self.is_closed = True
        async def post(self, url, json=None, timeout=None):
            seen["timeout"] = timeout
            return FakeResponse()

    monkeypatch.setattr(httpx, "AsyncClient", FakeAsyncClient)

    client = OllamaClient()
    try:
        client.embed(["x"], timeout=12.5)
    finally:
        client.shutdown()

    assert seen["timeout"] == 12.5
