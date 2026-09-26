from __future__ import annotations

import asyncio
import codecs
import json
import logging
import re
import shlex
import threading
from collections.abc import Iterable, Iterator
from dataclasses import dataclass, field
from typing import Any, AsyncIterator, Callable

import httpx

from .async_runner import AsyncRunner, _CancelledByEvent
from .stream_events import (
    StreamEvent,
    StreamFinished,
    TextDelta,
    ToolCallsDelta,
)
from .intent import ToolIntentGate, _cached_verb_forms
from .context_window import ContextWindow, RequestTokenCache
from . import token_calibration
from .model_capabilities import get_capabilities
from .models_config import get_override
from .tool_strategies import (
    RoundResult,
    NativeToolStrategy,
    XmlToolStrategy,
    authorize_and_execute,
)


logger = logging.getLogger(__name__)


_RETRY_EXHAUSTED_MSG = (
    "No se pudo completar la operación: el modelo no logró "
    "invocar la herramienta mediante la llamada nativa tras "
    "reintentarlo. Reformula la petición."
)

_LOOP_REPEATED_MSG = (
    "El modelo completó la tarea y estaba verificándola en bucle. "
    "Se detiene aquí: revisa el resultado en el chat y, si falta "
    "algo, reformula la petición."
)

_BLOCKED_ROUNDS_MSG = (
    "El modelo intentó varias veces una operación que no está "
    "autorizada por tu petición. Reformula el mensaje indicando "
    "el archivo concreto donde quieres que se guarde el resultado."
)

_MAX_REPEATED_SIGNATURES = 2
_MAX_CONSECUTIVE_BLOCKED_ROUNDS = 3

# Keywords que aparecen en el JSON de una tool call textual. Se usan
# para decidir si el buffering activado por un `{` es realmente una
# tool call o simplemente código/JSON en prosa.
_TOOL_CALL_KEYWORDS: tuple[str, ...] = (
    '"function"',
    '"name"',
    '"parameters"',
    '"arguments"',
    '"tool_call"',
)
# Safety net: si tras este número de caracteres buffereados no se ha
# visto ninguna keyword ni un cierre de JSON, se cancela el buffering.
_MAX_PEEK_CHARS = 200


def _in_code_fence(text: str) -> bool:
    """True si `text` termina dentro de un code-fence markdown.

    Cuenta los triple-backtick: numero impar = fence abierto.
    Se usa para evitar activar el buffering anti-tool-call cuando
    el `{`/`[` esta en un bloque de codigo del propio mensaje
    (H5 auditoria 2026-09-26).
    """
    return text.count("```") % 2 == 1


def _advance_fence_state(current: bool, delta: str) -> bool:
    """Actualiza el estado de fence contando ``` en `delta`.

    O(len(delta)), no O(texto acumulado). Sustituye la reconstruccion
    `_in_code_fence("".join(emitted_so_far))` que era O(n²) en
    respuestas largas con muchos `{` (auditoria 2026-09-26).

    Limitacion conocida: si un ``` se parte entre dos deltas (raro
    con deltas de 4-5+ chars), el toggle se pierde. El peor caso es
    activar/desactivar buffering una vez de mas, autocorregible.
    """
    if not delta:
        return current
    fence_count = delta.count("```")
    if fence_count % 2 == 1:
        return not current
    return current


@dataclass
class _ChatContext:
    """Estado constante durante el bucle de rondas de chat().

    Los campos "Recibidos" los pasa chat() y no cambian. Los campos
    "Preparados" los produce `_prepare_context`. Solo `history` se
    reasigna en cada ronda (fit de contexto).
    """
    # -- Recibidos del llamante ------------------------------------
    model: str
    options: dict[str, Any] | None
    on_text: Callable[[str], None]
    on_tool: Callable[[str, dict[str, Any]], str]
    on_metrics: Callable[[dict[str, int]], None] | None
    cancel_event: threading.Event | None
    context_window: ContextWindow | None

    # -- Preparados por _prepare_context ---------------------------
    strategy: Any
    history: list[dict[str, Any]]
    authorization_text: str
    # Ultimo assistant message ANTES del ultimo user. Se usa para
    # autorizar confirmaciones conversacionales (P1 auditoria).
    last_assistant: str
    gate: ToolIntentGate
    tool_names: set[str]
    send_tools: list[dict[str, Any]] | None
    buffer_only: bool
    # Diagnostico: se construye solo con DEBUG activo.
    snapshot: Any = None


@dataclass
class _RoundExecution:
    """Resumen de una ronda con tool calls."""
    round_signature: str | None
    had_block: bool
    had_execution: bool
    had_write: bool = False
    had_failure: bool = False


@dataclass
class _LoopState:
    """Estado mutable del bucle de chat().

    Persiste a lo largo de las rondas del mismo chat():
      · textual_retry_used — retry de tool-call textual ya usado.
      · consecutive_blocked_rounds — rondas seguidas bloqueadas.
      · recent_tool_signatures — firmas de las ultimas tools.
      · token_cache — cache de costes durante este chat().
    """
    textual_retry_used: bool = False
    consecutive_blocked_rounds: int = 0
    recent_tool_signatures: list[str] = field(default_factory=list)
    token_cache: RequestTokenCache | None = None
    # Stall guard: contador de nudges ya inyectados y bandera de si
    # el modelo emitio alguna tool call en este chat().
    stall_retries_used: int = 0
    any_tool_call_emitted: bool = False
    # Falso completado: peticion de escritura sin escribir_archivo.
    false_completion_retries_used: int = 0
    any_write_executed: bool = False
    any_tool_failed: bool = False



# Marcadores de fallo de tool calling textual. Se usan para avisar
# al usuario (DiagnosticsController) cuando el modelo intenta usar
# herramientas escribiendo JSON en el texto en lugar de emitir
# tool_calls nativos. Centralizados para que emisor y detector
# compartan la misma fuente de verdad.
_TEXTUAL_TOOL_FAILURE_MARKERS: tuple[str, ...] = (
    "no logró invocar la herramienta",
    "has escrito el json de la herramienta",
)


# Frases que indican que el usuario pidio una verificacion
# explicita. Si el modelo responde sin tool calls y el usuario
# pidio verificar, se activa el stall guard: inyectar un nudge
# y repetir la ronda.
_VERIFICATION_VERBS: tuple[str, ...] = (
    "ejecuta", "ejecutar", "verifica", "verificar",
    "comprueba", "comprobar", "cita", "citar",
    "run", "execute", "verify", "check",
    "py_compile",
)

_STALL_NUDGE_MESSAGE = (
    "REGLA DE HIERRO: has respondido sin emitir ninguna tool call. "
    "El usuario pidio una verificacion explicita. NO puedes cerrar "
    "la fase sin ejecutar el comando. Tu siguiente mensaje DEBE "
    "contener una llamada a la herramienta ejecutar_comando. No "
    "respondas con texto hasta haber recibido el output del comando."
)

# Maximo de reintentos tras detectar un stall. Con 1 basta: si el
# modelo ignora el nudge una vez, no lo va a obedecer a la segunda.
_MAX_STALL_RETRIES = 1


# Tools que consideramos "escritura real". Si una de estas
# devuelve exito en el turno, el modelo ha hecho su trabajo.
# Limites del truncado selectivo de tool results largos.
# 100 lineas = ~60 tokens de contenido + marcador, suficiente
# para dar contexto sin inflar el prefill. Si el modelo necesita
# mas, puede volver a leer con start_line/end_line.
_TOOL_RESULT_MAX_LINES = 100
_TOOL_RESULT_KEEP_HEAD = 60
_TOOL_RESULT_KEEP_TAIL = 20


# Limite de tamano del thinking reenviado al modelo entre rondas
# de tool calling. ~4k chars aprox ~1k tokens. Sin esto, un tool
# loop de 5+ rondas acumula 15-25k tokens de thinking en el
# payload (Hueco 5, 2026-09-26).
_THINKING_MAX_CHARS = 4000
_THINKING_KEEP_HEAD = 2500
_THINKING_KEEP_TAIL = 1200


def _shrink_thinking(text: str | None) -> str:
    """Trunca un thinking largo preservando head + tail.

    El thinking de modelos de razonamiento se reenvia al modelo
    en el assistant message de la ronda siguiente. Si es muy
    largo (gpt-oss en nivel high), se acumula y dispara el
    prefill. Se conserva el final (suele ser la conclusion util)
    y el inicio (contexto del razonamiento).
    """
    if not text:
        return ""
    if len(text) <= _THINKING_MAX_CHARS:
        return text
    head = text[:_THINKING_KEEP_HEAD]
    tail = text[-_THINKING_KEEP_TAIL:]
    omitted = len(text) - _THINKING_KEEP_HEAD - _THINKING_KEEP_TAIL
    marker = (
        f"\n\n[... {omitted} caracteres de razonamiento omitidos "
        "por tamano ...]\n\n"
    )
    return head + marker + tail


def _shrink_tool_result_content(text: str) -> str:
    """Trunca un tool result largo preservando head + tail.

    Devuelve el texto original si ya es corto. Si no, conserva
    las primeras _TOOL_RESULT_KEEP_HEAD lineas y las ultimas
    _TOOL_RESULT_KEEP_TAIL, con un marcador en medio.
    """
    if not text:
        return text
    lines = text.splitlines(keepends=True)
    total = len(lines)
    if total <= _TOOL_RESULT_MAX_LINES:
        return text
    head = lines[:_TOOL_RESULT_KEEP_HEAD]
    tail = lines[-_TOOL_RESULT_KEEP_TAIL:]
    omitted = total - _TOOL_RESULT_KEEP_HEAD - _TOOL_RESULT_KEEP_TAIL
    # head[-1] ya termina en \n: no hace falta prefijo en el
    # marcador, y añadirlo crea una linea vacia intermedia.
    marker = (
        f"[... {omitted} lineas omitidas por tamano. "
        "Si necesitas el contenido completo, vuelve a leerlo "
        "con start_line/end_line. ...]\n"
    )
    return "".join(head) + marker + "".join(tail)


def _shrink_tool_results(
    messages: list[dict[str, Any]],
) -> list[dict[str, Any]]:
    """Devuelve copia de `messages` con tool results largos truncados.

    Aplica a role="tool" (modo nativo) o a role="user" cuyo
    content empieza por ``[TOOL_RESULT:`` (modo XML). NO toca
    assistant ni user reales.
    """
    out: list[dict[str, Any]] = []
    for m in messages:
        content = m.get("content")
        if not isinstance(content, str):
            out.append(m)
            continue
        role = m.get("role")
        is_tool = role == "tool" or (
            role == "user" and content.startswith("[TOOL_RESULT:")
        )
        if not is_tool:
            out.append(m)
            continue
        shrunk = _shrink_tool_result_content(content)
        if shrunk == content:
            out.append(m)
        else:
            new = dict(m)
            new["content"] = shrunk
            out.append(new)
    return out


_WRITE_TOOLS: frozenset[str] = frozenset({
    "escribir_archivo",
    "editar_archivo",
    "insertar_en_archivo",
    "crear_archivo",
    "crear_carpeta",
})

# Verbos que indican que el usuario pidio escribir o modificar.
_WRITE_VERBS: tuple[str, ...] = (
    "crea", "crear", "escribe", "escribir",
    "modifica", "modificar", "anade", "anadir",
    "agrega", "agregar", "implementa", "implementar",
    "genera", "generar", "refactoriza", "refactorizar",
    "actualiza", "actualizar", "edita", "editar",
    "write", "create", "edit", "update", "add",
)

# Marcadores en el texto final que sugieren que el modelo cree
# haber terminado. Si aparece uno sin haber escrito, es falso
# completado.
_FALSE_COMPLETION_MARKERS: tuple[str, ...] = (
    "verificada", "completado", "completada", "terminado",
    "terminada", "listo", "lista para usar", "hecho",
    "done", "completed", "finished", "ready",
    "todo listo", "fase completada", "fase verificada",
)

# Ampliacion de vocabulario (H1 auditoria 2026-09-26).
# Los tests demuestran que estas variantes se pierden hoy.
# Verbos imperativos de colocar-algo que mistral-small3.2 usa
# sin disparar el gate de escritura (bug 2026-09-26).
_WRITE_VERBS = _WRITE_VERBS + (
    "pon", "poner", "ponme",
    "inserta", "insertar", "insertame",
    "mete", "meter",
)
_WRITE_VERBS = _WRITE_VERBS + (
    "edicion", "ediciones",
    "correccion", "correcciones",
    "reparacion", "reparaciones",
    "modificacion", "modificaciones",
    "creacion", "creaciones",
    "actualizacion", "actualizaciones",
    "implementacion", "implementaciones",
    "corrige", "corregir", "arregla", "arreglar",
    "soluciona", "solucionar", "refactoriza", "refactorizar",
)
_VERIFICATION_VERBS = _VERIFICATION_VERBS + (
    "corre", "correr", "lanza", "lanzar",
    "compila", "compilar", "testea", "testear",
    "pasa", "pasar",
)
_FALSE_COMPLETION_MARKERS = _FALSE_COMPLETION_MARKERS + (
    "resuelto", "solucionado", "arreglado", "corregido",
    "ya esta", "funciona", "funcionando", "operativo",
    "acabado",
)

# Frases que un modelo puede usar para declarar una escritura
# inexistente. Caso real: mistral-small3.2 dijo "se ha escrito
# correctamente" tras emitir solo texto CLI (2026-09-26).
_FALSE_COMPLETION_MARKERS = _FALSE_COMPLETION_MARKERS + (
    "se ha escrito", "he escrito",
    "se ha guardado", "he guardado",
    "se ha modificado", "he modificado",
    "se ha creado", "he creado",
    "se ha actualizado", "he actualizado",
    "se ha corregido", "he corregido",
    "archivo escrito", "archivo creado",
    "archivo modificado", "archivo guardado",
)

_FALSE_COMPLETION_NUDGE = (
    "REGLA DE HIERRO: has declarado la tarea como completada "
    "o verificada, pero NO has escrito ningun archivo en este "
    "turno. Si el usuario pidio crear o modificar un archivo, "
    "DEBES usar escribir_archivo (o crear_archivo) con el "
    "contenido FINAL COMPLETO antes de cerrar la tarea. No "
    "declares exito sin haber recibido el resultado de la "
    "escritura."
)

# Maximo de nudges por falso completado. Uno basta.
_MAX_FALSE_COMPLETION_RETRIES = 1

# Palabras interrogativas en espanol. Solo con tilde: sin ella,
# "como"/"que" aparecen en frases declarativas y disparan
# falsos positivos. La lista es CORTA y estable (las palabras
# interrogativas de un idioma no crecen).
_QUESTION_MARKERS: tuple[str, ...] = (
    "cómo", "qué", "cuál", "cuáles",
    "cuándo", "dónde", "quién", "quiénes",
    "por qué", "cuánto", "cuánta", "cuántos", "cuántas",
)


# Mensaje cuando el stream se corto antes de done=true (EOF, red,
# timeout sin cierre limpio). El texto parcial ya se mostro al
# usuario via on_text, pero NO debe guardarse como respuesta del
# asistente en el historial.
_INTERRUPTED_STREAM_MSG = (
    "La conexion con Ollama se cerro antes de que el modelo "
    "terminara de responder. El texto parcial no se ha guardado "
    "en el historial. Reintenta la peticion."
)

# Sufijo visible cuando Ollama corta la generacion por num_predict.
# La respuesta es valida pero incompleta por limite de tokens.
_TRUNCATED_STREAM_SUFFIX = (
    "\n\n[Respuesta truncada: se alcanzo el limite de tokens "
    "de generacion (num_predict). Aumenta el limite o divide "
    "la tarea.]"
)



# ── Helpers de matching normalizado (H1) ──────────────────────

_MENTIONS_PATTERN_CACHE: dict[tuple[str, ...], "re.Pattern[str]"] = {}


def _mentions_pattern(verbs: tuple[str, ...]) -> "re.Pattern[str]":
    """Regex (word-boundary) que matchea cualquier forma conjugada.

    Cachea el patron por tupla: mismo conjunto de verbos =
    mismo regex. Reutiliza _cached_verb_forms de intent.py.
    """
    pat = _MENTIONS_PATTERN_CACHE.get(verbs)
    if pat is not None:
        return pat
    forms: set[str] = set()
    for v in verbs:
        norm = ToolIntentGate._normalise(v).lower()
        conjugated = _cached_verb_forms(norm)
        forms.update(conjugated)
        # Encliticos: "escribe" + "lo" -> "escribelo".
        # Solo aplica a formas que terminan en vocal (imperativos).
        for form in conjugated:
            if form and form[-1] in "aeiouáéíóú":
                for suf in (
                    "lo", "la", "los", "las",
                    "le", "les", "me", "te", "se", "nos",
                ):
                    forms.add(form + suf)
    alt = "|".join(
        re.escape(f) for f in sorted(forms, key=len, reverse=True)
    )
    pat = re.compile(rf"(?<!\w)(?:{alt})(?!\w)")
    _MENTIONS_PATTERN_CACHE[verbs] = pat
    return pat


def _mentions_any(text: str | None, verbs: tuple[str, ...]) -> bool:
    """True si el texto menciona alguna forma de algun verbo."""
    if not text:
        return False
    norm = ToolIntentGate._normalise(text).lower()
    return bool(_mentions_pattern(verbs).search(norm))


def _mentions_marker(text: str | None, markers: tuple[str, ...]) -> bool:
    """True si el texto contiene algun marcador (normalizado)."""
    if not text:
        return False
    norm = ToolIntentGate._normalise(text).lower()
    for m in markers:
        if ToolIntentGate._normalise(m).lower() in norm:
            return True
    return False


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
        self.timeout = httpx.Timeout(connect=10.0, read=900.0, write=30.0, pool=10.0)
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

    def shutdown(self, timeout: float | None = None) -> bool:
        """Libera recursos del cliente. Llamar al cerrar la app.

        `timeout` es el presupuesto total para el cierre. Devuelve
        True si cerro limpiamente.
        """
        return self._async_runner.close(timeout)

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
        # 15 en lugar de 8: tareas multi-modulo necesitan leer
        # varios archivos antes de escribir el orquestador. Con 8
        # el modelo se queda sin rondas antes de empezar.
        max_rounds: int = 15,
        cancel_event: threading.Event | None = None,
        options: dict[str, Any] | None = None,
        system_prompt: str = "",
        context_window: ContextWindow | None = None,
        on_metrics: Callable[[dict[str, int]], None] | None = None,
    ) -> str:
        # Fase "una vez por chat": capabilities, strategy, gate,
        # system prompt, filtro de tools. Ver `_prepare_context`.
        ctx = self._prepare_context(
            model,
            messages,
            tools,
            on_text=on_text,
            on_tool=on_tool,
            on_metrics=on_metrics,
            cancel_event=cancel_event,
            options=options,
            system_prompt=system_prompt,
            context_window=context_window,
        )

        # Aliases locales. El cuerpo del bucle los usa tal cual.
        history = ctx.history
        strategy = ctx.strategy
        send_tools = ctx.send_tools
        buffer_only = ctx.buffer_only
        tool_names = ctx.tool_names

        # 5. Bucle de rondas delegando en la estrategia.
        # Estado mutable del bucle (retry, firmas, contador de
        # bloqueos, cache de tokens). Ver _LoopState.
        state = _LoopState(
            token_cache=(
                RequestTokenCache() if context_window is not None else None
            ),
        )

        # Alinear Ollama con el presupuesto que la app ya calcula.
        # Si el agente fijo num_ctx explicitamente, ese valor gana:
        # no lo pisamos. Sin esto, Ollama usa su default (4k-32k
        # segun VRAM) mientras la app poda como si fuera otro
        # tamano. Fix derivado de la investigacion 2026-09-26.
        effective_options = options
        if (
            context_window is not None
            and context_window.limit_tokens > 0
        ):
            # Tope de 32k para no forzar KV cache gigante en modelos
            # con context_length alto (gpt-oss: 131072 -> ~26 GB KV).
            # Sin este tope, Ollama aborta el prefill en equipos con
            # poca RAM unificada.
            _NUM_CTX_SAFE_MAX = 32_768
            num_ctx_value = min(
                context_window.limit_tokens, _NUM_CTX_SAFE_MAX
            )
            if options is None:
                effective_options = {"num_ctx": num_ctx_value}
            elif "num_ctx" not in options:
                effective_options = dict(options)
                effective_options["num_ctx"] = num_ctx_value

        for _ in range(max_rounds):
            # Diagnostico: loggear el snapshot si DEBUG.
            if ctx.snapshot is not None:
                ctx.snapshot.round_number += 1
                logger.debug(ctx.snapshot.to_log())
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
                    cache=state.token_cache,
                )
                ctx.history = history

            message = self._stream(
                model,
                history,
                send_tools,
                None if buffer_only else on_text,
                cancel_event=cancel_event,
                options=effective_options,
            )

            self._emit_round_metrics(ctx, message, state.token_cache)

            result = strategy.process_round(message, tool_names)
            # Extraer estado de finalizacion del mensaje. Los
            # anade _stream_async al consumir StreamFinished.
            result.completed = bool(message.pop("_stream_completed", True))
            result.done_reason = message.pop("_stream_done_reason", None)
            # Preservar thinking del modelo para la siguiente ronda de
            # tool calling. Las estrategias no lo leen; se propaga aquí
            # para no duplicar el código en cada una.
            if not result.assistant_thinking:
                thinking = message.get("thinking")
                if isinstance(thinking, str) and thinking:
                    result.assistant_thinking = thinking

            # Reintento por tool calling textual (solo nativo).
            if result.retry_requested:
                if not state.textual_retry_used:
                    state.textual_retry_used = True
                    history.append({
                        "role": "assistant",
                        "content": result.assistant_content,
                    })
                    history.append({"role": "user", "content": result.retry_message})
                    continue
                on_text(_RETRY_EXHAUSTED_MSG)
                return _RETRY_EXHAUSTED_MSG

            # Mostrar al usuario el texto visible (sin bloques XML).
            if result.visible_text:
                on_text(result.visible_text)

            # H1: respuesta final sin done=true no es valida.
            if result.is_final and not result.completed:
                logger.warning(
                    "Stream interrumpido sin done=true "
                    "(modelo %s). No se persiste la respuesta.",
                    ctx.model,
                )
                raise OllamaError(_INTERRUPTED_STREAM_MSG)
            # H2: truncamiento por num_predict. La respuesta es
            # valida pero incompleta. Se marca visualmente.
            if result.is_final and result.done_reason == "length":
                logger.info(
                    "Respuesta truncada por num_predict (modelo %s).",
                    ctx.model,
                )
                result.final_text = (
                    result.final_text + _TRUNCATED_STREAM_SUFFIX
                )

            # Respuesta final.
            if result.is_final:
                # Falso completado: el usuario pidio escribir o
                # modificar, el modelo emitio alguna tool call
                # pero ninguna de escritura fue exitosa, y el
                # texto final declara exito. Nudge especifico.
                # Fase 1 (2026-09-26): nudge por evidencia, no por
                # palabras. Basta con (a) que el usuario pida una
                # escritura, (b) que NO sea una pregunta
                # informativa, (c) que el assistant cierre el turno
                # sin preguntar al usuario, y (d) que NADA se haya
                # escrito en este turno. Cualquier texto final vale:
                # no dependemos de mantener listas de frases de
                # exito ("se ha escrito", "he completado"...).
                if (
                    state.false_completion_retries_used
                    < _MAX_FALSE_COMPLETION_RETRIES
                    and not state.any_write_executed
                    and ctx.tool_names
                    and bool(_WRITE_TOOLS & ctx.tool_names)
                    and self._user_requested_write(
                        self._effective_auth_text(ctx)
                    )
                    and not self._user_asked_question(
                        ctx.authorization_text
                    )
                    and self._assistant_closes_turn(
                        result.final_text
                    )
                ):
                    state.false_completion_retries_used += 1
                    logger.info(
                        "Falso completado (modelo %s): peticion "
                        "de escritura sin escribir_archivo. "
                        "Inyectando nudge y repitiendo ronda.",
                        ctx.model,
                    )
                    ctx.history.append({
                        "role": "user",
                        "content": _FALSE_COMPLETION_NUDGE,
                    })
                    continue
                # Stall guard: si el usuario pidio verificacion
                # explicita y el modelo NO ha emitido ninguna tool
                # call en todo el chat(), no aceptamos la respuesta
                # como "verificada". Inyectamos un nudge y repetimos
                # la ronda (una sola vez).
                if (
                    state.stall_retries_used < _MAX_STALL_RETRIES
                    and not state.any_tool_call_emitted
                    and ctx.tool_names
                    and self._user_requested_verification(
                        ctx.authorization_text
                    )
                ):
                    state.stall_retries_used += 1
                    logger.info(
                        "Stall detectado (modelo %s): respuesta sin "
                        "tool calls tras peticion de verificacion. "
                        "Inyectando nudge y repitiendo ronda.",
                        ctx.model,
                    )
                    ctx.history.append({
                        "role": "user",
                        "content": _STALL_NUDGE_MESSAGE,
                    })
                    continue
                # Si ya reintentamos con el nudge y sigue sin
                # ejecutar, abortamos la fase con un mensaje claro.
                #
                # Antes forzabamos XmlToolStrategy aqui, pero en la
                # practica el modelo pierde el habito de llamar a
                # escribir_archivo en modo XML: solo emite la tool mas
                # obvia del prompt (el comando de verificacion) y
                # responde texto con "FASE VERIFICADA" sin hacer el
                # trabajo. Mejor avisar al usuario que enmascarar.
                if (
                    state.stall_retries_used >= _MAX_STALL_RETRIES
                    and not state.any_tool_call_emitted
                ):
                    logger.warning(
                        "Modelo %s no ejecuta tool calls ni tras "
                        "nudge. Abortando fase.",
                        ctx.model,
                    )
                    msg = (
                        "El modelo no ejecuto ninguna herramienta "
                        "aunque se le pidio explicitamente. No se ha "
                        "aplicado ningun cambio. Prueba con otro "
                        "modelo (qwen3-coder:30b tiene problemas "
                        "conocidos en tareas de edicion largas) o "
                        "reformula la peticion pidiendo una sola "
                        "accion concreta."
                    )
                    ctx.on_text(msg)
                    return msg
                return result.final_text

            # H5: si el stream se interrumpio, NO ejecutar tools
            # parciales. Los tool_calls pueden estar incompletos.
            if result.tool_calls and not result.completed:
                logger.warning(
                    "Stream interrumpido con tool_calls pendientes "
                    "(modelo %s). No se ejecutan.",
                    ctx.model,
                )
                raise OllamaError(_INTERRUPTED_STREAM_MSG)
            # Hay tool calls: ejecutar y volver a la siguiente ronda.
            if result.tool_calls:
                # Ejecutar las tool calls. El mensaje assistant con
                # tool_calls se appendea dentro del metodo para que el
                # chat template del modelo entienda la cadena.
                execution = self._execute_round_tools(ctx, result)
                state.any_tool_call_emitted = True
                if execution.had_write:
                    state.any_write_executed = True
                if execution.had_failure:
                    state.any_tool_failed = True

                stop = self._evaluate_round(ctx, execution, state)
                if stop is not None:
                    on_text(stop)
                    return stop
                continue

            # Sin tool calls y sin ser final: caso raro (no debería
            # ocurrir con las estrategias actuales). Cerramos.
            return result.final_text

        raise OllamaError("Se alcanzó el límite de rondas de herramientas.")

    def _prepare_context(
        self,
        model: str,
        messages: list[dict[str, Any]],
        tools: Any,
        *,
        on_text: Callable[[str], None],
        on_tool: Callable[[str, dict[str, Any]], str],
        on_metrics: Callable[[dict[str, int]], None] | None,
        cancel_event: threading.Event | None,
        options: dict[str, Any] | None,
        system_prompt: str,
        context_window: ContextWindow | None,
    ) -> _ChatContext:
        """Prepara el contexto de un chat().

        Ejecuta la fase "una sola vez por chat": capabilities,
        eleccion de estrategia, filtrado de tools, inyeccion del
        system prompt. El bucle de rondas reutiliza el contexto.
        """
        if not model:
            raise OllamaError("No hay un modelo seleccionado.")

        caps = get_capabilities(self.host, model)
        logger.debug(
            "Modelo %s: tool_mode=%s (probed=%s)",
            model, caps.tool_mode, caps.probed,
        )

        strategy = self._choose_strategy(caps, tools, model)

        history = list(messages)

        # Si vamos en modo XML, normalizar el historial: no enviamos
        # `tools` en el payload, asi que los tool_calls nativos de
        # rondas anteriores rompen el chat template del modelo (500).
        if isinstance(strategy, XmlToolStrategy):
            history = self._strip_native_tool_calls(history)

        definitions = self._extract_definitions(tools)
        authorization_text = self._last_user_text(history)
        last_assistant = self._last_assistant_before_last_user(history)
        gate = self._build_intent_gate(tools)
        active_tools = gate.tools_for_request(
            definitions, authorization_text
        )
        tool_names = {
            str(item.get("function", {}).get("name", ""))
            for item in (active_tools or [])
            if item.get("function", {}).get("name")
        }

        tool_prompt = (
            strategy.prepare_system_prompt(active_tools)
            if active_tools
            else ""
        )
        self._inject_system_prompts(history, system_prompt, tool_prompt)

        send_tools = (
            active_tools if strategy.should_send_tools_param() else None
        )
        buffer_only = strategy.needs_full_buffer(bool(active_tools))

        # Snapshot de diagnostico. Solo se construye si el logger
        # tiene DEBUG activo: en produccion es None y no cuesta
        # nada. Agrupa lo que el modelo va a recibir.
        snapshot: Any = None
        if logger.isEnabledFor(logging.DEBUG):
            from .request_snapshot import RequestSnapshot
            sys_content = ""
            for m in history:
                if m.get("role") == "system":
                    sys_content = str(m.get("content", ""))
                    break
            snapshot = RequestSnapshot(
                model=model,
                system_prompt_chars=len(sys_content),
                system_prompt_preview=sys_content[:200],
                history_messages=len(history),
                active_tools=sorted(tool_names),
                options=options,
                thinking_override=get_override(model).thinking,
            )

        return _ChatContext(
            model=model,
            options=options,
            on_text=on_text,
            on_tool=on_tool,
            on_metrics=on_metrics,
            cancel_event=cancel_event,
            context_window=context_window,
            strategy=strategy,
            history=history,
            authorization_text=authorization_text,
            last_assistant=last_assistant,
            gate=gate,
            tool_names=tool_names,
            send_tools=send_tools,
            buffer_only=buffer_only,
            snapshot=snapshot,
        )

    @staticmethod
    def _emit_round_metrics(
        ctx: _ChatContext,
        message: dict[str, Any],
        cache: RequestTokenCache | None = None,
    ) -> None:
        """Extrae y emite las métricas de una ronda.

        Pop del `_metrics` del mensaje. Si hay métricas reales de
        Ollama, alimenta el calibrador EWMA (chars/token) y llama a
        `on_metrics`. Todo envuelto en try/except porque un fallo de
        métricas nunca debe romper la generación.
        """
        round_metrics = message.pop("_metrics", None)
        if not round_metrics:
            return
        try:
            prompt_tokens = int(
                round_metrics.get("prompt_eval_count", 0)
            )
            if prompt_tokens > 0:
                # Serializar los mensajes completos (incluye
                # tool_calls, roles, estructura) y las tool
                # definitions. `prompt_eval_count` cuenta todo eso,
                # así que medir solo el content daba un ratio sesgado
                # a la baja.
                # Reutilizar el cache de caracteres serializados si
                # esta disponible: sin esto, cada ronda reserializa
                # TODO el historial, aunque la mayoria de mensajes
                # no cambiaron desde la ronda anterior.
                if cache is not None:
                    messages_chars = sum(
                        cache.get_or_compute_serialized_chars(m)
                        for m in ctx.history
                    )
                else:
                    messages_chars = sum(
                        len(json.dumps(
                            m, ensure_ascii=False, default=str,
                        ))
                        for m in ctx.history
                    )
                tools_chars = 0
                if ctx.send_tools:
                    try:
                        tools_chars = len(json.dumps(
                            ctx.send_tools,
                            ensure_ascii=False,
                            default=str,
                        ))
                    except (TypeError, ValueError):
                        tools_chars = 0
                total_chars = messages_chars + tools_chars
                token_calibration.observe(
                    ctx.model,
                    chars=total_chars,
                    actual_tokens=prompt_tokens,
                )
        except Exception:
            # Un fallo de calibracion no debe romper la generacion,
            # pero sí debe quedar trazado: era un punto ciego de
            # diagnostico cuando el ratio se desviaba.
            logger.exception("Error calibrando tokens para %s", ctx.model)
        if ctx.on_metrics is not None:
            try:
                ctx.on_metrics(round_metrics)
            except Exception:
                logger.exception("Error emitiendo metricas al llamante")

    @staticmethod
    def _execute_round_tools(
        ctx: _ChatContext,
        result: RoundResult,
    ) -> _RoundExecution:
        """Ejecuta las tool calls de una ronda.

        Appendea el mensaje assistant con tool_calls al historial
        (necesario para que el chat template del modelo entienda la
        cadena tool_call -> tool_result), ejecuta cada tool via
        `authorize_and_execute`, y devuelve el resumen.
        """
        assistant_msg: dict[str, Any] = {
            "role": "assistant",
            "content": result.assistant_content,
            "tool_calls": [
                {"function": {"name": name, "arguments": args}}
                for name, args in result.tool_calls
            ],
        }
        # Reenviar el thinking del modelo en el assistant message. El
        # chat template de modelos de razonamiento (gpt-oss, qwen3)
        # lo espera; sin él, el tool loop puede degradar.
        #
        # Hueco 5 (2026-09-26): truncar para no acumular 20k+ tokens
        # de thinking en tool loops largos. Preserva head + tail.
        if result.assistant_thinking:
            assistant_msg["thinking"] = _shrink_thinking(
                result.assistant_thinking
            )
        ctx.history.append(assistant_msg)
        had_block = False
        had_execution = False
        had_write = False
        had_failure = False
        signature: str | None = None
        for name, args in result.tool_calls:
            text = authorize_and_execute(
                name, args, ctx.gate,
                ctx.authorization_text, ctx.on_tool,
                last_assistant=ctx.last_assistant,
            )
            if text.startswith("OPERACIÓN NO AUTORIZADA"):
                had_block = True
            else:
                had_execution = True
                if text.startswith("ERROR"):
                    had_failure = True
                if (
                    name in _WRITE_TOOLS
                    and not text.startswith("ERROR")
                    and not text.startswith("OPERACIÓN CANCELADA")
                ):
                    had_write = True
                try:
                    signature = json.dumps(
                        {"name": name, "args": args},
                        sort_keys=True, ensure_ascii=False, default=str,
                    )
                except Exception:
                    signature = f"{name}:{args!r}"
            ctx.history.append(
                ctx.strategy.format_tool_result(name, text)
            )
        return _RoundExecution(
            round_signature=signature,
            had_block=had_block,
            had_execution=had_execution,
            had_write=had_write,
            had_failure=had_failure,
        )

    @staticmethod
    def _evaluate_round(
        ctx: _ChatContext,
        execution: _RoundExecution,
        state: _LoopState,
    ) -> str | None:
        """Evalua una ronda con tool calls. Devuelve mensaje de stop o None.

        Detecta dos condiciones de corte:
          · Firma de tool repetida consecutivamente (modelo en bucle).
          · Todas las rondas bloqueadas consecutivamente (autorizacion).
        """
        sig = execution.round_signature
        if sig is not None:
            state.recent_tool_signatures.append(sig)
            repeated = 0
            for old in reversed(state.recent_tool_signatures):
                if old == sig:
                    repeated += 1
                else:
                    break
            if repeated >= _MAX_REPEATED_SIGNATURES:
                return _LOOP_REPEATED_MSG

        if execution.had_block and not execution.had_execution:
            state.consecutive_blocked_rounds += 1
            if state.consecutive_blocked_rounds >= _MAX_CONSECUTIVE_BLOCKED_ROUNDS:
                return _BLOCKED_ROUNDS_MSG
        else:
            state.consecutive_blocked_rounds = 0
        return None

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

        # Truncado selectivo: tool results largos se recortan antes
        # de medir, para que fit() no los elimine enteros por no
        # caber. Preserva head + tail; el modelo puede volver a
        # leer si necesita mas. Ver Hueco 3 (2026-09-26).
        rest = _shrink_tool_results(rest)

        system_content = (
            str(system_msg.get("content", "")) if system_msg else ""
        )

        pruned, budget = context_window.fit(
            system_prompt=system_content,
            tool_definitions=tool_definitions,
            messages=rest,
            cache=cache,
        )

        # Marcador de poda: si se recortaron mensajes, avisar al
        # modelo explícitamente. Sin esto, el modelo sigue razonando
        # como si tuviera el historial completo y alucina sobre cosas
        # que ya no están en el contexto. Se re-hace el fit con el
        # marcador añadido al system para que el presupuesto lo
        # cuente. El marcador pesa ~50 tokens; la segunda pasada es
        # rápida (O(n) en el peor caso) y garantiza que no exceda.
        if budget.dropped_messages > 0:
            marker = (
                f"\n\n[CONTEXTO RECORTADO: {budget.dropped_messages} "
                "mensaje(s) anteriores eliminados por límite de ventana. "
                "No los tienes disponibles. Si necesitas información "
                "previa, pídesela al usuario antes de responder.]"
            )
            new_system = system_content + marker
            pruned, budget = context_window.fit(
                system_prompt=new_system,
                tool_definitions=tool_definitions,
                messages=rest,
                cache=cache,
            )
            # Si el system ya no cabe con el marcador, es que el
            # presupuesto está al borde. Reintentamos sin marcador
            # (el modelo perderá el aviso, pero al menos habrá
            # contexto).
            if budget.overflow and len(pruned) == 0:
                pruned, budget = context_window.fit(
                    system_prompt=system_content,
                    tool_definitions=tool_definitions,
                    messages=rest,
                    cache=cache,
                )
                new_system = system_content
            system_content = new_system

        if system_msg is not None:
            # Actualizar el system message con el marcador si lo hay.
            if system_content != str(system_msg.get("content", "")):
                updated = dict(system_msg)
                updated["content"] = system_content
                return [updated] + list(pruned)
            return [system_msg] + list(pruned)
        return list(pruned)

    def _choose_strategy(
        self, caps, tools, model: str = ""
    ) -> Any:
        """Selecciona la estrategia según el modo de tool calling.

        Devuelve NativeToolStrategy o XmlToolStrategy. Añadir un
        tercer modo sería añadir una rama aquí, sin tocar el bucle
        de chat().

        Modo XML si las capabilities lo dicen, nativo en otro caso.
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
            # Defensa: un provider roto podria devolver algo que no
            # es un dict. Fail-closed: gate vacio (bloquea todo) en
            # vez de tirar del registro global, que solo existe para
            # tests. Un provider roto debe fallar cerrado, no abierto.
            if not isinstance(rules, dict):
                return ToolIntentGate({})
            return ToolIntentGate(rules)
        # Path de compatibilidad con tests (listas sueltas, sin
        # provider). El registro global existe SOLO como mecanismo de
        # tests. El codigo productivo siempre pasa un ToolProvider con
        # intent_rules(). No escribir al registro desde aqui.
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
    def _user_asked_question(text: str | None) -> bool:
        """True si el texto del usuario es una pregunta.

        Evita disparar el nudge por preguntas informativas que
        mencionan verbos de escritura ("¿cómo escribo un archivo?").
        Usa marcas inequivocas (?, ¿) y palabras interrogativas
        con tilde en espanol. NO incluye "que" sin tilde: es
        demasiado frecuente en frases declarativas.
        """
        if not text:
            return False
        s = text.strip()
        if not s:
            return False
        if s.endswith(("?", "？")):
            return True
        if s.startswith("¿"):
            return True
        lower = s.lower()
        return any(m in lower for m in _QUESTION_MARKERS)

    @staticmethod
    def _assistant_closes_turn(text: str | None) -> bool:
        """True si el assistant cerro el turno (no pregunto nada).

        Una respuesta que termina en '?' es una peticion de
        informacion, no un cierre. Cualquier otra cosa no vacia
        cuenta como cierre.
        """
        if not text:
            return False
        s = text.strip()
        if not s:
            return False
        return not s.rstrip().endswith(("?", "？"))

    @staticmethod
    def _effective_auth_text(ctx: Any) -> str:
        """Texto de autorizacion efectivo para el nudge.

        Si el usuario esta confirmando con texto corto ("si",
        "vale", "ok") y el assistant anterior contenia el verbo
        de escritura en su pregunta, se concatenan. Cubre el flujo
        assistant-pregunta -> usuario-confirma -> assistant dice
        'He corregido' sin emitir tool_call (bug 2026-09-26 noche).

        Solo aplica a confirmaciones: en un mensaje normal, se
        devuelve authorization_text tal cual.
        """
        from .intent import ToolIntentGate
        text = getattr(ctx, "authorization_text", "") or ""
        if not text:
            return ""
        if not ToolIntentGate._is_short_confirmation(text):
            return text
        last = getattr(ctx, "last_assistant", "") or ""
        if not last:
            return text
        return last + "\n" + text

    @staticmethod
    def _user_requested_write(text: str | None) -> bool:
        """True si el usuario pidio escribir/modificar algo.

        Normaliza acentos y expande conjugaciones via
        intent._cached_verb_forms. Antes era substring match
        plano y se perdian variantes ("corrige", "arreglar"...).
        """
        return _mentions_any(text, _WRITE_VERBS)

    @staticmethod
    def _has_code_block(text: str | None, min_lines: int = 10) -> bool:
        """True si `text` tiene un bloque ```...``` con N+ lineas.

        Caso real (2026-09-26): mistral-small3.2 responde con el
        codigo completo en el chat sin llamar a escribir_archivo.
        El nudge de falso completado no disparaba porque no habia
        marcador de exito. Con esto, un bloque grande en una
        peticion de escritura cuenta como falso completado.

        Exigir min_lines>=10 evita falsos positivos con ejemplos
        cortos de codigo en respuestas explicativas.
        """
        if not text:
            return False
        import re as _re
        pattern = _re.compile(
            r"(?:```|~~~)[^\n]*\n(.*?)(?:```|~~~)",
            _re.DOTALL,
        )
        for match in pattern.finditer(text):
            body = match.group(1)
            if body.count("\n") >= min_lines:
                return True
        return False

    @staticmethod
    def _looks_like_false_completion(text: str | None) -> bool:
        """True si el texto final declara la tarea hecha.

        Normaliza acentos para que "ya esta" y "ya está"
        matcheen igual. Los marcadores se ampliaron con formas
        como "resuelto", "solucionado", "funciona" (H1).
        """
        return _mentions_marker(text, _FALSE_COMPLETION_MARKERS)

    @staticmethod
    def _user_requested_verification(text: str | None) -> bool:
        """True si el mensaje del usuario pide verificacion explicita.

        Se usa para activar el stall guard: si el modelo responde
        sin emitir tool calls y el usuario pidio ejecutar/verificar/
        citar, se inyecta un nudge y se repite la ronda.

        Normaliza y conjuga: "corre los tests", "compilalo",
        "pasa las pruebas" ahora matchean (H1).
        """
        return _mentions_any(text, _VERIFICATION_VERBS)

    @staticmethod
    def _strip_native_tool_calls(
        history: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Adapta el historial al modo XML.

        Cuando pasamos de NativeToolStrategy a XmlToolStrategy
        (porque el modelo fallo repetidamente en tool calling), el
        historial puede tener mensajes con estructura nativa:

          · assistant con `tool_calls` declarados.
          · role="tool" con `tool_name`.

        En modo XML esos mensajes no son validos: no enviamos `tools`
        en el payload, y el chat template del modelo revienta (500)
        al intentar renderizarlos. Aqui los normalizamos:

          · assistant con tool_calls → assistant sin tool_calls
            (conservamos el content visible).
          · role="tool" → role="user" con prefijo [TOOL_RESULT:name]
            para que _last_user_text los ignore (confused deputy).

        Devuelve una lista nueva; no muta la original.
        """
        cleaned: list[dict[str, Any]] = []
        for m in history:
            role = m.get("role")
            if role == "assistant" and m.get("tool_calls"):
                new_m = {
                    k: v for k, v in m.items()
                    if k != "tool_calls"
                }
                cleaned.append(new_m)
            elif role == "tool":
                name = str(
                    m.get("tool_name") or m.get("name") or "tool"
                )
                content = str(m.get("content") or "")
                cleaned.append({
                    "role": "user",
                    "content": f"[TOOL_RESULT:{name}]\n{content}",
                })
            else:
                cleaned.append(m)
        return cleaned

    @staticmethod
    def _last_assistant_before_last_user(
        history: list[dict[str, Any]],
    ) -> str:
        """Ultimo assistant ANTES del ultimo user.

        Se usa para autorizar confirmaciones conversacionales: el
        usuario responde "si" a una pregunta del modelo, y el gate
        necesita saber que pregunto el assistant (P1 auditoria).
        """
        last_user_idx: int | None = None
        for i in range(len(history) - 1, -1, -1):
            if history[i].get("role") == "user":
                last_user_idx = i
                break
        if last_user_idx is None:
            return ""
        for i in range(last_user_idx - 1, -1, -1):
            if history[i].get("role") == "assistant":
                return str(history[i].get("content", ""))
        return ""

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
    @staticmethod
    def _parse_cli_tool_call(
        content: str, tool_names: set[str]
    ) -> tuple[str, dict[str, Any]] | None:
        """Detecta `tool_name --arg1 val1 --arg2 val2` en texto.

        Tercer fallback tras XML y JSON. Caso real: mistral-small3.2
        emite el tool call como linea de comandos cuando el chat
        template falla (2026-09-26).

        Reglas estrictas contra falsos positivos:
          - 1 sola linea significativa (max 3 con basura).
          - No contiene ``` (bloque de codigo).
          - Empieza por un nombre de tool conocido.
          - Tiene al menos un --arg con valor.
        Los --kebab-case se normalizan a snake_case para casar
        con el schema (--line-start -> line_start).
        """
        if not content or "```" in content:
            return None
        lines = [
            l.rstrip() for l in content.strip().splitlines() if l.strip()
        ]
        if not lines or len(lines) > 3:
            return None
        first = lines[0].strip()
        parts = first.split(maxsplit=1)
        if not parts or parts[0] not in tool_names:
            return None
        if "--" not in first:
            return None
        try:
            tokens = shlex.split(first)
        except ValueError:
            return None
        if not tokens or tokens[0] not in tool_names:
            return None
        name = tokens[0]
        args: dict[str, Any] = {}
        i = 1
        while i < len(tokens):
            tok = tokens[i]
            if tok.startswith("--"):
                key = tok[2:].replace("-", "_")
                if i + 1 >= len(tokens):
                    break
                args[key] = tokens[i + 1]
                i += 2
            else:
                i += 1
        if not args:
            return None
        return name, args

    @staticmethod
    def _iter_balanced_json(text: str) -> Iterator[str]:
        """Genera substrings balanceados {...} en orden de aparicion.

        No hace un parseo real: solo cuenta llaves. Cada candidato
        se intenta json.loads por el llamante. Ignora bloques
        desbalanceados al final (JSON a medio escribir).
        """
        depth = 0
        start = -1
        for i, ch in enumerate(text):
            if ch == "{":
                if depth == 0:
                    start = i
                depth += 1
            elif ch == "}":
                depth -= 1
                if depth == 0 and start >= 0:
                    yield text[start : i + 1]
                    start = -1
                elif depth < 0:
                    depth = 0
                    start = -1

    @staticmethod
    def _parse_textual_json_tool_call(
        content: str, tool_names: set[str]
    ) -> tuple[str, dict[str, Any]] | None:
        """Extrae un tool_call emitido como JSON en prosa.

        Cubre el bug de Ollama #15539 (Gemma4 y similares): el
        modelo emite el JSON correctamente pero el parser no lo
        intercepta y aparece como texto plano en content.

        Acepta varias formas:
          - {"name": "X", "arguments": {...}}
          - {"name": "X", "parameters": {...}}
          - {"function": {"name": "X", "arguments": {...}}}
          - {"tool_calls": [{"function": {...}}]}

        Devuelve (name, args) solo si name esta en tool_names:
        evita falsos positivos con JSON de ejemplo.
        """
        if not content or "{" not in content:
            return None
        import json as _json
        # Endurecido: solo si el content es SOLO bloques JSON
        # (con whitespace entre ellos). Si hay prosa alrededor,
        # es narracion del modelo, no una tool call filtrada.
        # Evita falsos positivos con ejemplos de codigo.
        remaining = content
        for block in OllamaClient._iter_balanced_json(content):
            remaining = remaining.replace(block, "", 1)
        if remaining.strip():
            return None
        for block in OllamaClient._iter_balanced_json(content):
            try:
                data = _json.loads(block)
            except (ValueError, TypeError):
                continue
            if not isinstance(data, dict):
                continue
            name = data.get("name")
            args = data.get("arguments") or data.get("parameters")
            # Forma {"function": {"name": ..., "arguments": ...}}
            fn = data.get("function")
            if not isinstance(name, str) and isinstance(fn, dict):
                name = fn.get("name")
                if args is None:
                    args = fn.get("arguments") or fn.get("parameters")
            # Forma {"tool_calls": [{"function": {...}}]}
            if not isinstance(name, str):
                tcs = data.get("tool_calls")
                if isinstance(tcs, list) and tcs:
                    first = tcs[0]
                    if isinstance(first, dict):
                        fn2 = first.get("function")
                        if isinstance(fn2, dict):
                            name = fn2.get("name")
                            if args is None:
                                args = fn2.get("arguments") or fn2.get("parameters")
            if not isinstance(name, str) or name not in tool_names:
                continue
            if not isinstance(args, dict):
                args = {}
            return name, args
        return None

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
                    "Ollama no respondió en el tiempo de espera (900 s). "
                    "Si acabas de enviar un prompt largo, puede estar "
                    "procesándolo (prefill). Es normal con modelos densos "
                    "como gemma4 o llama3 en equipos con poca memoria. "
                    "Si se repite, prueba un modelo MoE (gpt-oss, qwen3) "
                    "o reduce el tamaño del prompt. Detalle: ReadTimeout."
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
        thinking_parts: list[str] = []
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
                        line, message, content_parts, thinking_parts
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
                    buffer.strip(), message, content_parts, thinking_parts
                )
                if event is not None:
                    yield event

        # Distinguir respuesta completa de interrumpida. `completed`
        # es True si Ollama emitió done=true antes de cerrar el socket.
        # Si solo salimos por EOF, la respuesta quedó truncada.
        completed = bool(message.pop("_done", None))
        done_reason = message.pop("_done_reason", None)
        message["content"] = "".join(content_parts)
        if thinking_parts:
            message["thinking"] = "".join(thinking_parts)
        # Extraer métricas reales (guardadas por parse_ollama_line) y
        # limpiar los campos temporales del mensaje.
        metrics: dict[str, int] = {}
        for key in list(message.keys()):
            if key.startswith("_metric_"):
                metrics[key[len("_metric_"):]] = message.pop(key)
        yield StreamFinished(
            message=message,
            metrics=metrics,
            completed=completed,
            done_reason=done_reason,
        )

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
            value = override.thinking
            # N1 auditoria 2026-09-26: gpt-oss espera "low"/
            # "medium"/"high"; un bool se ignora silenciosamente.
            # Mapeamos bool->nivel SOLO para esa familia; el resto
            # de modelos sigue enviando el bool tal cual.
            if isinstance(value, bool) and model.split(":", 1)[0].startswith("gpt-oss"):
                value = "high" if value else "low"
                logger.info(
                    "Modelo %s: thinking=%s mapeado a %r "
                    "(gpt-oss exige low/medium/high)",
                    model, override.thinking, value,
                )
            payload["think"] = value

        message: dict[str, Any] = {}
        metrics: dict[str, int] = {}
        buffering_textual = False
        # `True` cuando tras inspeccionar el buffer hemos visto una
        # keyword inequívoca de tool call. Una vez confirmado, no se
        # cancela: el buffering se mantiene hasta el final.
        buffering_confirmed = False
        # Fragmentos guardados desde que se activó el buffering. Al
        # final, si NO era tool-call, se emite SOLO este fragmento, no
        # el content completo (eso duplicaba el texto ya emitido).
        buffered_parts: list[str] = []
        # Estado incremental de code-fence markdown: True si el
        # texto emitido termina dentro de un ``` abierto. Se
        # actualiza con cada delta emitido (O(delta)) en vez de
        # reconstruir el texto acumulado (O(n²) en respuestas
        # largas). Auditoria 2026-09-26.
        in_code_fence: bool = False

        async for event in self.iter_ollama_events(
            payload, cancel_event=cancel_event
        ):
            if isinstance(event, TextDelta):
                if on_text is not None:
                    if buffering_textual:
                        buffered_parts.append(event.text)
                        if not buffering_confirmed:
                            joined = "".join(buffered_parts)
                            if any(
                                kw in joined
                                for kw in _TOOL_CALL_KEYWORDS
                            ):
                                # Confirma tool call: seguir buffereando.
                                buffering_confirmed = True
                            elif "}" in joined or "]" in joined:
                                # JSON cerrado y sin keywords: es
                                # prosa con código/JSON. Emitir.
                                on_text(joined)
                                in_code_fence = _advance_fence_state(
                                    in_code_fence, joined
                                )
                                buffering_textual = False
                                buffered_parts.clear()
                            elif len(joined) > _MAX_PEEK_CHARS:
                                # Safety net: demasiado tiempo sin
                                # decidir. Asumir prosa.
                                on_text(joined)
                                in_code_fence = _advance_fence_state(
                                    in_code_fence, joined
                                )
                                buffering_textual = False
                                buffered_parts.clear()
                    else:
                        # Buscar un posible inicio de JSON en CUALQUIER
                        # posicion del delta, no solo al principio. Con
                        # deltas de 4-5 chars, el `{` puede caer en medio
                        # ("...tienes: {"na"). Antes se emitia sin mas.
                        start = -1
                        for ch in ("{", "["):
                            idx = event.text.find(ch)
                            if idx != -1 and (start == -1 or idx < start):
                                start = idx
                        if start == -1:
                            on_text(event.text)
                            in_code_fence = _advance_fence_state(
                                in_code_fence, event.text
                            )
                        elif start == 0:
                            # H5: si ya hay un fence abierto, el `{`
                            # es codigo del mensaje, no tool call.
                            if in_code_fence:
                                on_text(event.text)
                                in_code_fence = _advance_fence_state(
                                    in_code_fence, event.text
                                )
                            else:
                                buffering_textual = True
                                buffering_confirmed = False
                                buffered_parts.append(event.text)
                        else:
                            # Emitir lo previo, luego decidir.
                            prefix = event.text[:start]
                            rest = event.text[start:]
                            on_text(prefix)
                            in_code_fence = _advance_fence_state(
                                in_code_fence, prefix
                            )
                            if in_code_fence:
                                on_text(rest)
                                in_code_fence = _advance_fence_state(
                                    in_code_fence, rest
                                )
                            else:
                                buffering_textual = True
                                buffering_confirmed = False
                                buffered_parts.append(rest)
            elif isinstance(event, StreamFinished):
                message = event.message
                metrics = event.metrics
                # H1/H2: propagar estado de finalizacion. Sin esto,
                # chat() no puede distinguir respuesta completa de
                # interrumpida o truncada por limite de tokens.
                message["_stream_completed"] = event.completed
                message["_stream_done_reason"] = event.done_reason

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
                    # Investigacion 2026-09-26 (§5): fallback JSON
                    # crudo. Cubre el bug de Ollama #15539 (Gemma4
                    # y similares): el modelo emite el JSON
                    # correctamente pero el parser no lo intercepta.
                    json_call = self._parse_textual_json_tool_call(
                        content, tool_names
                    )
                    if json_call is not None:
                        jname, jargs = json_call
                        message["_textual_json_call"] = (jname, jargs)
                        message.setdefault("tool_calls", []).append({
                            "function": {
                                "name": jname,
                                "arguments": jargs,
                            }
                        })
                    else:
                        # Tercer fallback: formato CLI textual
                        # (`tool --k v`). Bug 2026-09-26 con
                        # mistral-small3.2.
                        cli_call = self._parse_cli_tool_call(
                            content, tool_names
                        )
                        if cli_call is not None:
                            cname, cargs = cli_call
                            message["_textual_cli_call"] = (
                                cname, cargs
                            )
                            message.setdefault(
                                "tool_calls", []
                            ).append({
                                "function": {
                                    "name": cname,
                                    "arguments": cargs,
                                }
                            })
                        else:
                            textual_name = (
                                OllamaClient._textual_tool_call_name(
                                    content, tool_names
                                )
                            )
                            if textual_name:
                                message["_textual_tool_name"] = (
                                    textual_name
                                )

        # Si activamos buffering pero NO resulto ser tool call
        # textual NI hubo tool_calls nativos, el usuario no ha visto
        # lo que se buffereo. Emitir SOLO ese fragmento, no `content`
        # completo: emitir el content entero duplicaba el texto ya
        # emitido antes de activar el buffer.
        #
        # Si hay tool_calls nativos, NO emitimos: el JSON escrito
        # como texto corresponde a la tool call y no debe verse.
        has_native_tool_calls = bool(message.get("tool_calls"))
        if (
            buffering_textual
            and on_text is not None
            and not textual_name
            and not has_native_tool_calls
        ):
            pending = "".join(buffered_parts)
            if pending:
                on_text(pending)
                in_code_fence = _advance_fence_state(
                    in_code_fence, pending
                )

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
        thinking_parts: list[str] | None = None,
    ) -> StreamEvent | None:
        """Procesa una linea NDJSON. Devuelve un StreamEvent o None."""
        try:
            data = json.loads(line)
        except ValueError:
            # Ollama puede enviar chunks cortados en fronteras raras
            # durante desconexiones o cancelaciones. Descartar la
            # linea es correcto; sin logging era un punto ciego de
            # diagnostico.
            logger.debug(
                "Linea NDJSON invalida descartada: %r", line[:200]
            )
            return None
        chunk = data.get("message") or {}

        calls_raw = chunk.get("tool_calls")
        if calls_raw:
            message.setdefault("tool_calls", []).extend(calls_raw)

        # Los modelos de razonamiento (gpt-oss, qwen3, gemma4) emiten
        # `thinking` junto a `content`. Se acumula para poder reenviarlo
        # al modelo en la siguiente ronda de tool calling, ya que el
        # chat template lo espera en el assistant message previo.
        # No se emite a la UI.
        if thinking_parts is not None and chunk.get("thinking"):
            thinking_parts.append(str(chunk["thinking"]))

        delta: str | None = None
        if chunk.get("content"):
            delta = str(chunk["content"])
            content_parts.append(delta)

        if data.get("done"):
            message["_done"] = True
            # Motivo de cierre reportado por Ollama: "stop", "length",
            # "unload", etc. Se guarda con prefijo para limpiarlo al
            # emitir StreamFinished.
            reason = data.get("done_reason")
            if isinstance(reason, str) and reason:
                message["_done_reason"] = reason
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
