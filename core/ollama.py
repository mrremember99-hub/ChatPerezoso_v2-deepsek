from __future__ import annotations

import asyncio
import codecs
import json
import logging
import re
import threading
from typing import Any, AsyncIterator, Callable

import httpx

from .async_runner import AsyncRunner, _CancelledByEvent
from .stream_events import (
    StreamEvent,
    StreamFinished,
    TextDelta,
    ToolCallsDelta,
)
from .intent import ToolIntentGate
from .model_capabilities import get_capabilities
from .tool_strategies import (
    NativeToolStrategy,
    XmlToolStrategy,
    authorize_and_execute,
)
from .xml_tools import build_tools_prompt, parse_tool_calls, strip_tool_call_blocks


logger = logging.getLogger(__name__)


class OllamaError(Exception):
    pass


class OllamaCancelled(OllamaError):
    """La generación fue cancelada por el usuario."""


class OllamaClient:
    """Cliente de transporte para la API de Ollama.

    Soporta dos modos de tool calling:
      · NATIVE: si el modelo declara `tools` en /api/show. Se envía el
        parámetro `tools` al payload y se leen los tool_calls nativos.
      · XML:    si el modelo no declara `tools`. Las herramientas se
        describen en el system prompt y el modelo responde con bloques
        <tool_call>{...}</tool_call>, que parseamos con xml_tools.

    Esta bifurcación evita el regex sobre JSON crudo (frágil y lleno de
    falsos positivos) para modelos como deepseek-r1 que nunca emiten
    tool_calls nativos.
    """

    _TEXTUAL_CALL_NAME = re.compile(r'"name"\s*:\s*"([a-zA-Z0-9_]+)"')
    _TEXTUAL_SHELL_CALL = re.compile(r"(?:^|\n)\s*\$\s*([a-zA-Z0-9_]+)(?:\s|$)")

    def __init__(self, host: str = "http://localhost:11434"):
        self.host = host.rstrip("/")
        self.timeout = httpx.Timeout(connect=10.0, read=60.0, write=30.0, pool=10.0)
        # Runner dedicado para corrutinas httpx. Ver core/async_runner.py.
        self._async_runner = AsyncRunner(name="OllamaAsync")
        # Cliente HTTP persistente, creado la primera vez y reutilizado
        # entre turnos. Vive dentro del loop del runner (httpx.AsyncClient
        # está atado al loop en el que se crea). Se cierra al shutdown.
        self._http_client: httpx.AsyncClient | None = None
        self._async_runner.set_close_callback(self._close_http_client)

    # -- API pública ---------------------------------------------------------

    def force_close_active(self) -> None:
        """No-op por compatibilidad.

        Antes cerrábamos la respuesta HTTP desde otro hilo, pero httpx
        sync no es thread-safe para eso y causaba segfaults. Ahora la
        cancelación se hace vía future.cancel() en el event loop del
        AsyncRunner, que interrumpe el await sin tocar el socket.
        """
        pass

    # -- cliente HTTP persistente -------------------------------------------

    async def _get_client(self) -> httpx.AsyncClient:
        """Devuelve el AsyncClient persistente, creándolo si hace falta.

        El cliente vive dentro del event loop del runner. Se reutiliza
        entre turnos para no recrear el pool de conexiones cada vez.
        """
        # getattr defensivo: los mocks de tests pueden no exponer is_closed.
        if (
            self._http_client is None
            or getattr(self._http_client, "is_closed", False)
        ):
            self._http_client = httpx.AsyncClient(
                timeout=self.timeout,
                limits=httpx.Limits(
                    max_keepalive_connections=2,
                    max_connections=4,
                ),
            )
        return self._http_client

    async def _close_http_client(self) -> None:
        """Cierra el AsyncClient persistente. Se ejecuta dentro del loop."""
        if (
            self._http_client is not None
            and not getattr(self._http_client, "is_closed", True)
        ):
            try:
                await self._http_client.aclose()
            except Exception as exc:
                logger.warning("Error cerrando AsyncClient: %s", exc)
        self._http_client = None

    def shutdown(self) -> None:
        """Libera recursos del cliente. Llamar al cerrar la app."""
        self._async_runner.close()

    def list_models(self) -> list[str]:
        try:
            response = httpx.get(f"{self.host}/api/tags", timeout=10)
            response.raise_for_status()
            return [
                item.get("name", "")
                for item in response.json().get("models", [])
                if item.get("name")
            ]
        except (httpx.HTTPError, ValueError) as exc:
            raise OllamaError(f"No se pudo consultar Ollama: {exc}") from exc

    def chat(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: Any,
        on_text: Callable[[str], None],
        on_tool: Callable[[str, dict[str, Any]], str],
        max_rounds: int = 8,
        cancel_event: threading.Event | None = None,
        options: dict[str, Any] | None = None,
        system_prompt: str = "",
    ) -> str:
        if not model:
            raise OllamaError("No hay un modelo seleccionado.")

        # 1. Detectar capacidades del modelo (cacheado).
        caps = get_capabilities(self.host, model)
        logger.debug(
            "Modelo %s: tool_mode=%s (probed=%s)",
            model, caps.tool_mode, caps.probed,
        )

        # 2. Elegir estrategia según el modo detectado.
        strategy = self._choose_strategy(caps, tools)

        # 3. Preparar historial y reglas de intención.
        history = [dict(message) for message in messages]
        definitions = self._extract_definitions(tools)
        authorization_text = self._last_user_text(history)
        gate = self._build_intent_gate(tools)
        active_tools = gate.tools_for_request(definitions, authorization_text)
        tool_names = {
            str(item.get("function", {}).get("name", ""))
            for item in (active_tools or [])
            if item.get("function", {}).get("name")
        }

        # 4. Componer el system prompt según la estrategia.
        tool_prompt = (
            strategy.prepare_system_prompt(active_tools) if active_tools else ""
        )
        self._inject_system_prompts(history, system_prompt, tool_prompt)

        send_tools = active_tools if strategy.should_send_tools_param() else None
        buffer_only = strategy.needs_full_buffer(bool(active_tools))

        # 5. Bucle de rondas delegando en la estrategia.
        textual_retry_used = False

        for _ in range(max_rounds):
            self._check_cancel(cancel_event)

            message = self._stream(
                model,
                history,
                send_tools,
                None if buffer_only else on_text,
                cancel_event=cancel_event,
                options=options,
            )

            result = strategy.process_round(message, tool_names)

            # Reintento por tool calling textual (solo nativo).
            if result.retry_requested:
                if not textual_retry_used:
                    textual_retry_used = True
                    history.append({
                        "role": "assistant",
                        "content": result.assistant_content,
                    })
                    history.append({"role": "user", "content": result.retry_message})
                    continue
                # Ya se reintentó una vez: fallar con mensaje al usuario.
                final_text = (
                    "No se pudo completar la operación: el modelo no logró "
                    "invocar la herramienta mediante la llamada nativa tras "
                    "reintentarlo. Reformula la petición."
                )
                on_text(final_text)
                return final_text

            # Mostrar al usuario el texto visible (sin bloques XML).
            if result.visible_text:
                on_text(result.visible_text)

            # Respuesta final.
            if result.is_final:
                return result.final_text

            # Hay tool calls: ejecutar y volver a la siguiente ronda.
            if result.tool_calls:
                history.append({
                    "role": "assistant",
                    "content": result.assistant_content,
                })
                for name, args in result.tool_calls:
                    result_text = authorize_and_execute(
                        name, args, gate, authorization_text, on_tool
                    )
                    history.append(strategy.format_tool_result(name, result_text))
                continue

            # Sin tool calls y sin ser final: caso raro (no debería
            # ocurrir con las estrategias actuales). Cerramos.
            return result.final_text

        raise OllamaError("Se alcanzó el límite de rondas de herramientas.")

    @staticmethod
    def _choose_strategy(caps, tools) -> Any:
        """Selecciona la estrategia según el modo de tool calling.

        Devuelve NativeToolStrategy o XmlToolStrategy. Añadir un tercer
        modo sería añadir una rama aquí, sin tocar el bucle de chat().
        """
        if caps.tool_mode == "xml":
            return XmlToolStrategy()
        return NativeToolStrategy()

    # -- extracción y construcción de gates ---------------------------------

    @staticmethod
    def _extract_definitions(tools: Any) -> list[dict[str, Any]] | None:
        if tools is None:
            return None
        definitions_method = getattr(tools, "definitions", None)
        if callable(definitions_method):
            try:
                defs = definitions_method()
            except Exception:
                return None
            return list(defs) if defs else None
        if isinstance(tools, list):
            return list(tools) if tools else None
        return None

    @staticmethod
    def _build_intent_gate(tools: Any) -> ToolIntentGate:
        if tools is None:
            return ToolIntentGate({})
        rules_method = getattr(tools, "intent_rules", None)
        if callable(rules_method):
            rules = rules_method()
            ToolIntentGate.register_rules(rules)
            return ToolIntentGate(rules)
        return ToolIntentGate(dict(ToolIntentGate._RULES_REGISTRY))

    # -- system prompt -------------------------------------------------------

    @staticmethod
    def _inject_system_prompts(
        history: list[dict[str, Any]],
        user_prompt: str,
        tool_prompt: str,
    ) -> None:
        parts: list[str] = []
        existing = ""
        for message in history:
            if message.get("role") == "system":
                existing = str(message.get("content", ""))
                break
        if existing:
            parts.append(existing)
        if user_prompt.strip():
            parts.append(user_prompt.strip())
        if tool_prompt.strip():
            parts.append(tool_prompt.strip())

        if not parts:
            return

        combined = "\n\n".join(parts)
        for message in history:
            if message.get("role") == "system":
                message["content"] = combined
                return
        history.insert(0, {"role": "system", "content": combined})

    @staticmethod
    def _tool_system_prompt(active_tools: list[dict[str, Any]]) -> str:
        tool_names = [
            str(item.get("function", {}).get("name", ""))
            for item in active_tools
            if item.get("function", {}).get("name")
        ]
        whitelist = "\n".join(f"- {name}" for name in tool_names)
        return (
            "## REGLAS CRÍTICAS\n"
            "Tienes acceso a un conjunto CERRADO de herramientas. Todas las "
            "demás están PROHIBIDAS.\n\n"
            "## HERRAMIENTAS PERMITIDAS (única lista válida)\n"
            f"{whitelist}\n\n"
            "## PROHIBICIONES ABSOLUTAS\n"
            "- NUNCA inventes nombres de herramientas.\n"
            "- NUNCA uses herramientas para responder preguntas generales, "
            "explicar conceptos, opinar o redactar texto.\n"
            "- NUNCA escribas JSON de herramientas, comandos con prefijo $, "
            "ni bloques de código como sustituto de una llamada nativa.\n"
            "- NUNCA afirmes que una herramienta se ejecutó si no has recibido "
            "su resultado.\n"
            "- NUNCA infieras una acción sobre archivos a partir de una "
            "pregunta informativa.\n\n"
            "## CUÁNDO USAR HERRAMIENTAS\n"
            "Solo cuando la última petición del usuario solicite EXPLÍCITAMENTE "
            "una operación sobre el workspace. Para crear, escribir o borrar un "
            "archivo, el usuario debe pedir esa acción y ese archivo. Si pide "
            "crear un archivo sin nombre, elige uno razonable y usa la "
            "herramienta directamente; la aplicación se encarga de la "
            "confirmación.\n\n"
            "## RUTAS\n"
            "Todas las rutas son RELATIVAS al workspace. Usa \".\" para la "
            "raíz del workspace, nunca \"/\". Ejemplos válidos: \".\", "
            "\"subcarpeta\", \"archivo.txt\". NUNCA uses rutas absolutas "
            "como /, /tmp o /Users.\n\n"
            "## ORDEN DE OPERACIONES\n"
            "Antes de escribir un archivo existente, usa primero la herramienta "
            "de lectura para conocer su contenido completo. El contenido que "
            "envíes a la escritura debe ser el contenido FINAL COMPLETO, nunca "
            "un resumen ni una descripción de la instrucción.\n\n"
            "## LLAMADAS NATIVAS\n"
            "Usa exclusivamente las llamadas de herramienta nativas de Ollama."
        )

    @staticmethod
    def _last_user_text(history: list[dict[str, Any]]) -> str:
        """Devuelve el último mensaje del usuario que sea una instrucción real.

        Ignora los resultados de herramientas que en modo XML se envían
        con rol "user" pero con prefijo [TOOL_RESULT:name]. Es una
        salvaguarda frente al confused deputy: el contenido de un
        archivo leído no debe poder reautorizar operaciones.

        El filtro es defensivo. Hoy authorization_text se calcula una
        sola vez antes del bucle, así que los mensajes de tool result
        no se consultan. Pero si en el futuro se recalculara por ronda,
        este filtro evita que el contenido de un archivo se interprete
        como instrucción.
        """
        for message in reversed(history):
            if message.get("role") != "user":
                continue
            content = str(message.get("content") or "")
            if content.startswith("[TOOL_RESULT:"):
                continue
            return content
        return ""

    @staticmethod
    def _textual_tool_call_name(content: str, tool_names: set[str]) -> str | None:
        if "{" in content and '"name"' in content:
            match = OllamaClient._TEXTUAL_CALL_NAME.search(content)
            if match:
                name = match.group(1)
                if name in tool_names:
                    return name
        shell_match = OllamaClient._TEXTUAL_SHELL_CALL.search(content)
        if shell_match:
            name = shell_match.group(1)
            if name in tool_names:
                return name
        return None

    # -- streaming -----------------------------------------------------------

    def _stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        on_text: Callable[[str], None] | None,
        *,
        cancel_event: threading.Event | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Wrapper síncrono. Delega en _stream_async vía AsyncRunner."""
        try:
            return self._async_runner.submit(
                self._stream_async(
                    model, messages, tools, on_text,
                    cancel_event=cancel_event, options=options,
                ),
                cancel_event=cancel_event,
            )
        except _CancelledByEvent:
            raise OllamaCancelled("Operación cancelada por el usuario.")
        except TimeoutError as exc:
            raise OllamaError(
                "Ollama dejó de responder durante demasiado tiempo."
            ) from exc
        except httpx.HTTPError as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise OllamaCancelled(
                    "Operación cancelada por el usuario."
                ) from exc
            raise OllamaError(str(exc)) from exc
        except asyncio.CancelledError:
            raise OllamaCancelled("Operación cancelada por el usuario.")

    async def _iter_ollama_events(
        self,
        payload: dict[str, Any],
        *,
        cancel_event: threading.Event | None = None,
    ) -> AsyncIterator[StreamEvent]:
        """Itera sobre los eventos del stream de Ollama.

        Puro parsing NDJSON + emision de eventos tipados. No conoce
        callbacks ni la UI. Emite:
          - TextDelta por cada fragmento de texto.
          - ToolCallsDelta por cada bloque de tool_calls nativos.
          - StreamFinished al final, con el mensaje completo.

        Los errores se propagan por excepcion (OllamaError,
        OllamaCancelled), no como eventos.
        """
        message: dict[str, Any] = {"role": "assistant", "content": ""}
        content_parts: list[str] = []
        buffer = ""
        decoder = codecs.getincrementaldecoder("utf-8")(errors="replace")

        client = await self._get_client()
        async with client.stream(
            "POST", f"{self.host}/api/chat", json=payload
        ) as response:
            response.raise_for_status()

            async for raw_chunk in response.aiter_bytes(chunk_size=1024):
                if cancel_event is not None and cancel_event.is_set():
                    raise OllamaCancelled(
                        "Operacion cancelada por el usuario."
                    )
                buffer += decoder.decode(raw_chunk)
                while "\n" in buffer:
                    line, buffer = buffer.split("\n", 1)
                    line = line.strip()
                    if not line:
                        continue
                    event = self._handle_line_event(
                        line, message, content_parts
                    )
                    if event is not None:
                        yield event
                    if message.get("_done"):
                        break
                if message.get("_done"):
                    break

            tail = decoder.decode(b"", final=True)
            if tail:
                buffer += tail
            if buffer.strip() and not message.get("_done"):
                event = self._handle_line_event(
                    buffer.strip(), message, content_parts
                )
                if event is not None:
                    yield event

        message.pop("_done", None)
        message["content"] = "".join(content_parts)
        yield StreamFinished(message=message)

    async def _stream_async(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        on_text: Callable[[str], None] | None,
        *,
        cancel_event: threading.Event | None = None,
        options: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """Wrapper sync-friendly sobre _iter_ollama_events.

        Consume el iterador y aplica la logica de buffering
        adaptativo + callbacks. Mantenemos esta capa para no romper
        la firma de chat() todavia.
        """
        payload: dict[str, Any] = {
            "model": model,
            "messages": messages,
            "stream": True,
            "keep_alive": "30m",
        }
        if tools:
            payload["tools"] = tools
        if options:
            payload["options"] = options

        message: dict[str, Any] = {}
        buffering_textual = False

        async for event in self._iter_ollama_events(
            payload, cancel_event=cancel_event
        ):
            if isinstance(event, TextDelta):
                if on_text is not None and not buffering_textual:
                    prefix = event.text.lstrip()[:1]
                    if prefix in ("{", "["):
                        buffering_textual = True
                    else:
                        on_text(event.text)
            elif isinstance(event, StreamFinished):
                message = event.message

        content = message.get("content", "")
        textual_name: str | None = None
        if content and not message.get("tool_calls"):
            tool_names = {
                str(item.get("function", {}).get("name", ""))
                for item in (tools or [])
                if item.get("function", {}).get("name")
            }
            if tool_names:
                textual_name = self._textual_tool_call_name(
                    content, tool_names
                )
                if textual_name:
                    message["_textual_tool_name"] = textual_name

        # Si activamos buffering pero NO resulto ser tool call, el
        # usuario no ha visto nada: emitimos el texto completo.
        if buffering_textual and on_text is not None and not textual_name:
            on_text(content)

        return message

    @staticmethod
    def _handle_line_event(
        line: str,
        message: dict[str, Any],
        content_parts: list[str],
    ) -> StreamEvent | None:
        """Procesa una linea NDJSON. Devuelve un StreamEvent o None."""
        try:
            data = json.loads(line)
        except ValueError:
            return None
        chunk = data.get("message") or {}

        calls_raw = chunk.get("tool_calls")
        if calls_raw:
            message.setdefault("tool_calls", []).extend(calls_raw)

        delta: str | None = None
        if chunk.get("content"):
            delta = str(chunk["content"])
            content_parts.append(delta)

        if data.get("done"):
            message["_done"] = True

        if delta is not None:
            return TextDelta(delta)
        if calls_raw:
            return ToolCallsDelta(calls=tuple(calls_raw))
        return None

    @staticmethod
    @staticmethod
    def _handle_line(
        line: str,
        message: dict[str, Any],
        content_parts: list[str],
    ) -> str | None:
        """Procesa una línea NDJSON. Devuelve el delta si lo hay."""
        try:
            data = json.loads(line)
        except ValueError:
            return None
        chunk = data.get("message") or {}
        delta: str | None = None
        if chunk.get("content"):
            delta = str(chunk["content"])
            content_parts.append(delta)
        if chunk.get("tool_calls"):
            message.setdefault("tool_calls", []).extend(chunk["tool_calls"])
        if data.get("done"):
            message["_done"] = True
        return delta

    @staticmethod
    def _check_cancel(cancel_event: threading.Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise OllamaCancelled("Operación cancelada por el usuario.")
