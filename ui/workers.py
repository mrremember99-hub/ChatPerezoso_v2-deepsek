"""Workers que corren en hilos aparte."""
from __future__ import annotations

import threading
import time
from typing import Any

from PySide6.QtCore import QObject, Signal

from core.ollama import OllamaCancelled, OllamaClient, OllamaError
from core.tool_result import ToolResult
from plugins.mcp import MCPClient, MCPError


# Tiempo máximo que un worker espera una confirmación del usuario.
# Sin límite, un cierre de ventana dejaba el worker colgado.
CONFIRMATION_TIMEOUT_SECONDS = 600  # 10 minutos


# Cap real del buffer de streaming. push() bloquea al productor
# hasta que el consumidor drene o el cancel_event se active. Los
# deltas mayores que este cap se aceptan enteros (no se puede
# descartar texto ya generado): eso es una excepción deliberada.
#
# En la práctica el buffer apenas acumula: el controller hace drain
# cada 32 ms (ChatController._drain_stream). A 200 tok/s × ~5 chars/tok
# son ~32 chars en el momento del drain. Este umbral solo importa si
# la UI se queda atascada minutos, en cuyo caso la insistencia en la
# señal basta para recuperar cuando vuelva a procesar eventos.
_STREAM_BUFFER_SOFT_LIMIT_CHARS = 1_000_000


class TextDeltaBuffer:
    """Buffer thread-safe que coalesce deltas de streaming.

    Backpressure real: push() bloquea al productor si el buffer supera
    `max_chars`, hasta que drain() libera espacio o pasa el timeout.

    El productor es el hilo del AsyncRunner (no el de UI), así que
    bloquearlo aquí no congela Qt: solo ralentiza la lectura del
    stream de Ollama hasta que la UI drene. Ese es el efecto de
    backpressure que se busca (mismo principio que el ring buffer de
    llama.cpp, server-stream.h).

    push(text) devuelve True solo cuando el buffer pasa de vacío a no
    vacío, para que el consumidor arranque un timer de drain solo si
    no estaba ya pendiente.
    """

    def __init__(
        self, max_chars: int = _STREAM_BUFFER_SOFT_LIMIT_CHARS,
    ) -> None:
        self._cond = threading.Condition()
        self._parts: list[str] = []
        self._chars = 0
        # Validar: max_chars <= 0 desactiva el cap y el push
        # siempre entraria por el branch `len(text) >= max_chars`.
        self._max_chars = max(1, int(max_chars))

    def push(
        self,
        text: str,
        cancel_event: threading.Event | None = None,
    ) -> bool:
        """Añade texto. Bloquea hasta que haya hueco o se cancele.

        Backpressure real: si el buffer esta lleno, espera a que el
        consumidor drene. Sin `cancel_event`, espera indefinidamente
        (es lo correcto: si el productor no puede ceder, el buffer
        se llenara sin control). Con `cancel_event`, sale con False
        si se activa.

        Un chunk individual mas grande que `max_chars` se acepta
        igual: rechazar texto ya generado es peor que un pico de
        RAM puntual.

        Devuelve True si el buffer paso de vacio a no vacio.
        """
        if not text:
            return False

        # Delta gigante: no cabe nunca. Aceptarlo sin esperar.
        if len(text) >= self._max_chars:
            with self._cond:
                was_empty = not self._parts
                self._parts.append(text)
                self._chars += len(text)
                return was_empty

        with self._cond:
            while self._chars + len(text) > self._max_chars:
                if cancel_event is not None and cancel_event.is_set():
                    return False
                self._cond.wait(timeout=0.1)
            was_empty = not self._parts
            self._parts.append(text)
            self._chars += len(text)
            return was_empty

    def drain(self) -> str:
        with self._cond:
            if not self._parts:
                return ""
            text = "".join(self._parts)
            self._parts.clear()
            self._chars = 0
            self._cond.notify_all()
            return text

    @property
    def pending_chars(self) -> int:
        with self._cond:
            return self._chars


class ModelWorker(QObject):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, client: OllamaClient):
        super().__init__()
        self.client = client

    def run(self) -> None:
        try:
            self.finished.emit(self.client.list_models())
        except OllamaError as exc:
            self.error.emit(str(exc))


class CapabilitiesWorker(QObject):
    """Consulta /api/show para saber si el modelo soporta tools.

    Se ejecuta en un hilo aparte porque la consulta implica una
    peticion HTTP que puede tardar hasta 5s si Ollama esta ocupado.

    El `generation` permite al llamante descartar resultados
    obsoletos: si el usuario cambia de modelo varias veces, solo la
    última consulta debe actualizar la UI. El worker lo emite en
    ambas señales para que el receptor pueda comparar.
    """

    finished = Signal(str, object, int)  # model_name, caps, generation
    error = Signal(str, str, int)        # model_name, mensaje, generation

    def __init__(self, host: str, model: str, generation: int = 0):
        super().__init__()
        self.host = host
        self.model = model
        self.generation = generation

    def run(self) -> None:
        from core.model_capabilities import get_capabilities
        try:
            caps = get_capabilities(self.host, self.model)
            self.finished.emit(self.model, caps, self.generation)
        except Exception as exc:
            self.error.emit(self.model, str(exc), self.generation)


class MCPWorker(QObject):
    finished = Signal(str, object, list)
    error = Signal(str, str)

    def __init__(self, server_id: str, client: Any):
        super().__init__()
        self.server_id = server_id
        self.client = client

    def run(self) -> None:
        try:
            tools = self.client.list_tools()
            self.finished.emit(self.server_id, self.client, tools)
        except MCPError as exc:
            self.error.emit(self.server_id, str(exc))
        except BaseException as exc:
            # Cualquier otra excepcion tambien debe llegar al
            # controller. Si no, el bridge queda con estado
            # inconsistente: el worker nunca emite signal, el
            # controller cree que sigue conectado, y la UI muestra
            # el servidor como activo mientras las llamadas fallan.
            self.error.emit(
                self.server_id,
                f"{type(exc).__name__}: {exc}",
            )


class ChatWorker(QObject):
    # Señal ligera: solo se emite cuando el buffer pasa de vacío a no
    # vacío. Sustituye al flujo de un Signal por cada delta. El
    # consumidor (ChatController) hace el drain real desde un timer.
    stream_ready = Signal()
    tool = Signal(str)
    tool_result = Signal(object)          # ToolResult
    confirmation_requested = Signal(str, object)
    tool_auto_approved = Signal(str)      # name
    metrics_updated = Signal(object)      # dict[str, int] de una ronda
    finished = Signal(str)
    cancelled = Signal()
    error = Signal(str)

    def __init__(
        self,
        client: OllamaClient,
        model: str,
        messages: list[dict],
        tools: Any,
        options: dict | None = None,
        system_prompt: str = "",
        auto_approve: bool = False,
        context_window: Any = None,
    ):
        super().__init__()
        self.client = client
        self.model = model
        self.messages = messages
        self.tools = tools
        self.options = options
        self.system_prompt = system_prompt
        self.auto_approve = auto_approve
        # ContextWindow opcional. Si viene, OllamaClient.chat() ajusta
        # el historial al presupuesto en cada ronda del bucle de tools.
        self.context_window = context_window
        # Buffer coalescente del texto generado. Ver TextDeltaBuffer.
        self._text_buffer = TextDeltaBuffer()
        self._cancel_event = threading.Event()
        # Protege la lectura/escritura de _confirmation_* entre el
        # hilo del worker y el hilo de UI. Sin lock, `cancel()` y
        # `resolve_confirmation()` pueden pisarse al escribir
        # `_confirmation_approved`.
        self._confirmation_lock = threading.Lock()
        self._confirmation_event: threading.Event | None = None
        self._confirmation_name = ""
        self._confirmation_arguments: dict[str, Any] = {}
        self._confirmation_approved = False

    def run(self) -> None:
        try:
            result = self.client.chat(
                self.model,
                self.messages,
                self.tools,
                self._on_model_text,
                self._call_tool,
                cancel_event=self._cancel_event,
                options=self.options,
                system_prompt=self.system_prompt,
                context_window=self.context_window,
                on_metrics=self.metrics_updated.emit,
            )
            self.finished.emit(result)
        except OllamaCancelled:
            self.cancelled.emit()
        except Exception as exc:
            self.error.emit(str(exc))

    def _on_model_text(self, text: str) -> None:
        """Callback que el OllamaClient invoca por cada delta de texto.

        En lugar de emitir una señal Qt por delta, acumula en el
        buffer y emite `stream_ready` solo cuando el buffer pasa de
        vacío a no vacío. Esto desacopla productor y consumidor: Qt
        procesa el drain a su ritmo (típicamente 30 Hz) aunque Ollama
        genere a 200+ chunks/s.
        """
        became_non_empty = self._text_buffer.push(
            text,
            cancel_event=self._cancel_event,
        )
        if became_non_empty:
            self.stream_ready.emit()

    def drain_text(self) -> str:
        """Devuelve el texto acumulado desde el último drain."""
        return self._text_buffer.drain()

    def cancel(self) -> None:
        """Marca la cancelación.

        NO cerramos la respuesta HTTP desde este hilo: httpx/httpcore
        no es thread-safe para eso y causaba segfaults en macOS.
        En su lugar, AsyncRunner cancela el future de asyncio
        (future.cancel()) desde su hilo watcher, lo que interrumpe el
        await en el siguiente checkpoint de la corrutina — normalmente
        sub-milisegundo, no "entre chunks" como en una versión anterior
        de este mecanismo.
        """
        self._cancel_event.set()
        with self._confirmation_lock:
            event = self._confirmation_event
            if event is not None:
                self._confirmation_approved = False
        # Fuera del lock.
        if event is not None:
            event.set()

    # -- ejecución de herramientas -----------------------------------------

    def _call_tool(self, name: str, arguments: dict) -> str:
        self.tool.emit(name)

        requires = self.tools.requires_confirmation(name)
        # Piloto automático: salta el diálogo para todas las tools
        # EXCEPTO `ejecutar_comando`. El shell siempre confirma: es la
        # única garantía frente a comandos destructivos.
        auto = self.auto_approve and name != "ejecutar_comando"

        if requires and not auto:
            # _request_confirmation devuelve también el tiempo REAL de
            # ejecución de la tool (excluye el tiempo del diálogo).
            result, duration_ms = self._request_confirmation(name, arguments)
        elif requires and auto:
            # Auto-aprobado: ejecutar con allow_destructive=True y
            # avisar al usuario con una señal para que la UI lo muestre.
            self.tool_auto_approved.emit(name)
            start = time.monotonic()
            result = self.tools.call(
                name,
                arguments,
                allow_destructive=True,
                cancel_event=self._cancel_event,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
        else:
            start = time.monotonic()
            result = self.tools.call(
                name,
                arguments,
                cancel_event=self._cancel_event,
            )
            duration_ms = int((time.monotonic() - start) * 1000)

        tool_result = self._build_result(name, result, duration_ms)
        self.tool_result.emit(tool_result)
        return tool_result.to_text()

    @staticmethod
    def _build_result(name: str, raw: str, duration_ms: int) -> ToolResult:
        raw = raw or "(sin resultado)"
        if raw.startswith("ERROR MCP") or raw.startswith("ERROR:"):
            return ToolResult(
                tool_name=name,
                summary=raw.split("\n", 1)[0],
                detail=raw,
                is_error=True,
                duration_ms=duration_ms,
            )
        if raw.startswith("OPERACIÓN CANCELADA"):
            return ToolResult(
                tool_name=name,
                summary="Operación cancelada por el usuario.",
                detail=raw,
                is_cancelled=True,
                duration_ms=duration_ms,
            )
        first_line, _, rest = raw.partition("\n")
        summary = first_line.strip()
        truncated = "truncad" in rest.lower()
        return ToolResult(
            tool_name=name,
            summary=summary,
            detail=rest.strip(),
            duration_ms=duration_ms,
            truncated=truncated,
        )

    def _request_confirmation(
        self, name: str, arguments: dict[str, Any]
    ) -> tuple[str, int]:
        """Pide confirmación al usuario y ejecuta la tool si aprueba.

        Devuelve (resultado, duración_ms) donde duración_ms es el tiempo
        REAL de ejecución de la tool, sin contar el tiempo que el usuario
        ha pasado en el diálogo de confirmación.
        """
        event = threading.Event()
        with self._confirmation_lock:
            self._confirmation_event = event
            self._confirmation_name = name
            self._confirmation_arguments = dict(arguments)
            self._confirmation_approved = False
        self.confirmation_requested.emit(name, dict(arguments))

        # Con timeout: si la UI no responde (ventana cerrada, por ejemplo),
        # el worker se desbloquea y sigue su curso.
        event.wait(timeout=CONFIRMATION_TIMEOUT_SECONDS)

        # Leer el estado final y resetearlo bajo el lock, para que el
        # hilo de UI no escriba `_confirmation_approved` mientras
        # nosotros lo estamos leyendo.
        with self._confirmation_lock:
            approved = self._confirmation_approved
            self._confirmation_event = None
            self._confirmation_name = ""
            self._confirmation_arguments = {}
            self._confirmation_approved = False

        if approved and not self._cancel_event.is_set():
            start = time.monotonic()
            result = self.tools.call(
                name,
                arguments,
                allow_destructive=True,
                cancel_event=self._cancel_event,
            )
            duration_ms = int((time.monotonic() - start) * 1000)
        elif self._cancel_event.is_set():
            result = (
                "OPERACIÓN CANCELADA POR EL USUARIO: "
                "no se ha ejecutado ninguna operación."
            )
            duration_ms = 0
        else:
            result = (
                "OPERACIÓN CANCELADA: no se recibió confirmación del usuario "
                "a tiempo. No se ha ejecutado ninguna operación."
            )
            duration_ms = 0

        return result, duration_ms

    def resolve_confirmation(self, approved: bool) -> None:
        with self._confirmation_lock:
            event = self._confirmation_event
            if event is None:
                return
            self._confirmation_approved = approved
        # Fuera del lock: el worker está esperando en event.wait() y
        # despertará al recibir el set().
        event.set()