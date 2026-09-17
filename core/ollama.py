from __future__ import annotations

import json
import re
import threading
from typing import Any, Callable

import httpx

from .intent import ToolIntentGate


class OllamaError(Exception):
    pass


class OllamaCancelled(OllamaError):
    """La generación fue cancelada por el usuario."""


class OllamaClient:
    """Cliente de transporte para la API de Ollama (/api/tags, /api/chat).

    Se ocupa exclusivamente de: listar modelos, transmitir la conversación en
    streaming y orquestar el ciclo de tool calling nativo (incluyendo el
    reintento cuando el modelo escribe una llamada como texto en vez de
    usar el mecanismo nativo). La decisión de qué herramientas ofrecer y
    autorizar vive en ``ToolIntentGate``, no aquí.
    """

    # Detecta que el modelo escribió una llamada de herramienta como JSON en el
    # texto, en vez de usar el mecanismo nativo de tool_calls de Ollama.
    _TEXTUAL_CALL_NAME = re.compile(r'"name"\s*:\s*"([a-zA-Z0-9_]+)"')
    _TEXTUAL_SHELL_CALL = re.compile(r"(?:^|\n)\s*\$\s*([a-zA-Z0-9_]+)(?:\s|$)")

    def __init__(self, host: str = "http://localhost:11434"):
        self.host = host.rstrip("/")
        # read=300s: acota el caso del servidor muerto a mitad de stream sin
        # impedir generaciones razonablemente largas.
        self.timeout = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)

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
        """Ejecuta el bucle de conversación con tool calling.

        ``tools`` puede ser un ``ToolProvider`` (con ``definitions()`` e
        ``intent_rules()``) o directamente una lista de definiciones en
        formato Ollama. Aceptar ambas formas mantiene compatibilidad con
        tests y usos puntuales.
        """
        if not model:
            raise OllamaError("No hay un modelo seleccionado.")
        # Copia superficial de cada mensaje.
        history = [dict(message) for message in messages]
        # Extrae las definiciones si recibimos un provider. El resto del
        # método trabaja siempre con listas de diccionarios.
        definitions = self._extract_definitions(tools)
        # La autorización se evalúa contra la petición externa original.
        authorization_text = self._last_user_text(history)
        gate = self._build_intent_gate(tools)
        active_tools = gate.tools_for_request(definitions, authorization_text)
        tool_prompt = self._tool_system_prompt(active_tools) if active_tools else ""
        self._inject_system_prompts(history, system_prompt, tool_prompt)
        tools_enabled = bool(active_tools)
        final_text = ""
        textual_retry_used = False
        for _ in range(max_rounds):
            self._check_cancel(cancel_event)
            message = self._stream(
                model,
                history,
                active_tools if tools_enabled else None,
                on_text,
                cancel_event=cancel_event,
                options=options,
            )
            tool_calls = message.get("tool_calls") or []
            content = str(message.get("content") or "")
            if not tool_calls:
                textual_name = message.get("_textual_tool_name")
                if textual_name:
                    if not textual_retry_used:
                        textual_retry_used = True
                        history.append({
                            "role": "user",
                            "content": (
                                f"No has usado la llamada nativa de herramienta para «{textual_name}»: "
                                "escribiste el JSON como texto normal. Repite la operación usando "
                                "exclusivamente tool calling nativo, sin escribir nada de JSON en el mensaje."
                            ),
                        })
                        continue
                    final_text = (
                        f"No se pudo completar la operación: el modelo no logró invocar «{textual_name}» "
                        "mediante la llamada nativa de herramienta tras reintentarlo. Vuelve a intentarlo "
                        "o reformula la petición."
                    )
                    on_text(final_text)
                    return final_text
                if content:
                    final_text += content
                return final_text
            # El contenido de una ronda con tool_calls puede ser un preámbulo técnico.
            # No se muestra ni se acumula como respuesta final.
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
                        "ERROR: llamada de herramienta bloqueada: la última petición del usuario "
                        "no solicita esa operación sobre el workspace."
                    )
                else:
                    result_text = on_tool(name, arguments)
                history.append({"role": "tool", "content": result_text, "tool_name": name})
        raise OllamaError("Se alcanzó el límite de rondas de herramientas.")

    @staticmethod
    def _extract_definitions(tools: Any) -> list[dict[str, Any]] | None:
        """Devuelve una lista de definiciones de herramientas.

        Acepta tanto un ``ToolProvider`` (objeto con ``definitions()``)
        como una lista ya construida. Devuelve ``None`` si no hay nada.
        """
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
        """Construye un gate con las reglas declaradas por el provider.

        Si ``tools`` es un ``ToolProvider`` (tiene ``intent_rules()``), se
        usan sus reglas y se registran globalmente. Si es una lista de
        definiciones (modo retrocompatible para tests y usos puntuales),
        se usan las reglas ya registradas globalmente. Si no hay nada,
        el gate vacío bloquea cualquier tool call.
        """
        if tools is None:
            return ToolIntentGate({})
        rules_method = getattr(tools, "intent_rules", None)
        if callable(rules_method):
            rules = rules_method()
            ToolIntentGate.register_rules(rules)
            return ToolIntentGate(rules)
        # Modo lista: usa el registro global (que los providers han ido
        # dejando al construirse).
        return ToolIntentGate(dict(ToolIntentGate._RULES_REGISTRY))

    @staticmethod
    def _inject_system_prompts(
        history: list[dict[str, Any]],
        user_prompt: str,
        tool_prompt: str,
    ) -> None:
        """Compone el system prompt final a partir de hasta tres fuentes:

        1. Un system message ya existente en el historial (rara vez presente).
        2. El system prompt del agente activo (``user_prompt``).
        3. Las reglas de tool calling cuando hay herramientas activas.

        El orden importa: las reglas de tool calling van al final porque el
        modelo presta más atención a lo último que lee antes de la pregunta.
        """
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
        # Reemplaza el system message existente (si lo había) o inserta uno.
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
        names = ", ".join(tool_names)
        return (
            "Tienes acceso a herramientas reales, pero no forman parte de una conversación normal. "
            "No uses ninguna herramienta para responder preguntas generales, explicar conceptos, "
            "opinar o redactar texto. Úsala solo cuando la última petición del usuario pida de forma "
            "clara una operación sobre su workspace. Para crear, escribir o borrar un archivo, la "
            "petición debe solicitar expresamente esa acción y ese archivo. Nunca infieras una acción "
            "sobre archivos a partir de una pregunta informativa. Si usas una herramienta, espera su "
            "resultado antes de afirmar que realizaste la operación. No inventes resultados. "
            "Si el usuario pide crear un archivo pero no da nombre, elige un nombre de archivo razonable "
            "a partir del contenido solicitado y usa directamente la herramienta de creación/escritura; "
            "no pidas una confirmación en texto: la aplicación se encarga de la confirmación antes de ejecutar. "
            "Antes de usar una herramienta de escritura para modificar un archivo existente, usa primero "
            "la herramienta de lectura disponible para conocer su contenido actual completo. El contenido "
            "que envíes a la herramienta de escritura debe ser siempre el contenido final completo del archivo "
            "tras aplicar el cambio pedido, nunca una descripción de la instrucción del usuario ni un resumen. "
            f"Las herramientas disponibles son: {names}. "
            "Usa exclusivamente las llamadas de herramienta nativas proporcionadas por Ollama; no escribas "
            "comandos con prefijo $, llamadas de función, JSON de herramientas ni Markdown para simular una "
            "ejecución. Si no puedes hacer una llamada nativa, no afirmes que la herramienta se ha ejecutado. "
            "Las operaciones que escriben o borran datos requieren "
            "confirmación explícita del usuario en la aplicación."
        )

    @staticmethod
    def _last_user_text(history: list[dict[str, Any]]) -> str:
        for message in reversed(history):
            if message.get("role") == "user":
                return str(message.get("content") or "")
        return ""

    @staticmethod
    def _textual_tool_call_name(content: str, tool_names: set[str]) -> str | None:
        """Detecta si el texto (sin tool_calls nativos) es en realidad una llamada
        de herramienta escrita como JSON, en vez de usar el mecanismo nativo.

        La comprobación es deliberadamente laxa: basta con que el texto contenga
        ``"name": "<tool_conocida>"``. Algunos modelos omiten el campo
        ``parameters``/``arguments``, así que exigirlo dejaba pasar llamadas
        textuales sin detectar.
        """
        if "{" in content and '"name"' in content:
            match = OllamaClient._TEXTUAL_CALL_NAME.search(content)
            if match:
                name = match.group(1)
                if name in tool_names:
                    return name

        # Algunos modelos, incluso con tools habilitadas, pueden emitir una
        # pseudo-llamada tipo shell. Nunca la ejecutamos: solo la detectamos
        # para poder pedir al modelo que repita usando tool_calls nativos.
        shell_match = OllamaClient._TEXTUAL_SHELL_CALL.search(content)
        if shell_match:
            name = shell_match.group(1)
            if name in tool_names:
                return name
        return None

    def _stream(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: list[dict[str, Any]] | None,
        on_text: Callable[[str], None],
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
        try:
            with httpx.stream(
                "POST", f"{self.host}/api/chat", json=payload, timeout=self.timeout
            ) as response:
                response.raise_for_status()
                message: dict[str, Any] = {"role": "assistant", "content": ""}
                content_parts: list[str] = []
                for line in response.iter_lines():
                    self._check_cancel(cancel_event)
                    if not line:
                        continue
                    try:
                        data = json.loads(line)
                    except ValueError as exc:
                        raise OllamaError("Ollama devolvió una línea JSON inválida.") from exc
                    chunk = data.get("message") or {}
                    if chunk.get("content"):
                        content_parts.append(str(chunk["content"]))
                    if chunk.get("tool_calls"):
                        message.setdefault("tool_calls", []).extend(chunk["tool_calls"])
                    if data.get("done"):
                        break
                content = "".join(content_parts)
                if content and not message.get("tool_calls"):
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
        except httpx.HTTPError as exc:
            raise OllamaError(str(exc)) from exc

    @staticmethod
    def _check_cancel(cancel_event: threading.Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise OllamaCancelled("Operación cancelada por el usuario.")
