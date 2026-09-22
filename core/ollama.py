from __future__ import annotations

import asyncio
import codecs
import json
import logging
import re
import threading
from collections.abc import Iterable
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
from .context_window import ContextWindow, RequestTokenCache
from . import token_calibration
from .model_capabilities import get_capabilities
from .models_config import get_override
from .tool_strategies import (
    NativeToolStrategy,
    XmlToolStrategy,
    authorize_and_execute,
)
from .xml_tools import build_tools_prompt, parse_tool_calls, strip_tool_call_blocks


logger = logging.getLogger(__name__)


# Marcadores de fallo de tool calling textual. Se usan para avisar
# al usuario (DiagnosticsController) cuando el modelo intenta usar
# herramientas escribiendo JSON en el texto en lugar de emitir
# tool_calls nativos. Centralizados para que emisor y detector
# compartan la misma fuente de verdad.
_TEXTUAL_TOOL_FAILURE_MARKERS: tuple[str, ...] = (
    "no logró invocar la herramienta",
    "has escrito el json de la herramienta",
)


def is_textual_tool_failure(text: str | None) -> bool:
    """True si `text` proviene de un fallo de tool calling textual.

    Un fallo significa: el modelo intentó invocar una herramienta
    escribiendo JSON en el texto en vez de emitir un tool_call nativo.
    Se detecta por los mensajes que emite `chat()` en el camino de
    retry agotado.
    """
    if not text:
        return False
    lower = text.lower()
    return any(marker in lower for marker in _TEXTUAL_TOOL_FAILURE_MARKERS)


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
        # read=300s: modelos con thinking mode (qwen3, north-mini-code,
        # muse-glimmer) pueden tardar 60-120s en el primer token. Con
        # 60s, httpx cortaba la conexión antes de que el modelo empezara.
        self.timeout = httpx.Timeout(connect=10.0, read=300.0, write=30.0, pool=10.0)
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
        context_window: ContextWindow | None = None,
        on_metrics: Callable[[dict[str, int]], None] | None = None,
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
        # Copia superficial de la lista (no de cada dict). El bucle
        # nunca muta los dicts existentes: solo hace history.append
        # para añadir mensajes nuevos. Los dicts originales del
        # caller quedan intactos porque _fit_round_history devuelve
        # listas nuevas antes de enviarse al modelo.
        history = list(messages)
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
        consecutive_blocked_rounds = 0
        # Firmas de las últimas operaciones de tool para detectar
        # "sobre-trabajo": si el modelo repite la misma operación
        # (mismo nombre + mismos argumentos) dos veces seguidas, es
        # que ya terminó pero no lo reconoce. Se corta el bucle y se
        # responde con el último contenido visible.
        recent_tool_signatures: list[str] = []
        MAX_REPEATED_SIGNATURES = 2
        # Cache de costes de contexto: vive solo durante este chat().
        # Evita que fit() reestime los mismos mensajes en cada ronda
        # del bucle de tools.
        token_cache = (
            RequestTokenCache() if context_window is not None else None
        )

        for _ in range(max_rounds):
            self._check_cancel(cancel_event)

            # Ajustar el historial al presupuesto antes de cada ronda.
            # Los tool results intermedios pueden crecer mucho (diffs
            # grandes, búsquedas amplias) y sin este control el request
            # puede exceder num_ctx antes de que el bucle termine,
            # provocando truncados silenciosos o errores en Ollama.
            if context_window is not None:
                history = self._fit_round_history(
                    context_window,
                    history,
                    send_tools or [],
                    cache=token_cache,
                )

            message = self._stream(
                model,
                history,
                send_tools,
                None if buffer_only else on_text,
                cancel_event=cancel_event,
                options=options,
            )

            # Métricas reales de esta ronda. Se emiten como callback
            # para que el consumidor (ChatWorker) pueda propagarlas a
            # la UI. No afectan al flujo de tool calling ni al
            # procesamiento del mensaje.
            round_metrics = message.pop("_metrics", None)
            if round_metrics:
                try:
                    prompt_tokens = int(
                        round_metrics.get("prompt_eval_count", 0)
                    )
                    if prompt_tokens > 0:
                        # Serializar los mensajes completos (incluye
                        # tool_calls, roles, estructura) y las tool
                        # definitions. `prompt_eval_count` cuenta todo
                        # eso, así que medir solo el content daba un
                        # ratio sesgado a la baja.
                        messages_chars = sum(
                            len(json.dumps(
                                m,
                                ensure_ascii=False,
                                default=str,
                            ))
                            for m in history
                        )
                        tools_chars = 0
                        if send_tools:
                            try:
                                tools_chars = len(json.dumps(
                                    send_tools,
                                    ensure_ascii=False,
                                    default=str,
                                ))
                            except (TypeError, ValueError):
                                tools_chars = 0
                        total_chars = messages_chars + tools_chars
                        token_calibration.observe(
                            model,
                            chars=total_chars,
                            actual_tokens=prompt_tokens,
                        )
                except Exception:
                    pass
                if on_metrics is not None:
                    try:
                        on_metrics(round_metrics)
                    except Exception:
                        pass

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
                # El mensaje assistant del historial DEBE incluir los
                # tool_calls que emitió el modelo. Sin esto, el chat
                # template del modelo en Ollama ve un mensaje `tool`
                # huérfano en la siguiente ronda y el modelo vuelve a
                # llamar a la misma herramienta en bucle.
                history.append({
                    "role": "assistant",
                    "content": result.assistant_content,
                    "tool_calls": [
                        {
                            "function": {
                                "name": name,
                                "arguments": args,
                            }
                        }
                        for name, args in result.tool_calls
                    ],
                })
                round_had_block = False
                round_had_execution = False
                round_signature: str | None = None
                for name, args in result.tool_calls:
                    result_text = authorize_and_execute(
                        name, args, gate, authorization_text, on_tool
                    )
                    # authorize_and_execute devuelve el mensaje de
                    # bloqueo cuando el gate rechaza, o el resultado
                    # real cuando ejecuta. Distinguimos por prefijo.
                    if result_text.startswith("OPERACIÓN NO AUTORIZADA"):
                        round_had_block = True
                    else:
                        round_had_execution = True
                        # Firma estable: nombre + args ordenados.
                        try:
                            import json as _json
                            key = _json.dumps(
                                {"name": name, "args": args},
                                sort_keys=True,
                                ensure_ascii=False,
                                default=str,
                            )
                        except Exception:
                            key = f"{name}:{args!r}"
                        round_signature = key
                    history.append(strategy.format_tool_result(name, result_text))

                # Detección de "sin progreso": si el modelo repite la
                # misma operación con los mismos argumentos dos veces
                # seguidas, ya terminó pero no lo reconoce. Cortar y
                # devolver el último texto disponible.
                if round_signature is not None:
                    recent_tool_signatures.append(round_signature)
                    # Contar cuántas veces seguidas aparece la firma.
                    repeated = 0
                    for sig in reversed(recent_tool_signatures):
                        if sig == round_signature:
                            repeated += 1
                        else:
                            break
                    if repeated >= MAX_REPEATED_SIGNATURES:
                        msg = (
                            "El modelo completó la tarea y estaba "
                            "verificándola en bucle. Se detiene aquí: "
                            "revisa el resultado en el chat y, si falta "
                            "algo, reformula la petición."
                        )
                        on_text(msg)
                        return msg

                # Si toda la ronda fue bloqueada, contar. Si hubo
                # alguna ejecución, resetear el contador.
                if round_had_block and not round_had_execution:
                    consecutive_blocked_rounds += 1
                    if consecutive_blocked_rounds >= 3:
                        msg = (
                            "El modelo intentó varias veces una operación "
                            "que no está autorizada por tu petición. "
                            "Reformula el mensaje indicando el archivo "
                            "concreto donde quieres que se guarde el "
                            "resultado (por ejemplo: «crea el archivo "
                            "utils.py con la función generate_proxy»)."
                        )
                        on_text(msg)
                        return msg
                else:
                    consecutive_blocked_rounds = 0
                continue

            # Sin tool calls y sin ser final: caso raro (no debería
            # ocurrir con las estrategias actuales). Cerramos.
            return result.final_text

        raise OllamaError("Se alcanzó el límite de rondas de herramientas.")

    @staticmethod
    def _fit_round_history(
        context_window: ContextWindow,
        history: list[dict[str, Any]],
        tool_definitions: list[dict[str, Any]],
        cache: RequestTokenCache | None = None,
    ) -> list[dict[str, Any]]:
        """Ajusta el historial al presupuesto antes de enviarlo al modelo.

        Extrae el system message (si lo hay) y lo pasa como
        ``system_prompt`` al ContextWindow, para que no sea podado
        como si fuera un mensaje más. El resto del historial se poda
        con normalidad, contando también los ``tool_calls``.
        """
        system_msg: dict[str, Any] | None = None
        rest: list[dict[str, Any]] = []
        for m in history:
            if m.get("role") == "system" and system_msg is None:
                system_msg = m
            else:
                rest.append(m)

        system_content = (
            str(system_msg.get("content", "")) if system_msg else ""
        )

        pruned, _budget = context_window.fit(
            system_prompt=system_content,
            tool_definitions=tool_definitions,
            messages=rest,
            cache=cache,
        )

        if system_msg is not None:
            return [system_msg] + list(pruned)
        return list(pruned)

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
            # `defs` viene de un método del provider, tipado como Any.
            # Estrechamos con isinstance para que Pylance sepa que es
            # iterable antes de llamar a list(). Los strings también
            # son iterables, pero iterar sobre ellos daría caracteres
            # sueltos, así que los excluimos explícitamente.
            if not defs or isinstance(defs, (str, bytes)):
                return None
            if not isinstance(defs, Iterable):
                return None
            return list(defs)
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
            # Defensa: un provider roto podría devolver algo que no es
            # un dict. Caemos al registro global antes que romper.
            if not isinstance(rules, dict):
                return ToolIntentGate(dict(ToolIntentGate._RULES_REGISTRY))
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
        existing_idx: int | None = None
        for i, message in enumerate(history):
            if message.get("role") == "system":
                existing_idx = i
                existing = str(message.get("content", ""))
                if existing:
                    parts.append(existing)
                break
        if user_prompt.strip():
            parts.append(user_prompt.strip())
        if tool_prompt.strip():
            parts.append(tool_prompt.strip())

        if not parts:
            return

        combined = "\n\n".join(parts)
        # Reemplazamos el dict entero en lugar de mutarlo in-place.
        # `history` es una copia superficial de `messages`, así que
        # mutar el dict compartiría la mutación con el llamante. El
        # contrato de chat() dice que los mensajes de entrada quedan
        # intactos: esto lo garantiza.
        new_system = {"role": "system", "content": combined}
        if existing_idx is not None:
            history[existing_idx] = new_system
        else:
            history.insert(0, new_system)

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
            # httpx.ReadTimeout("") tiene str() vacío. Sin este fallback,
            # el usuario vería un "Error:" sin más y no sabría qué pasó.
            detail = str(exc) or type(exc).__name__
            if isinstance(exc, httpx.ReadTimeout):
                detail = (
                    "Ollama no respondió en el tiempo de espera. "
                    "El modelo puede necesitar más tiempo (thinking mode) "
                    "o estar demasiado cargado. Detalle: ReadTimeout."
                )
            raise OllamaError(detail) from exc
        except asyncio.CancelledError:
            raise OllamaCancelled("Operación cancelada por el usuario.")

    async def iter_ollama_events(
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
                    event = self.parse_ollama_line(
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
                event = self.parse_ollama_line(
                    buffer.strip(), message, content_parts
                )
                if event is not None:
                    yield event

        message.pop("_done", None)
        message["content"] = "".join(content_parts)
        # Extraer métricas reales (guardadas por parse_ollama_line) y
        # limpiar los campos temporales del mensaje.
        metrics: dict[str, int] = {}
        for key in list(message.keys()):
            if key.startswith("_metric_"):
                metrics[key[len("_metric_"):]] = message.pop(key)
        yield StreamFinished(message=message, metrics=metrics)

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
        """Wrapper sync-friendly sobre iter_ollama_events.

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
        # Override de thinking por modelo. Solo aplica si el usuario
        # lo ha definido en models.json. Si está ausente, Ollama decide
        # según la capability del modelo.
        override = get_override(model)
        if override.thinking is not None:
            payload["think"] = override.thinking

        message: dict[str, Any] = {}
        metrics: dict[str, int] = {}
        buffering_textual = False

        async for event in self.iter_ollama_events(
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
                metrics = event.metrics

        content = message.get("content", "")
        textual_name: str | None = None
        if content and not message.get("tool_calls"):
            tool_names = {
                str(item.get("function", {}).get("name", ""))
                for item in (tools or [])
                if item.get("function", {}).get("name")
            }
            if tool_names:
                # Dialecto XML <function=NAME>: se parsea como si fuera
                # un tool_call nativo. Lo emiten modelos como
                # qwen3-coder cuando ignoran el tool calling de Ollama.
                #
                # Importante: poblamos `tool_calls` en el propio mensaje
                # (con el formato que espera Ollama) y limpiamos el XML
                # del `content`. Sin esto, el chat template de Qwen3 no
                # reconoce la tool call cuando reenviamos el historial,
                # y el modelo vuelve a intentar la misma llamada en
                # bucle hasta agotar max_rounds.
                from .xml_tools import (
                    parse_function_xml,
                    strip_tool_call_blocks,
                )
                xml_calls = parse_function_xml(content, known_tools=tool_names)
                if xml_calls:
                    message["_textual_xml_calls"] = xml_calls
                    message.setdefault("tool_calls", []).extend(
                        {
                            "function": {
                                "name": name,
                                "arguments": args,
                            }
                        }
                        for name, args in xml_calls
                    )
                    message["content"] = strip_tool_call_blocks(content)
                else:
                    textual_name = self._textual_tool_call_name(
                        content, tool_names
                    )
                    if textual_name:
                        message["_textual_tool_name"] = textual_name

        # Si activamos buffering pero NO resulto ser tool call, el
        # usuario no ha visto nada: emitimos el texto completo.
        if buffering_textual and on_text is not None and not textual_name:
            on_text(content)

        # Adjuntar las métricas reales al mensaje para que chat() las
        # pueda propagar (via callback on_metrics) y la UI las muestre.
        if metrics:
            message["_metrics"] = metrics

        return message

    @staticmethod
    def parse_ollama_line(
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
            # Métricas reales que Ollama envía en el chunk final.
            # Se guardan con prefijo `_metric_` para no colisionar con
            # campos del mensaje; se extraen y limpian en
            # iter_ollama_events al emitir StreamFinished.
            for key in (
                "prompt_eval_count",
                "prompt_eval_duration",
                "eval_count",
                "eval_duration",
                "total_duration",
                "load_duration",
            ):
                value = data.get(key)
                if isinstance(value, (int, float)) and not isinstance(value, bool):
                    message[f"_metric_{key}"] = int(value)

        if delta is not None:
            return TextDelta(delta)
        if calls_raw:
            return ToolCallsDelta(calls=tuple(calls_raw))
        return None

    # _handle_line fue eliminado: duplicaba parse_ollama_line sin emitir
    # tool_calls, y no se invocaba desde ningún sitio. Si en el futuro se
    # necesita otra ruta de parsing, extender parse_ollama_line, no crear
    # un método paralelo.

    @staticmethod
    def _check_cancel(cancel_event: threading.Event | None) -> None:
        if cancel_event is not None and cancel_event.is_set():
            raise OllamaCancelled("Operación cancelada por el usuario.")
