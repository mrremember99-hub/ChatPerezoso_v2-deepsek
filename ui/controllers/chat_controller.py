from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, QThread, QTimer, Signal
from PySide6.QtWidgets import QWidget

import logging

from core.context_window import ContextWindow
from core.session_summary import (
    SessionSummary,
    build_summary_prompt,
    format_summary_block,
)
from core.history import AsyncHistoryWriter, HistoryStore
from core.ollama import is_textual_tool_failure
from core.models_config import is_verified_tool_model
from core.model_capabilities import is_model_available
from core.tool_provider import ToolProvider
from core.prompt_phases import (
    DetectedPhases,
    build_phase_prompt,
    detect_phases,
)
from core.tool_result import ToolResult
from core.shutdown import remaining

from ..chat_state import ChatState

from ..rendering import ChatRenderer
from ..views.dialogs import confirm_tool
from ..workers import ChatWorker

logger = logging.getLogger(__name__)


# Numero minimo de turnos (user+assistant) que se conservan al
# compactar. Aunque el contexto se llene, no bajamos de aqui.
MIN_TURNS_TO_KEEP = 8

# Ratio de conversion chars -> tokens (estandar para es/en).
_CHARS_PER_TOKEN = 4

# Limite por defecto si no conocemos el del modelo.
_FALLBACK_CONTEXT_TOKENS = 4096

# Umbral de compactacion: 70% del contexto efectivo.
_COMPACT_THRESHOLD = 0.70

# Intervalo de drain del buffer de streaming. ~30 fps. El worker
# acumula deltas y el controller los vuelca al renderer a este ritmo.
# Sustituye al flujo de un Signal por delta.
_STREAM_DRAIN_INTERVAL_MS = 32

# Tiempo máximo de espera al worker en shutdown. Si no termina en
# este plazo, se fuerza la salida y se registra en el log.
_SHUTDOWN_GRACE_MS = 3000


_NARRATION_TEMPLATES = {
    "buscar_en_workspace": "Buscando en el workspace…",
    "leer_archivo": "Leyendo archivo…",
    "listar_carpeta": "Listando carpeta…",
    "crear_archivo": "Creando archivo…",
    "escribir_archivo": "Escribiendo archivo…",
    "borrar_archivo": "Borrando archivo…",
    "crear_carpeta": "Creando carpeta…",
    "ejecutar_comando": "Ejecutando comando…",
    "git_status": "Consultando estado de Git…",
    "git_diff": "Obteniendo diferencias…",
    "git_log": "Consultando historial de Git…",
    "git_show": "Mostrando commit…",
}


class ChatController(QObject):
    streaming_changed = Signal(bool)  # DEPRECADO: usar state_changed
    state_changed = Signal(object)  # ChatState
    status = Signal(str)
    assistant_message = Signal(str)
    error_message = Signal(str)
    conversation_changed = Signal()
    mcp_error = Signal(str)
    textual_tool_attempt = Signal()
    # Se emite tras 2 fallos consecutivos de tool calling con un
    # modelo no verificado. Lleva el nombre del modelo como
    # argumento. El AppController abre un dialogo con opciones.
    no_tool_calling_detected = Signal(str)
    # Cola de prompts: (actual, total), 1-based.
    queue_progress = Signal(int, int)
    queue_finished = Signal()
    # Contenido completo de la cola (lista de prompts).
    queue_list_set = Signal(list)
    # Estado de un elemento: (índice 1-based, estado).
    queue_item_status_changed = Signal(int, str)
    queue_paused = Signal()
    # Métricas reales de la última ronda del worker.
    metrics_updated = Signal(object)

    # Atributos usados por tests. La app real no los asigna.
    # Declararlos aquí permite que Pylance no marque los accesos
    # como error en tests/*.py sin ensuciar el código con # type: ignore.
    _owner: Any = None
    _fake_worker: Any = None
    _fake_thread: Any = None

    def __init__(
        self,
        parent: QObject,
        parent_widget: QWidget | None,
        client: Any,
        tools: Any,
        renderer: ChatRenderer,
        store: HistoryStore | None = None,
        initial_messages: list[dict] | None = None,
        summary_model: str = "",
    ):
        # `client` y `tools` se anotan como Any porque los tests pasan
        # dobles que no cumplen los protocolos completos, y el worker
        # real se sustituye en tests vía _spawn_worker. En producción
        # siempre llega un OllamaClient y un ToolProvider reales.
        super().__init__(parent)
        self._parent_widget = parent_widget
        self.client = client
        self.tools = tools
        self.renderer = renderer
        self.store = store or HistoryStore()
        self._async_writer = AsyncHistoryWriter(self.store)
        self.messages: list[dict] = list(initial_messages or [])
        # Any porque los tests sustituyen el worker y el thread
        # reales por dobles que no heredan de ChatWorker/QThread.
        self._thread: Any = None
        self._worker: Any = None
        self._state: ChatState = ChatState.IDLE
        self._last_model = ""
        self._last_options: dict[str, Any] | None = None
        self._last_system_prompt = ""
        # Modo piloto automático: si True, el worker salta el diálogo
        # de confirmación para las tools. El shell se rige por
        # `_auto_approve_shell`.
        self._auto_approve = False
        # Extensión opt-in: si True, `ejecutar_comando` también se
        # auto-aprueba. Requiere `_auto_approve=True` para tener efecto.
        self._auto_approve_shell = False
        # Hook de verificación post-escritura. Callable o None.
        self._verificador_hook: Any = None
        # Cola de prompts para envío secuencial. Vacía = no hay cola.
        self._queue: list[str] = []
        # Orquestación determinista: si el prompt tiene N>=2 fases
        # detectables, se activa este plan. Cada elemento de
        # _phase_bodies es el cuerpo de una fase. En cada ronda
        # _advance_queue regenera el prompt con snapshot fresco.
        self._phase_plan: DetectedPhases | None = None
        self._phase_bodies: list[str] = []
        self._workspace_provider: Any = None
        self._queue_total: int = 0
        self._queue_active: bool = False
        # Cuando un prompt falla, la cola se pausa en vez de
        # descartarse. El usuario decide si reintentar, saltar
        # o cancelar desde la UI.
        self._queue_paused: bool = False
        # Prompt en curso (o que acaba de fallar). Guardado
        # porque _advance_queue hace pop antes de enviar.
        self._current_prompt: str = ""
        # Reintentos del prompt actual. Solo informativo.
        self._current_retry_count: int = 0
        # Resumen rolling de la sesion (Hueco 2). Se regenera
        # cada 20 mensajes, max 2 ciclos. Se inyecta al system
        # prompt del siguiente turno.
        self._session_summary = SessionSummary()
        # Modelo para el resumen. Pequeno y rapido por diseno:
        # el resumen es una tarea simple y no merece el grande.
        self._summary_model: str = summary_model or "qwen3:1.7b"
        # Aviso unico por sesion si el modelo no esta instalado (D6).
        self._summary_model_warned: bool = False
        self._current_actions: list[ToolResult] = []
        # Fallos consecutivos de tool calling textual. Reset:
        # tras mostrar el dialogo, tras un turno con tool calls
        # OK, y al cambiar de modelo.
        self._consecutive_textual_failures: int = 0
        # Limite de contexto del modelo activo, en tokens. 0 = desconocido.
        self._context_limit: int = 0
        # Ultimo ContextBudget calculado en _compact_if_needed.
        # Se usa para el badge de contexto del sidebar (Hueco 4).
        self._last_budget: Any | None = None
        # ContextWindow cacheado. Se recrea solo cuando cambia el
        # limite efectivo (ver _get_context_window).
        self._context_window: ContextWindow | None = None
        # Debounce de persistencia: en lugar de escribir todo el
        # historial a disco por cada mensaje, acumulamos cambios y
        # escribimos 500 ms despues del ultimo. Reduce el trabajo
        # sincrono en el hilo de UI de N escrituras por turno a 1.
        self._persist_timer = QTimer(self)
        self._persist_timer.setSingleShot(True)
        self._persist_timer.setInterval(500)
        self._persist_timer.timeout.connect(self._do_persist)
        # Timer de drain del buffer de streaming. El worker solo emite
        # `stream_ready` cuando pasa de vacío a no vacío; este timer
        # hace el drain real cada 32 ms y llama al renderer. Así Qt no
        # recibe una señal por delta.
        self._stream_timer = QTimer(self)
        self._stream_timer.setSingleShot(True)
        self._stream_timer.setInterval(_STREAM_DRAIN_INTERVAL_MS)
        self._stream_timer.timeout.connect(self._drain_stream)

    # -- API pública ---------------------------------------------------------
    def _summary_model_available(self) -> bool:
        """Comprueba disponibilidad del modelo de resumen (D6).

        Cacheado 60s via core.model_capabilities.is_model_available.
        Aviso una sola vez por sesion si no esta: el resumen se
        deshabilita pero el chat sigue funcionando.
        """
        host = getattr(self.client, "host", "")
        if not host or not self._summary_model:
            return False
        ok = is_model_available(host, self._summary_model)
        if not ok and not self._summary_model_warned:
            self._summary_model_warned = True
            logger.warning(
                "Modelo de resumen %r no disponible en %s; el "
                "resumen de sesion queda deshabilitado hasta que "
                "este instalado.",
                self._summary_model, host,
            )
        return ok

    @property
    def state(self) -> ChatState:
        """Estado actual del chat."""
        return self._state

    def is_streaming(self) -> bool:
        """Compatibilidad: True si el chat está activo (streaming o cancelando)."""
        return self._state.is_active

    def _set_state(self, new_state: ChatState) -> None:
        """Cambia el estado y emite las señales correspondientes."""
        if new_state is self._state:
            return
        old_is_active = self._state.is_active
        self._state = new_state
        self.state_changed.emit(new_state)
        new_is_active = new_state.is_active
        if old_is_active != new_is_active:
            self.streaming_changed.emit(new_is_active)

    def rebind_tools(self, tools: ToolProvider) -> None:
        self.tools = tools

    # -- cola de prompts ----------------------------------------------------

    def enqueue(
        self,
        prompts: list[str],
        model: str,
        options: dict[str, Any] | None = None,
        system_prompt: str | None = None,
    ) -> bool:
        """Encola una lista de prompts y envía el primero.

        Devuelve False si ya hay un turno activo o una cola en curso,
        o si la lista está vacía. La cola se ejecuta con el mismo
        modelo, opciones y system prompt hasta que termina o se
        cancela.
        """
        if self._state.is_active or self._queue_active:
            return False
        if not prompts or not model:
            return False

        self._queue = list(prompts)
        self._queue_total = len(prompts)
        self._queue_active = True

        # Capturar el contexto para reutilizarlo en cada prompt.
        self._last_model = model
        if options is not None:
            self._last_options = dict(options)
        if system_prompt is not None:
            self._last_system_prompt = system_prompt or ""

        # Todo list visual: se emite la lista al panel derecho.
        if len(prompts) > 1:
            self.queue_list_set.emit(list(prompts))

        self._advance_queue()
        return True

    def has_queue(self) -> bool:
        return self._queue_active

    def _stop_queue_with_message(self, message: str) -> None:
        """Detiene la cola y limpia el estado interno.

        Marca los prompts pendientes como cancelados en el todo list
        para que el usuario vea qué quedó sin hacer.
        """
        # Índice del actual en el todo list.
        current = self._queue_total - len(self._queue)
        # Marcar todos los pendientes a partir del siguiente como
        # cancelados.
        for offset in range(len(self._queue)):
            idx = current + offset + 1
            self.queue_item_status_changed.emit(idx, "cancelled")

        remaining = len(self._queue)
        self._queue.clear()
        self._queue_total = 0
        self._queue_active = False
        self.queue_finished.emit()
        if remaining > 0:
            self.status.emit(f"{message}: {remaining} pendiente(s)")

    def set_workspace_provider(self, provider: Any) -> None:
        """Registra un callable que devuelve el Workspace actual.

        Se usa para regenerar el snapshot del workspace antes de
        cada fase en modo orquestación. Si es None, no se incluye
        snapshot en los prompts de fase.
        """
        self._workspace_provider = provider

    def _current_workspace_snapshot(self) -> str:
        if self._workspace_provider is None:
            return ""
        try:
            ws = self._workspace_provider()
        except Exception:
            return ""
        if ws is None:
            return ""
        try:
            from core.workspace_snapshot import snapshot_workspace
            return snapshot_workspace(ws)
        except Exception:
            return ""

    def send_user_input(
        self,
        text: str,
        model: str,
        options: dict[str, Any] | None = None,
        system_prompt: str | None = None,
    ) -> bool:
        """Punto de entrada del chat con detección de fases.

        Si el texto contiene N>=2 fases (``FASE 1``, ``FASE 2``...),
        lo trocea en N conversaciones independientes y las envía
        secuencialmente con la cola existente. Cada fase recibe el
        mismo *preamble*, su propio cuerpo, y un snapshot fresco
        del workspace en el momento de enviarse.

        Si no hay fases, delega a ``send()`` normal.
        """
        # Si hay una cola pausada (por cancelacion de una fase) y
        # el usuario envia un mensaje nuevo, se cancela la cola:
        # es lo intuitivo. Sin esto, _queue_active sigue True y el
        # mensaje se descarta en silencio (bug UX 2026-09-26).
        if self._queue_active and self._queue_paused:
            self.cancel_paused_queue()
            self.status.emit(
                "Cola pausada cancelada al enviar mensaje nuevo"
            )
        if self._state.is_active or self._queue_active:
            return False
        if not text or not model:
            return False

        plan = detect_phases(text)
        if plan is None:
            self.send(text, model, options, system_prompt)
            return True

        self._phase_plan = plan
        self._phase_bodies = list(plan.phases)
        snapshot = self._current_workspace_snapshot()
        prompts = [
            build_phase_prompt(plan.preamble, body, snapshot)
            for body in plan.phases
        ]
        self.status.emit(
            f"Orquestando {plan.count} fases · prefill por fase reducido"
        )
        return self.enqueue(prompts, model, options, system_prompt)

    def _advance_queue(self) -> None:
        """Envía el siguiente prompt. Si no hay más, cierra la cola."""
        if not self._queue:
            self._queue_total = 0
            self._queue_active = False
            self._phase_plan = None
            self._phase_bodies.clear()
            self.status.emit("Cola completada")
            self.queue_finished.emit()
            return

        current = self._queue_total - len(self._queue) + 1
        total = self._queue_total
        next_prompt = self._queue.pop(0)

        # Orquestación: regenerar el prompt de esta fase con
        # snapshot fresco del workspace (la fase anterior puede
        # haber creado o modificado archivos).
        if self._phase_plan is not None and self._phase_bodies:
            idx = current - 1
            if 0 <= idx < len(self._phase_bodies):
                snapshot = self._current_workspace_snapshot()
                next_prompt = build_phase_prompt(
                    self._phase_plan.preamble,
                    self._phase_bodies[idx],
                    snapshot,
                )

        self._current_prompt = next_prompt
        self._current_retry_count = 0
        self.queue_progress.emit(current, total)
        # Marcar el prompt que arranca como "running".
        self.queue_item_status_changed.emit(current, "running")
        # P2: resetear historial entre fases. Cada fase es
        # independiente; sin esto se arrastra el historial de las
        # anteriores (~12k tokens en un prompt de 9 fases).
        if self._phase_plan is not None:
            self._reset_phase_history()
        self.send(
            next_prompt,
            self._last_model,
            self._last_options,
            self._last_system_prompt,
        )

    def resume_queue_retry(self) -> bool:
        """Reintenta el prompt que falló. Solo válido si pausada.

        Reenvía el mismo prompt con el mismo contexto. No hay
        límite de reintentos: el usuario decide cuándo parar.
        """
        if not self._queue_paused:
            return False
        self._queue_paused = False
        self._current_retry_count += 1
        current = self._queue_total - len(self._queue)
        self.queue_item_status_changed.emit(current, "running")
        self.status.emit(
            f"Reintentando prompt {current} "
            f"(intento {self._current_retry_count + 1})"
        )
        self.send(
            self._current_prompt,
            self._last_model,
            self._last_options,
            self._last_system_prompt,
        )
        return True

    def resume_queue_skip(self) -> bool:
        """Salta el prompt que falló y sigue con el siguiente."""
        if not self._queue_paused:
            return False
        self._queue_paused = False
        self._current_retry_count = 0
        current = self._queue_total - len(self._queue)
        self.queue_item_status_changed.emit(current, "skipped")
        self._current_prompt = ""
        self.status.emit(f"Prompt {current} saltado, continúa la cola")
        self._advance_queue()
        return True

    def cancel_paused_queue(self) -> bool:
        """Cancela la cola pausada y descarta lo pendiente."""
        if not self._queue_paused:
            return False
        self._queue_paused = False
        self._current_prompt = ""
        self._current_retry_count = 0
        current = self._queue_total - len(self._queue)
        self.queue_item_status_changed.emit(current, "cancelled")
        self._stop_queue_with_message("Cola cancelada por el usuario")
        return True

    def is_queue_paused(self) -> bool:
        return self._queue_paused

    def set_auto_approve(self, enabled: bool) -> None:
        """Activa o desactiva el piloto automático de confirmaciones.

        Cuando está activo, el worker no muestra el diálogo de
        confirmación para las herramientas. `ejecutar_comando` (shell)
        se rige por `set_auto_approve_shell`: si está desactivado,
        siempre confirma.
        """
        self._auto_approve = bool(enabled)
        if self._worker is not None:
            self._worker.auto_approve = self._auto_approve
        # Cascada: si el piloto principal se apaga, la extensión de
        # shell también. Así nunca queda un estado inconsistente.
        if not enabled:
            self.set_auto_approve_shell(False)

    def set_auto_approve_shell(self, enabled: bool) -> None:
        """Activa o desactiva la auto-aprobación de `ejecutar_comando`.

        Solo tiene efecto si `set_auto_approve(True)` está activo.
        El worker aplica la doble puerta por su cuenta, pero aquí
        normalizamos el estado para que la UI y el worker coincidan.
        """
        enabled = bool(enabled) and self._auto_approve
        self._auto_approve_shell = enabled
        if self._worker is not None:
            self._worker.auto_approve_shell = enabled

    def set_verificador_hook(self, hook: Any) -> None:
        """Registra el callable de verificación post-escritura.

        Pasar None para desactivar. El hook recibe la ruta relativa
        del archivo y devuelve texto (vacío si OK).
        """
        self._verificador_hook = hook
        if self._worker is not None:
            self._worker.verificador_hook = hook

    def set_current_model(self, model: str) -> None:
        # Si el modelo cambia, el ContextWindow cacheado apunta al
        # modelo antiguo (calibración distinta). Invalidarlo aquí
        # fuerza a _get_context_window a recrearlo con el nuevo modelo.
        if model != self._last_model:
            self._context_window = None
        self._last_model = model

    def set_current_options(self, options: dict[str, Any] | None) -> None:
        self._last_options = dict(options) if options else None

    def set_current_system_prompt(self, system_prompt: str) -> None:
        self._last_system_prompt = system_prompt or ""

    def set_context_limit(self, tokens: int) -> None:
        """Limite de contexto efectivo del modelo activo, en tokens.

        0 significa 'desconocido': se usa _FALLBACK_CONTEXT_TOKENS.
        """
        self._context_limit = max(0, int(tokens))

    def last_assistant_text(self) -> str:
        for message in reversed(self.messages):
            if message.get("role") == "assistant":
                content = message.get("content")
                return content if isinstance(content, str) else ""
        return ""

    def send(
        self,
        text: str,
        model: str,
        options: dict[str, Any] | None = None,
        system_prompt: str | None = None,
    ) -> None:
        if self._state.is_active or not text or not model:
            return
        self._last_model = model
        if options is not None:
            self._last_options = dict(options)
        if system_prompt is not None:
            self._last_system_prompt = system_prompt

        self.renderer.insert_user_message(text)
        self._append_message({"role": "user", "content": text})
        self.renderer.reset()
        # Resumen rolling (Hueco 2) — D1 (auditoria 2026-09-26):
        # el calculo se movio al ChatWorker. Aqui SOLO se construye
        # el prompt (puro, sin red) si toca. El worker lo envia al
        # modelo al terminar el turno y emite summary_ready.
        summary_prompt = ""
        summary_new_index = 0
        _summary = getattr(self, "_session_summary", None)
        if (
            _summary is not None
            and _summary.should_update(len(self.messages))
            and self._summary_model_available()
        ):
            summary_prompt = build_summary_prompt(
                self.messages,
                keep_recent=0,
                previous_summary=_summary.text,
                since_index=_summary.last_message_count,
            )
            if summary_prompt:
                summary_new_index = len(self.messages)
        # Trace del turno anterior: leer ANTES de limpiar
        # _current_actions. Los tool_results no van al historial
        # persistente, asi que sin esto el modelo no sabe que
        # tools se ejecutaron en el turno inmediatamente anterior.
        trace = self._build_tool_trace()
        effective_system_prompt = self._last_system_prompt or ""
        # Inyectar resumen de sesion ANTES del system base si
        # existe. Formato: [RESUMEN DE LA SESION]\n...\n\n<base>
        summary = getattr(self, "_session_summary", None)
        summary_text = summary.text if summary is not None else ""
        if summary_text:
            # H2 (auditoria 2026-09-26): base del agente PRIMERO,
            # resumen despues. Antes el resumen (hasta 2k chars)
            # desplazaba las instrucciones del rol, degradando
            # instruction-following en modelos con atencion debil
            # a tokens iniciales largos.
            effective_system_prompt = (
                effective_system_prompt
                + "\n\n"
                + summary_text
                if effective_system_prompt.strip()
                else summary_text
            )
        if trace:
            effective_system_prompt = (
                effective_system_prompt + "\n\n---\n\n" + trace
                if effective_system_prompt.strip()
                else trace
            )
        self._current_actions = []
        self._set_state(ChatState.STREAMING)
        self.status.emit("Generando…")
        self._spawn_worker(
            model,
            self._last_options,
            effective_system_prompt,
            summary_prompt,
            summary_new_index,
        )

    def regenerate(self, model: str) -> None:
        if self._state.is_active or not self.messages or not model:
            return
        last_user_idx: int | None = None
        for i in range(len(self.messages) - 1, -1, -1):
            if self.messages[i].get("role") == "user":
                last_user_idx = i
                break
        if last_user_idx is None:
            return
        user_text = str(self.messages[last_user_idx].get("content", ""))
        if not user_text:
            return
        self._last_model = model
        # Truncar SIEMPRE a last_user_idx. El guard anterior
        # (last_user_idx < len - 1) fallaba cuando el ultimo mensaje
        # era un user sin respuesta (turno cancelado). En ese caso,
        # send() volvia a anadir el mismo user y el mensaje se
        # duplicaba en el historial.
        self.messages = self.messages[:last_user_idx]
        self.renderer.remove_from_last_user()
        self.send(user_text, model, self._last_options, self._last_system_prompt)

    def _spawn_worker(
        self,
        model,
        options=None,
        system_prompt="",
        summary_prompt="",
        summary_new_index=0,
    ):
        self._thread = QThread(self)
        self._worker = ChatWorker(
            self.client,
            model,
            list(self.messages),
            self.tools,
            options=options,
            system_prompt=system_prompt,
            auto_approve=self._auto_approve,
            auto_approve_shell=self._auto_approve_shell,
            context_window=self._get_context_window(),
            verificador_hook=self._verificador_hook,
            summary_model=self._summary_model,
            summary_prompt=summary_prompt,
            summary_new_index=summary_new_index,
        )
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.stream_ready.connect(self._schedule_stream_drain)
        self._worker.tool.connect(self._on_tool)
        self._worker.tool_result.connect(self._on_tool_result)
        self._worker.confirmation_requested.connect(self._on_confirmation)
        self._worker.tool_auto_approved.connect(self._on_tool_auto_approved)
        self._worker.metrics_updated.connect(self._on_worker_metrics)
        self._worker.summary_ready.connect(self._on_summary_ready)
        self._worker.finished.connect(self._on_done)
        self._worker.error.connect(self._on_error)
        self._worker.cancelled.connect(self._on_cancelled)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._worker.cancelled.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup)
        self._thread.start()

    def cancel(self) -> None:
        if self._worker is not None:
            self._worker.cancel()
            self._set_state(ChatState.CANCELLING)

    def clear(self) -> None:
        if self._state.is_active:
            return
        self.messages.clear()
        if self._persist_timer.isActive():
            self._persist_timer.stop()
        self.store.clear()
        # Nueva conversacion: resumen rolling a cero.
        summary = getattr(self, "_session_summary", None)
        if summary is not None:
            summary.reset()
        self.conversation_changed.emit()

    def _reset_phase_history(self) -> None:
        """Limpia el historial entre fases de orquestacion (P2).

        Cada fase es una conversacion independiente: el modelo
        no necesita arrastrar los mensajes de fases anteriores.
        Antes, la fase N recibia el historial completo de las
        fases 1..N-1, dando prompts de ~14000 tokens cuando
        bastaban ~2000 (auditoria 2026-09-26, P2).

        NO borra el archivo persistido (store.clear lo haria) y
        NO toca el render: las fases anteriores siguen visibles
        en el chat. Solo se limpia lo que va al modelo.
        """
        self.messages.clear()
        self._current_actions.clear()
        # Cada fase es una conversacion independiente: el resumen
        # de una fase no debe filtrarse a la siguiente.
        summary = getattr(self, "_session_summary", None)
        if summary is not None:
            summary.reset()
        self.conversation_changed.emit()

    def shutdown(self, deadline: float | None = None) -> bool:
        """Cierra el controller esperando a que el worker termine.

        `deadline` es un timestamp absoluto (``time.monotonic()``) que
        marca el presupuesto total del shutdown. El metodo reparte lo
        que queda entre el worker y el AsyncHistoryWriter. Si es None,
        cada fase usa su timeout individual (compatibilidad).

        Devuelve True si todo termino dentro del presupuesto, False si
        algo expiro. En el segundo caso el watchdog global de main.py
        actua como ultimo recurso.
        """
        if self._stream_timer.isActive():
            self._stream_timer.stop()

        worker = self._worker
        thread = self._thread

        if worker is not None:
            worker.cancel()

        ok = True

        # 1. Esperar al worker con el presupuesto restante.
        if thread is not None and thread.isRunning():
            wait_budget = remaining(
                deadline, default=_SHUTDOWN_GRACE_MS / 1000.0
            )
            if wait_budget <= 0:
                logger.error(
                    "ChatController.shutdown: sin presupuesto para el "
                    "worker; el watchdog global actua como ultimo recurso"
                )
                ok = False
            else:
                wait_ms = min(int(wait_budget * 1000), _SHUTDOWN_GRACE_MS)
                if not thread.wait(wait_ms):
                    # NO llamar a thread.terminate(): la documentacion
                    # de Qt advierte que terminar un hilo Python a
                    # mitad de una operacion puede corromper estado o
                    # provocar deadlocks.
                    logger.error(
                        "ChatWorker no terminó en %d ms durante "
                        "shutdown; dejando que el watchdog global actue",
                        wait_ms,
                    )
                    ok = False

        # 2. Persistencia final sin esperar aqui: el writer drena con
        #    el presupuesto restante.
        self._persist_now(wait=False)

        writer_budget = remaining(deadline, default=3.0)
        if writer_budget <= 0:
            logger.error(
                "ChatController.shutdown: sin presupuesto para el writer"
            )
            ok = False
        else:
            if not self._async_writer.shutdown(timeout=writer_budget):
                logger.error(
                    "ChatController.shutdown: writer no terminó en %.2fs",
                    writer_budget,
                )
                ok = False

        return ok

    # -- historial -----------------------------------------------------------
    def _append_message(self, message: dict) -> None:
        self.messages.append(message)
        self._compact_if_needed()
        self._persist()
        self.conversation_changed.emit()

    def context_summary(self) -> tuple[int, int, int]:
        """Devuelve (estimated_prompt, prompt_budget, dropped_messages).

        Si aun no hay budget calculado (primer turno), devuelve
        (0, 0, 0). El sidebar usa esto para el badge de contexto.
        """
        b = self._last_budget
        if b is None:
            return (0, 0, 0)
        return (
            int(getattr(b, "estimated_prompt", 0)),
            int(getattr(b, "prompt_budget", 0)),
            int(getattr(b, "dropped_messages", 0)),
        )

    def _compact_if_needed(self) -> None:
        """Poda el historial si no cabe en el presupuesto de contexto.

        El cálculo incluye el system prompt y las definiciones de las
        herramientas, no solo el historial visible. Los tool results
        intermedios no forman parte de self.messages (viven solo en la
        copia local de OllamaClient.chat), así que no cuentan aquí.

        El coste de compactar es despreciable (<0.1 ms según el
        benchmark), así que se puede llamar tras cada append sin
        miedo. Lo que importa es *cuándo* se dispara, no cuánto tarda.
        """
        window = self._get_context_window()

        # Reservar espacio para el trace que send() va a anadir
        # despues. El padding es intencionalmente barato: solo
        # infla la estimacion de tokens para que la poda de
        # self.messages sea coherente con el envio real (H2).
        #
        # Solo se reserva si HAY acciones previas: sin ellas
        # _build_tool_trace() devuelve cadena vacia y no hay
        # nada que reservar. Reservar siempre aplastaba ventanas
        # pequeñas (limit=500 del test de regresion).
        actions = getattr(self, "_current_actions", None) or []
        reserve = self._TRACE_RESERVE_CHARS if actions else 0
        system_prompt = (self._last_system_prompt or "") + (
            " " * reserve
        )
        try:
            tool_definitions = self.tools.definitions()
        except Exception:
            # Un provider roto no debe romper la compactación.
            tool_definitions = []

        pruned, budget = window.fit(
            system_prompt=system_prompt,
            tool_definitions=tool_definitions,
            messages=self.messages,
        )
        self._last_budget = budget

        if budget.dropped_messages > 0:
            logger.info(
                "Historial podado: %d mensaje(s) eliminado(s) "
                "(~%d tokens estimados, presupuesto %d, reserva %d)",
                budget.dropped_messages,
                budget.estimated_prompt,
                budget.prompt_budget,
                budget.output_reserve,
            )
            self.messages = pruned

    def _get_context_window(self) -> ContextWindow:
        """Devuelve el ContextWindow cacheado, recreándolo si cambia el límite.

        El límite efectivo es el mínimo entre el límite del modelo y el
        num_ctx del agente, si lo hay. 0 o negativo significa "desconocido":
        se usa _FALLBACK_CONTEXT_TOKENS.
        """
        effective_ctx = self._context_limit
        if self._last_options and self._last_options.get("num_ctx"):
            try:
                num_ctx = int(self._last_options["num_ctx"])
            except (TypeError, ValueError):
                num_ctx = 0
            if num_ctx > 0:
                if effective_ctx > 0:
                    effective_ctx = min(num_ctx, effective_ctx)
                else:
                    effective_ctx = num_ctx
        if effective_ctx <= 0:
            effective_ctx = _FALLBACK_CONTEXT_TOKENS

        if (
            self._context_window is None
            or self._context_window.limit_tokens != effective_ctx
        ):
            self._context_window = ContextWindow(
                limit_tokens=effective_ctx,
                model=self._last_model,
            )
        return self._context_window

    def _persist(self) -> None:
        """Programa la persistencia con debounce.

        Nota: durante una generacion larga, si _append_message se
        llama repetidamente antes de que expire el timer, la
        persistencia puede posponerse indefinidamente. El historial
        queda entonces en estado "eventually consistent": no se
        escribe hasta que la generacion termina o el usuario cierra
        la app. shutdown() fuerza un _persist_now() con flush.

        No escribe a disco directamente. Rearranca un timer de 500 ms
        que ejecutara _do_persist cuando no haya mas cambios. Asi una
        rafaga de _append_message() (user + assistant + tool results)
        produce una sola escritura.
        """
        self._persist_timer.start()

    def _do_persist(self) -> None:
        """Encola la escritura del historial. No bloquea el hilo UI."""
        self._async_writer.submit(
            self.messages, model=self._last_model
        )

    def _persist_now(self, *, wait: bool = True) -> None:
        """Fuerza la escritura inmediata. Se usa al cerrar o limpiar.

        wait=True (default): espera a que la escritura a disco
          termine. Mantiene el contrato del nombre: tras esta
          llamada, el archivo esta en disco.
        wait=False: encola sin esperar. Para casos donde el
          llamante no puede bloquearse.
        """
        if self._persist_timer.isActive():
            self._persist_timer.stop()
        self._do_persist()
        if wait:
            self._async_writer.flush()

    # -- slots internos ------------------------------------------------------
    def _schedule_stream_drain(self) -> None:
        """Arranca el timer de drain si no está corriendo ya."""
        if not self._stream_timer.isActive():
            self._stream_timer.start(_STREAM_DRAIN_INTERVAL_MS)

    def _drain_stream(self) -> None:
        """Vuelca el buffer del worker al renderer.

        Defensivo: si el worker es un fake de tests sin `drain_text`,
        no hace nada.
        """
        worker = self._worker
        if worker is None:
            return
        drain = getattr(worker, "drain_text", None)
        if drain is None:
            return
        text = drain()
        if text:
            self.renderer.on_text(text)

    def _on_tool(self, name: str) -> None:
        narration = _NARRATION_TEMPLATES.get(name, f"Ejecutando {name}…")
        self.renderer.insert_narration(narration, active=True)

    def _on_tool_result(self, result: ToolResult) -> None:
        if result.is_error and result.summary.startswith("ERROR MCP"):
            server_id = self._extract_mcp_server(result.tool_name)
            if server_id:
                self.mcp_error.emit(server_id)
        self._current_actions.append(result)
        self.renderer.insert_tool_card(result)

    def _on_confirmation(self, name: str, arguments: dict[str, Any]) -> None:
        worker = self._worker
        if worker is None:
            return
        approved = confirm_tool(self._parent_widget, name, arguments)
        # Recheck: durante el event loop anidado del dialogo, _cleanup
        # puede haber puesto self._worker a None (cancelacion, cierre
        # de ventana). Sin este recheck, resolve_confirmation falla
        # con AttributeError.
        try:
            if self._worker is worker:
                worker.resolve_confirmation(approved)
        except RuntimeError:
            pass

    def _on_worker_metrics(self, metrics: dict) -> None:
        """Reenvía las métricas del worker al resto de la app."""
        self.metrics_updated.emit(metrics)

    def _on_tool_auto_approved(self, name: str) -> None:
        """El worker auto-aprobó una tool por el modo piloto automático.

        Mostrar una narración para que el usuario sepa qué está pasando
        sin el diálogo. Sin esto, las operaciones destructivas ocurren
        sin ningún aviso visible.
        """
        self.renderer.insert_narration(
            f"Auto-aprobado: {name}", active=False
        )

    def _on_summary_ready(self, raw: str, new_index: int) -> None:
        """Aplica el resumen generado por el ChatWorker (D1).

        Llega en el hilo de UI (Qt queued connection, porque el
        worker vive en un QThread). `new_index` es el
        `len(messages)` en el momento del send; el resumen cubre
        los mensajes desde el ultimo `last_message_count` hasta
        ahi, sin huecos.
        """
        summary = getattr(self, "_session_summary", None)
        if summary is None:
            return
        block = format_summary_block(raw)
        if not block:
            return
        summary.apply(block, new_index)
        logger.info(
            "Resumen de sesion actualizado (%d ciclos, %d chars)",
            summary.cycles, len(block),
        )

    def _on_done(self, result: str) -> None:
        # Flush final del buffer antes de aplicar Markdown. Sin esto,
        # el texto de los últimos 32 ms se perdería.
        self._drain_stream()
        response_text = self.renderer.final_text(result)
        # Detectar intento de tool calling textual en el texto final.
        # OllamaClient devuelve este mensaje cuando el modelo escribio
        # el JSON como texto dos veces seguidas.
        if is_textual_tool_failure(response_text):
            self.textual_tool_attempt.emit()
            self._consecutive_textual_failures += 1
            # Segundo fallo seguido con un modelo no verificado:
            # avisar antes de que el usuario siga perdiendo tiempo.
            if (
                self._consecutive_textual_failures >= 2
                and not is_verified_tool_model(self._last_model)
            ):
                self.no_tool_calling_detected.emit(self._last_model)
                self._consecutive_textual_failures = 0
        elif any(a.status == "ok" for a in self._current_actions):
            # El modelo si tool-callea: resetear el contador.
            self._consecutive_textual_failures = 0
        summary = self._summarize_actions()
        if summary:
            self.renderer.insert_narration(summary, active=False)
        # Solo añadir al historial si hay contenido real. Un assistant
        # vacio no aporta nada y contamina el prompt del siguiente
        # turno (el modelo puede confundirse con mensajes vacios).
        if response_text.strip():
            self._append_message(
                {"role": "assistant", "content": response_text}
            )
            self.assistant_message.emit(response_text)
        else:
            self.renderer.insert_narration(
                "El modelo no genero respuesta en este turno.",
                active=False,
            )
        self._finish("Listo")

    def reset_textual_failures(self) -> None:
        """Resetea el contador de fallos consecutivos."""
        self._consecutive_textual_failures = 0

    def _summarize_actions(self) -> str:
        actions = self._current_actions
        if not actions:
            return ""
        ok = sum(1 for a in actions if a.status == "ok")
        err = sum(1 for a in actions if a.status == "error")
        cancelled = sum(1 for a in actions if a.status == "cancelled")
        total_ms = sum(a.duration_ms for a in actions)
        parts = [f"{ok} correcta(s)"]
        if err:
            parts.append(f"{err} con error")
        if cancelled:
            parts.append(f"{cancelled} cancelada(s)")
        return (
            f"Se ejecutaron {len(actions)} herramienta(s): "
            + ", ".join(parts)
            + f" · tiempo total {total_ms} ms"
        )

    # Limites del bloque de trace que se inyecta al system prompt
    # del turno siguiente. ~15 lineas x ~30 tokens, por debajo
    # del presupuesto de 300 tokens del diseno.
    _TOOL_TRACE_MAX_ITEMS = 15
    _TOOL_TRACE_MAX_LINE = 140
    # Cap total de chars del bloque. ~3000 chars ≈ 750 tokens.
    # Si se supera, se eliminan primero las acciones mas
    # antiguas (las recientes son mas relevantes).
    _TOOL_TRACE_MAX_TOTAL_CHARS = 3000
    # Reserva para _compact_if_needed: el trace del system prompt
    # se construye DESPUES de _append_message, pero el envio real
    # a Ollama lo incluye. Sin esta reserva, la poda de
    # self.messages usaría un presupuesto mas generoso que el que
    # aplica OllamaClient._fit_round_history (H2 auditoria).
    _TRACE_RESERVE_CHARS = _TOOL_TRACE_MAX_TOTAL_CHARS
    # Limites de detalle por categoria.
    _TOOL_TRACE_ERROR_DETAIL_MAX = 400
    _TOOL_TRACE_EXEC_OUTPUT_MAX = 300
    _TOOL_TRACE_READ_SHORT_MAX = 250
    # Herramientas de escritura (sin detalle: el path ya va en la
    # cabecera). El modelo sabe que escribio por su respuesta
    # final del turno anterior.
    _TOOL_TRACE_WRITE_TOOLS = frozenset({
        "escribir_archivo", "editar_archivo",
        "insertar_en_archivo", "crear_archivo",
        "crear_carpeta", "borrar_archivo",
    })
    # Herramientas de lectura/inspeccion: contenido completo si
    # es corto, omitido si es largo (evita inundar el trace).
    _TOOL_TRACE_READ_CONTENT_TOOLS = frozenset({
        "leer_archivo", "listar_carpeta",
    })

    def _build_tool_trace(self) -> str:
        """Resumen del ultimo turno para inyectar como bloque de sistema.

        Devuelve "" si no hubo acciones. El bloque se concatena al
        system prompt del siguiente turno para que el modelo sepa que
        tools se ejecutaron (los tool_results no van al historial
        persistente).

        Incluye el output real de cada tool segun la politica por
        categoria (H3 de los informes out(3/4)): los errores siempre
        (truncados), las lecturas cortas con contenido, los comandos
        con su salida. Con cap total de chars: si se supera, se
        eliminan primero las acciones mas antiguas.
        """
        actions = self._current_actions
        if not actions:
            return ""

        # Construir de mas reciente a mas antigua para priorizar lo
        # reciente si hay que recortar por presupuesto.
        recent = actions[-self._TOOL_TRACE_MAX_ITEMS :]
        blocks: list[str] = []
        total = 0
        for a in reversed(recent):
            block = self._format_trace_block(a)
            if total + len(block) > self._TOOL_TRACE_MAX_TOTAL_CHARS:
                break
            blocks.append(block)
            total += len(block)
        blocks.reverse()

        lines = ["[ACCIONES DEL TURNO ANTERIOR]"]
        extra = len(actions) - len(blocks)
        if extra > 0:
            lines.append(f"(+{extra} acciones anteriores omitidas)")
        lines.extend(blocks)
        lines.append(
            "Estas acciones YA se ejecutaron en el turno anterior. "
            "No las repitas sin motivo."
        )
        return "\n".join(lines)

    def _format_trace_block(self, a: ToolResult) -> str:
        """Cabecera + detalle (indentado) de una ToolResult."""
        args = (a.metadata or {}).get("arguments") or {}
        arg_hint = self._format_tool_arg_hint(args)
        head = f"{a.tool_name}{arg_hint} -> {a.status}"
        if len(head) > self._TOOL_TRACE_MAX_LINE:
            head = head[: self._TOOL_TRACE_MAX_LINE - 1] + "..."

        detail = self._trace_detail_for(a)
        if not detail:
            return head
        indented = "\n".join("  " + line for line in detail.splitlines())
        return head + "\n" + indented

    def _trace_detail_for(self, a: ToolResult) -> str:
        """Detalle segun la categoria de la tool.

        - Errores: siempre, hasta _TOOL_TRACE_ERROR_DETAIL_MAX chars.
        - Escrituras OK: nada (el path ya va en la cabecera).
        - Lecturas de contenido: completo si es corto, si no, nada.
        - Otros (exec, search, git, MCP): output truncado.
        """
        if a.is_error:
            raw = a.detail or a.summary
            return self._truncate_trace(
                raw, self._TOOL_TRACE_ERROR_DETAIL_MAX
            )
        if a.tool_name in self._TOOL_TRACE_WRITE_TOOLS:
            return ""
        if a.tool_name in self._TOOL_TRACE_READ_CONTENT_TOOLS:
            raw = a.to_text()
            if len(raw) <= self._TOOL_TRACE_READ_SHORT_MAX:
                return raw
            return ""
        raw = a.to_text()
        return self._truncate_trace(raw, self._TOOL_TRACE_EXEC_OUTPUT_MAX)

    @staticmethod
    def _truncate_trace(text: str, max_len: int) -> str:
        """Trunca conservando un marcador visible."""
        if not text:
            return ""
        if len(text) <= max_len:
            return text
        return text[: max_len - 20].rstrip() + "\n[... truncado]"

    @staticmethod
    def _format_tool_arg_hint(arguments: dict) -> str:
        """Argumento mas relevante entre parentesis, recortado."""
        if not arguments:
            return ""
        for key in (
            "path", "ruta", "file", "archivo", "filename",
            "command", "cmd", "pattern", "patron", "query",
        ):
            if key in arguments:
                val = str(arguments[key])
                if len(val) > 60:
                    val = val[:59] + "..."
                return f"({val})"
        k, v = next(iter(arguments.items()))
        s = f"{k}={v}"
        if len(s) > 60:
            s = s[:59] + "..."
        return f"({s})"

    def _on_cancelled(self) -> None:
        self._finish("Cancelado")

    def _on_error(self, message: str) -> None:
        self.renderer.insert_error(message)
        self._finish("Error")

    def _finish(self, status: str) -> None:
        if status == "Error":
            self._set_state(ChatState.ERROR)
        else:
            self._set_state(ChatState.IDLE)
        self.status.emit(status)
        self.renderer.reset()

        # Cola de prompts: actualizar el todo list y avanzar o
        # detener según el estado del turno.
        if self._queue_active:
            # El prompt que acaba de terminar es el número
            # (total - pendientes). Si _advance_queue ya hizo pop, la
            # cuenta es total - len(_queue). Si aún no ha hecho pop
            # (porque estamos en el mismo turno en que se envió), el
            # actual es total - len(_queue) + 1. Como send() hace pop
            # antes de enviar, estamos siempre en el primer caso.
            current_done = self._queue_total - len(self._queue)
            if status == "Listo":
                self.queue_item_status_changed.emit(current_done, "done")
                self._advance_queue()
            elif status == "Cancelado":
                # Pausar también al cancelar: el usuario decide si
                # reintentar, saltar o descartar la cola entera.
                self.queue_item_status_changed.emit(current_done, "cancelled")
                self._queue_paused = True
                self.queue_paused.emit()
                total = self._queue_total
                pending = len(self._queue)
                if pending > 0:
                    self.status.emit(
                        f"Cola pausada en {current_done}/{total} · "
                        f"{pending} pendiente(s)"
                    )
                else:
                    self.status.emit(
                        f"Cola pausada en {current_done}/{total} · "
                        "último prompt cancelado"
                    )
            else:
                # status == "Error": pausar en vez de descartar.
                # El usuario decide si reintentar el prompt, saltarlo
                # o cancelar la cola desde el panel derecho.
                self.queue_item_status_changed.emit(current_done, "error")
                self._queue_paused = True
                self.queue_paused.emit()
                total = self._queue_total
                pending = len(self._queue)
                if pending > 0:
                    self.status.emit(
                        f"Cola pausada en {current_done}/{total} · "
                        f"{pending} pendiente(s)"
                    )
                else:
                    self.status.emit(
                        f"Cola pausada en {current_done}/{total} · "
                        "último prompt falló"
                    )

    def _cleanup(self) -> None:
        if self._stream_timer.isActive():
            self._stream_timer.stop()
        # `sender()` es el hilo que acaba de terminar. Puede no ser
        # `self._thread` si la cola avanzó y ya hay otro turno en
        # curso. En ese caso solo liberamos el hilo antiguo; las
        # referencias actuales apuntan al nuevo.
        thread = self.sender()
        if thread is not None and thread is not self._thread:
            thread.deleteLater()
            return
        if self._thread is not None:
            self._thread.deleteLater()
        self._thread = None
        self._worker = None

    @staticmethod
    def _extract_mcp_server(tool_name: str) -> str | None:
        if not tool_name.startswith("mcp__"):
            return None
        parts = tool_name.split("__", 2)
        if len(parts) < 2 or not parts[1]:
            return None
        return parts[1]