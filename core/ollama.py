from __future__ import annotations

import json
import logging
import re
import threading
from typing import Any, Callable

import httpx

from .intent import ToolIntentGate
from .model_capabilities import get_capabilities
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
        self._active_response: httpx.Response | None = None
        self._active_lock = threading.Lock()

    # -- API pública ---------------------------------------------------------

    def force_close_active(self) -> None:
        with self._active_lock:
            response = self._active_response
        if response is not None:
            try:
                response.close()
            except Exception:
                pass

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
        use_xml = caps.tool_mode == "xml"
        logger.debug(
            "Modelo %s: tool_mode=%s (probed=%s)",
            model, caps.tool_mode, caps.probed,
        )

        # 2. Preparar historial y reglas de intención.
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

        # 3. Componer el system prompt según el modo.
        if use_xml:
            tool_prompt = build_tools_prompt(active_tools) if active_tools else ""
        else:
            tool_prompt = self._tool_system_prompt(active_tools) if active_tools else ""
        self._inject_system_prompts(history, system_prompt, tool_prompt)

        # En modo XML, con tools activas, buffereamos todo el stream
        # (no podemos saber si el modelo escribe texto o un bloque XML
        # hasta el final). Sin tools activas, streaming normal.
        buffer_only = use_xml and bool(active_tools)

        final_text = ""
        textual_retry_used = False

        for _ in range(max_rounds):
            self._check_cancel(cancel_event)

            message = self._stream(
                model,
                history,
                active_tools if not use_xml else None,
                None if buffer_only else on_text,
                cancel_event=cancel_event,
                options=options,
            )

            content = str(message.get("content") or "")

            # ---- Modo XML: parsear bloques del texto ----
            if use_xml and active_tools:
                xml_calls = parse_tool_calls(content, known_tools=tool_names)
                visible = strip_tool_call_blocks(content)

                if xml_calls:
                    if visible:
                        on_text(visible)
                    history.append({"role": "assistant", "content": content})
                    for name, args in xml_calls:
                        if not gate.tool_is_requested(name, authorization_text):
                            result_text = (
                                "ERROR: llamada de herramienta bloqueada: la "
                                "última petición del usuario no solicita esa "
                                "operación sobre el workspace."
                            )
                        else:
                            result_text = on_tool(name, args)
                        history.append({
                            "role": "user",
                            "content": f"[TOOL_RESULT:{name}]\n{result_text}",
                        })
                    continue

                # Sin bloques XML: es la respuesta final.
                if visible:
                    on_text(visible)
                    final_text += visible
                return final_text

            # ---- Modo NATIVE: tool_calls nativos ----
            tool_calls = message.get("tool_calls") or []

            if not tool_calls:
                textual_name = message.get("_textual_tool_name")
                if textual_name:
                    if not textual_retry_used:
                        textual_retry_used = True
                        history.append({"role": "assistant", "content": content})
                        history.append({
                            "role": "user",
                            "content": (
                                f"Has escrito el JSON de la herramienta "
                                f"«{textual_name}» como texto normal. No ejecutes "
                                "herramientas así. Repite la operación usando "
                                "EXCLUSIVAMENTE la llamada nativa de tool calling "
                                "que Ollama expone en el parámetro `tools`. No "
                                "escribas JSON en el mensaje."
                            ),
                        })
                        continue
                    final_text = (
                        f"No se pudo completar la operación: el modelo no logró "
                        f"invocar «{textual_name}» mediante la llamada nativa tras "
                        "reintentarlo. Reformula la petición."
                    )
                    on_text(final_text)
                    return final_text
                if content:
                    final_text += content
                return final_text

            history.append(message)
            for call in tool_calls:
                function = call.get("function", {})
                name = str(function.get("name", ""))
                arguments = function.get("arguments", {})
                if isinstance(arguments, str):
                    try:
                        arguments = json.loads(arguments)
                    except ValueError:
                        arguments = {}
                if not isinstance(arguments, dict):
                    arguments = {}
                if not gate.tool_is_requested(name, authorization_text):
                    result_text = (
                        "ERROR: llamada de herramienta bloqueada: la última "
                        "petición del usuario no solicita esa operación sobre "
                        "el workspace."
                    )
                else:
                    result_text = on_tool(name, arguments)
                history.append({
                    "role": "tool",
                    "content": result_text,
                    "tool_name": name,
                })

        raise OllamaError("Se alcanzó el límite de rondas de herramientas.")

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

        message: dict[str, Any] = {"role": "assistant", "content": ""}
        content_parts: list[str] = []
        buffer = ""

        try:
            with httpx.stream(
                "POST", f"{self.host}/api/chat", json=payload, timeout=self.timeout
            ) as response:
                with self._active_lock:
                    self._active_response = response
                try:
                    response.raise_for_status()

                    # Leemos en chunks de 1024 bytes. Cada chunk
                    # comprueba el cancel_event. Con iter_lines() el
                    # hilo se quedaba bloqueado esperando la siguiente
                    # linea y no veia el cancel hasta que Ollama
                    # enviara algo nuevo.
                    for raw_chunk in response.iter_bytes(chunk_size=1024):
                        if cancel_event is not None and cancel_event.is_set():
                            raise OllamaCancelled(
                                "Operación cancelada por el usuario."
                            )
                        buffer += raw_chunk.decode("utf-8", errors="replace")
                        while "\n" in buffer:
                            line, buffer = buffer.split("\n", 1)
                            line = line.strip()
                            if not line:
                                continue
                            self._handle_line(line, message, content_parts)
                            if message.get("_done"):
                                break
                        if message.get("_done"):
                            break

                    # Linea final sin salto
                    if buffer.strip() and not message.get("_done"):
                        self._handle_line(buffer.strip(), message, content_parts)

                finally:
                    with self._active_lock:
                        self._active_response = None

        except httpx.HTTPError as exc:
            if cancel_event is not None and cancel_event.is_set():
                raise OllamaCancelled("Operación cancelada por el usuario.") from exc
            raise OllamaError(str(exc)) from exc

        content = "".join(content_parts)
        message.pop("_done", None)

        if content and not message.get("tool_calls") and on_text is not None:
            tool_names = {
                str(item.get("function", {}).get("name", ""))
                for item in (tools or [])
                if item.get("function", {}).get("name")
            }
            textual_name = (
                self._textual_tool_call_name(content, tool_names)
                if tool_names
                else None
            )
            if textual_name:
                message["_textual_tool_name"] = textual_name
            else:
                on_text(content)
        message["content"] = content
        return message
    @staticmethod
    def _handle_line(
        line: str,
        message: dict[str, Any],
        content_parts: list[str],
    ) -> None:
        try:
            data = json.loads(line)
        except ValueError:
            return
        chunk = data.get("message") or {}
        if chunk.get("content"):
            content_parts.append(str(chunk["content"]))
        if chunk.get("tool_calls"):
            message.setdefault("tool_calls", []).extend(chunk["tool_calls"])
        if data.get("done"):
            message["_done"] = True

    @staticmethod
    def _check_cancel(cancel_event: threading.Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise OllamaCancelled("Operación cancelada por el usuario.")
