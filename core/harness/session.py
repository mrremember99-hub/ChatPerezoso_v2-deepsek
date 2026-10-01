"""HarnessSession — orquestador principal del ciclo de agente.

Spec: docs/harness-v3.md §1, §4.

Estado de slices:
  S4-a         — step() sin tool calls. ModelClient protocol.
  S4-b (este)  — ciclo completo con tool calls + idempotencia.
  S4-c         — integracion con LoopDetector + HealthMonitor +
                 confirmaciones bloqueantes.
"""
from __future__ import annotations

import hashlib
import inspect
import logging
import threading
import time
from collections.abc import Callable, Iterator
from dataclasses import replace as _dc_replace
from datetime import UTC, datetime
from typing import Any

from core.approval import is_auto_approved
from core.harness.durable import (
    IdempotencyRegistry,
    idempotency_key,
)
from core.harness.events import (
    ConfirmationRequested,
    ConfirmationResolved,
    Event,
    HarnessError,
    HealthSnapshot,
    LoopAborted,
    LoopCorrectivePrompt,
    LoopWarning,
    MessageCompleted,
    VerificationRun,
    MessageDelta,
    RunEnded,
    RunStarted,
    StepEnded,
    StepStarted,
    ToolCallCompleted,
    ToolCallRequested,
)
from core.harness.health import HealthMonitor
from core.harness.loop import CorrectivePromptBuilder, LoopDetector
from core.harness.model import ModelClient
from core.harness.policy import HarnessConfig

_logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class HarnessSession:
    """Sesion de agente. Orquesta el ciclo de un step.

    Uso:
        session = HarnessSession(
            config, model_client=client, tool_registry=registry,
        )
        for event in session.step("hola"):
            render(event)
    """

    def __init__(
        self,
        config: HarnessConfig,
        *,
        model_client: ModelClient,
        tool_registry: Any = None,
        event_log: Any = None,
        idempotency: IdempotencyRegistry | None = None,
        loop_detector: LoopDetector | None = None,
        health_monitor: HealthMonitor | None = None,
        # P2#9: firma flexible. Se acepta tanto
        # (name, args) como (name, args, *, cancel_event=...).
        # El helper _call_handler decide por inspect.
        confirmation_handler: (
            Callable[..., bool] | None
        ) = None,
        command_allowed: Callable[[str], bool] | None = None,
        # A (2026-10-01): hook de verificacion post-escritura.
        # Recibe la ruta relativa y devuelve texto (vacio = OK).
        # El harness lo llama SOLO si la tool es de escritura,
        # el status es "ok" y la ruta es str no vacia.
        verificador_hook: Callable[[str], str] | None = None,
    ) -> None:
        self.config = config
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.event_log = event_log
        self.idempotency = idempotency
        self.loop_detector = loop_detector
        self.health_monitor = health_monitor
        self.confirmation_handler = confirmation_handler
        self.command_allowed = command_allowed
        self.verificador_hook = verificador_hook
        self._corrective_builder = CorrectivePromptBuilder()
        # Contadores para health monitoring dentro del step en curso.
        self._step_error_count = 0
        self._step_findings_count = 0
        # S5-c-mini (2026-10-01): tools exitosas del step actual,
        # para que CompletionVerifier pueda comprobar que el
        # modelo ejecuto lo que dice haber completado.
        self._step_tools_executed: list[str] = []
        self._seq = 0
        self._step_index = -1
        self._run_started = False
        self._messages: list[dict[str, Any]] = []
        self._cancel = threading.Event()
        self._errors: list[str] = []
        self._step_failed = False
        self._step_in_progress = False
        self._run_ended = False

    # -- API ---------------------------------------------------------

    def load_history(
        self, messages: list[dict[str, Any]],
    ) -> None:
        """Precarga el historico conversacional en _messages.

        Solo se aceptan roles user/assistant/tool. El system
        prompt va por AgentSpec.system_prompt (P2#6); mensajes
        system en el historico se descartan silenciosamente
        para no duplicar o pisar el prompt del agente.

        Copia defensiva: no se retienen referencias a los
        dicts del argumento. No reinicia _seq ni _step_index.
        """
        allowed = {"user", "assistant", "tool"}
        for m in messages:
            if not isinstance(m, dict):
                continue
            role = m.get("role")
            if role not in allowed:
                continue
            self._messages.append({
                "role": role,
                "content": str(m.get("content", "")),
            })

    def step(self, user_message: str) -> Iterator[Event]:
        """Ejecuta un step completo (modelo + tool calls).

        No reentrante (auditoria P2#16): dos step() concurrentes
        comparten _messages, _step_index, _seq y _cancel. El
        segundo detecta el flag y falla limpio.
        """
        if self._step_in_progress:
            raise RuntimeError(
                "HarnessSession.step() no es reentrante"
            )
        self._step_in_progress = True
        try:
            yield from self._step_impl(user_message)
        finally:
            self._step_in_progress = False
            # P2#10: limpiar el flag AL FINAL del step. Antes se
            # limpiaba al PRINCIPIO de _step_impl, asi que un
            # cancel() llamado justo antes de step() se perdia.
            # El caller del siguiente step decide si quiere
            # cancelar antes de empezar (flag visible).
            self._cancel.clear()

    def _step_impl(self, user_message: str) -> Iterator[Event]:
        """Cuerpo de step(). Extraido para envolver con el flag."""
        self._step_failed = False
        self._step_error_count = 0
        self._step_findings_count = 0
        self._step_tools_executed = []

        if not self._run_started:
            self._run_started = True
            yield self._emit(
                RunStarted,
                user_message=user_message,
                agent_name=self.config.agent.name,
                model_name=self.config.model.name,
            )

        self._step_index += 1
        step_index = self._step_index
        yield self._emit(StepStarted, step_index=step_index)
        self._messages.append(
            {"role": "user", "content": user_message},
        )
        # P2#3: los roles user/tool/system tambien van al log como
        # MessageCompleted para que fold_events los recupere tras
        # un crash+resume. Antes solo assistant lo hacia. Va
        # DESPUES de StepStarted: el step empieza, y el primer
        # evento del step es el mensaje del usuario.
        yield self._emit(
            MessageCompleted, role="user", content=user_message,
        )

        # P2#10: si el usuario cancelo antes/durante la apertura
        # del step, salir sin llamar al modelo. Outcome cancelled.
        if self._cancel.is_set():
            yield self._emit(
                StepEnded,
                step_index=step_index,
                outcome="cancelled",
            )
            return

        fatal: str | None = None
        try:
            yield from self._agent_loop(step_index)
        except Exception as exc:  # noqa: BLE001
            fatal = str(exc) or type(exc).__name__

        if fatal is not None:
            self._errors.append(fatal)
            yield self._emit(
                HarnessError,
                component="model",
                message=fatal,
                recoverable=False,
            )
            yield self._emit(
                StepEnded, step_index=step_index, outcome="failed",
            )
            # P2#4: el run NO se cierra aqui. El caller decide
            # cuando termina de usar la sesion (close()). Antes,
            # un fallo de Ollama marcaba el run como terminado
            # aunque la app siguiera viva y pudiera reintentar.
            return

        # S5-c-mini (2026-10-01): completion verification.
        # Si el prompt declara fases con verificacion (formato
        # OVERPAPER), comprobar que el modelo hizo lo que dice.
        # Flag OFF por defecto: cero cambio de comportamiento.
        if (
            self.config.completion_verification_enabled
            and not self._step_failed
            and not self._cancel.is_set()
        ):
            yield from self._verify_completion(
                step_index, user_message,
            )

        # P2#10: failed tiene prioridad sobre cancelled.
        # Un abort del loop detector setea _cancel Y _step_failed;
        # eso es un fallo controlado del harness, no una
        # cancelacion del usuario. Solo "cancelled" puro (usuario
        # pidio parar, sin fallo) da ese outcome.
        if self._step_failed:
            outcome = "failed"
        elif self._cancel.is_set():
            outcome = "cancelled"
        else:
            outcome = "ok"
        yield self._emit(
            StepEnded, step_index=step_index, outcome=outcome,
        )

    def close(
        self,
        reason: str = "completed",
        summary: str = "",
    ) -> Iterator[Event]:
        """Cierra el run. Emite RunEnded una sola vez.

        Idempotente: la segunda llamada no hace nada. El ciclo
        correcto es: N step() -> close() al terminar.

        Motivo de existir (P2#4): step() NO emite RunEnded
        porque no puede saber si el usuario va a pedir otro
        turno. Antes solo se emitia en la rama de excepcion de
        _step_impl, y ademas marcaba el run como terminado
        aunque la app pudiera seguir. Consecuencias del bug:
        - La retencion de EventLog nunca borraba runs normales.
        - resume_on_startup ofrecia todos como incompletos.
        - Un fallo transitorio de Ollama cerraba el run.

        Con close() explicito: el caller cierra cuando el
        usuario sale de la sesion, no cuando un step falla.
        """
        if self._run_ended:
            return
        self._run_ended = True
        yield self._emit(
            RunEnded, reason=reason, summary=summary,
        )

    def cancel(self) -> None:
        """Marca la cancelacion del step en curso."""
        self._cancel.set()

    def resume(self) -> Iterator[Event]:
        """Reanuda desde el ultimo checkpoint. S4-b: no-op."""
        return
        yield  # pragma: no cover

    def health(self) -> dict[str, Any]:
        return {
            "run_id": self.config.run_id,
            "messages": len(self._messages),
            "steps": self._step_index + 1,
            "errors": len(self._errors),
        }

    def events(self, since_seq: int = 0) -> Iterator[Event]:
        if self.event_log is None:
            return
        yield from self.event_log.read(
            self.config.run_id, since_seq=since_seq,
        )

    def pending_confirmation(self) -> Any:
        return None

    def resolve_confirmation(self, _response: Any) -> None:
        """S4-b: no-op. Confirmaciones bloqueantes en S4-c."""

    def _call_handler(
        self, name: str, arguments: dict, *, reason: str = "",
    ) -> bool:
        """Llama al confirmation_handler pasando los kwargs
        que acepte (cancel_event, reason).

        Se usa inspect para decidir que kwargs inyectar; asi
        handlers de firma vieja (name, args) siguen funcionando.
        """
        handler = self.confirmation_handler
        assert handler is not None
        try:
            params = inspect.signature(handler).parameters
            accepts_cancel = "cancel_event" in params
            accepts_reason = "reason" in params
        except (TypeError, ValueError):
            accepts_cancel = False
            accepts_reason = False
        kwargs: dict = {}
        if accepts_cancel:
            kwargs["cancel_event"] = self._cancel
        if accepts_reason:
            kwargs["reason"] = reason
        if kwargs:
            return bool(handler(name, arguments, **kwargs))
        return bool(handler(name, arguments))

    # -- loop interno ------------------------------------------------

    def _verify_completion(
        self, step_index: int, user_message: str,
    ) -> Iterator[Event]:
        """S5-c-mini: emite VerificationRun si el prompt declara
        fases en formato OVERPAPER.

        No falla el step si la verificacion no pasa: solo
        emite el evento con las issues detectadas. El flag
        completion_verification_enabled controla si se llama.
        """
        from core.harness.completion import (
            CompletionVerifier, parse_phases,
        )

        try:
            phases = parse_phases(user_message)
        except Exception:  # noqa: BLE001
            return
        if not phases:
            return

        # El turno corresponde a la ultima fase declarada en el
        # prompt (OVERPAPER pega una fase por turno).
        phase = phases[-1]
        verifier = CompletionVerifier(self.config.policy.completion)

        try:
            result = verifier.verify(
                phase,
                executed_tools=list(self._step_tools_executed),
            )
        except Exception:  # noqa: BLE001
            return

        issues: list[dict] = []
        if result.status != "verified":
            issues.append({
                "code": result.status,
                "message": result.message,
                "missing_tools": list(result.missing_tools),
            })

        yield self._emit(
            VerificationRun,
            call_id=f"completion-step-{step_index}",
            target=f"phase-{phase.index}",
            issues=issues,
        )

    def _agent_loop(self, step_index: int) -> Iterator[Event]:
        """Itera modelo -> tool calls -> modelo hasta que el modelo
        termine o se exceda max_tool_rounds.
        """
        for round_index in range(self.config.max_tool_rounds):
            text_parts: list[str] = []
            # P2#11: acumular TODOS los tool_calls del turno. Antes
            # se hacia `break` al primero y se descartaba el resto
            # (el protocolo Ollama permite N tool_calls por ronda).
            tool_calls: list[dict] = []

            for delta in self.model_client.chat(
                self._build_messages(),
                tools=self._tool_definitions(),
                stream=True,
                cancel_event=self._cancel,
            ):
                if delta.kind == "text":
                    text_parts.append(delta.text)
                    yield self._emit(
                        MessageDelta,
                        role="assistant",
                        content=delta.text,
                    )
                elif delta.kind == "tool_call":
                    if isinstance(delta.tool_call, dict):
                        tool_calls.append(delta.tool_call)
                    # NO break: seguir leyendo por si vienen mas.
                elif delta.kind == "done":
                    if delta.text and not text_parts:
                        text_parts.append(delta.text)
                        yield self._emit(
                            MessageDelta,
                            role="assistant",
                            content=delta.text,
                        )

            full_text = "".join(text_parts)
            if full_text:
                yield self._emit(
                    MessageCompleted,
                    role="assistant",
                    content=full_text,
                )
            # P2#11: el mensaje assistant va a _messages aunque no
            # haya texto si pidio tools. Formato Ollama: content +
            # tool_calls. Sin esto, la siguiente ronda no ve que el
            # modelo pidio N tools y puede volver a pedirlas.
            if full_text or tool_calls:
                assistant_msg: dict = {
                    "role": "assistant", "content": full_text,
                }
                if tool_calls:
                    assistant_msg["tool_calls"] = [
                        {
                            "function": {
                                "name": str(tc.get("name", "")),
                                "arguments": tc.get("arguments", {}),
                            },
                        }
                        for tc in tool_calls
                    ]
                self._messages.append(assistant_msg)

            if not tool_calls:
                yield from self._observe_health(step_index)
                return  # el modelo no pide nada mas: step ok

            # P2#10: si el usuario cancelo mientras el modelo
            # generaba, no ejecutar las tools.
            if self._cancel.is_set():
                yield from self._observe_health(step_index)
                return

            # P2#11: ejecutar las N tools en orden. Parar si una
            # aborta o se cancela.
            for ordinal, tc in enumerate(tool_calls):
                yield from self._execute_tool_call(
                    tc, step_index, round_index, ordinal,
                )
                if self._cancel.is_set() or self._step_failed:
                    yield from self._observe_health(step_index)
                    return

        # Excedido max_tool_rounds.
        msg = (
            f"max_tool_rounds={self.config.max_tool_rounds} superado"
        )
        self._errors.append(msg)
        self._step_failed = True
        self._step_error_count += 1
        yield self._emit(
            HarnessError,
            component="session",
            message=msg,
            recoverable=True,
        )

    def _make_call_id(
        self, step_index: int, round_index: int, ordinal: int,
    ) -> str:
        """call_id determinista (auditoria P2#1).

        Mismo (run, step, round, ordinal) -> mismo id. Tras un
        crash+resume, el harness reconstruye el step desde el
        checkpoint y reintenta la misma posicion: la clave de
        idempotencia coincide y la tool no se re-ejecuta.

        Antes: secrets.token_hex(6) -> id aleatorio por llamada,
        la rama 'completed' de IdempotencyRegistry era inalcanzable
        y dos reintentos del mismo step ejecutaban la tool dos
        veces.
        """
        raw = (
            f"{self.config.run_id}|{step_index}|"
            f"{round_index}|{ordinal}"
        ).encode()
        return "tc_" + hashlib.sha256(raw).hexdigest()[:12]

    def _build_reason(
        self, name: str, arguments: dict,
    ) -> str:
        """Motivo por el que se pide confirmacion (2026-10-01).

        Se muestra en el dialogo de UI para que el usuario
        sepa por que su tool cae a confirmacion manual.
        """
        if name == "borrar_archivo":
            return "operacion destructiva (nunca auto-aprobada)"
        if name == "ejecutar_comando":
            if not self.config.auto_approve_shell:
                return "shell requiere auto_approve_shell activo"
            cmd = (
                arguments.get("command", "")
                if isinstance(arguments, dict) else ""
            )
            if not isinstance(cmd, str) or not cmd.strip():
                return "comando malformado"
            allowed = False
            if self.command_allowed is not None:
                try:
                    allowed = bool(self.command_allowed(cmd))
                except Exception:  # noqa: BLE001
                    allowed = False
            if not allowed:
                return "comando fuera de la allowlist"
            return "shell auto-aprobable pero requiere confirmacion"
        if not self.config.auto_approve:
            return "auto_approve global desactivado"
        return "requiere confirmacion explicita"

    def _execute_tool_call(
        self,
        tool_call: dict,
        step_index: int,
        round_index: int = 0,
        ordinal: int = 0,
    ) -> Iterator[Event]:
        """Ejecuta una tool call y emite Requested/Completed.

        `call_id` es determinista (P2#1). Con IdempotencyRegistry,
        una segunda ejecucion del mismo (step, round, ordinal)
        devuelve el resultado cacheado sin re-ejecutar.
        """
        if not isinstance(tool_call, dict):
            yield self._emit(
                HarnessError,
                component="session",
                message=f"tool_call malformado: {tool_call!r}",
                recoverable=True,
            )
            yield from self._fail_tool(
                "", {}, f"tool_call malformado: {tool_call!r}", "error",
            )
            return

        name = str(tool_call.get("name", ""))
        raw_args = tool_call.get("arguments", {})
        # P2#9: validar arguments ANTES del gate. Antes, cualquier
        # valor no-dict se convertia en {} silenciosamente y el
        # handler se llamaba con una llamada malformada (regla del
        # Run #2). None es valido (muchos modelos no mandan args
        # cuando la tool no los necesita); otros tipos son error.
        if raw_args is None:
            arguments: dict = {}
        elif isinstance(raw_args, dict):
            arguments = dict(raw_args)
        else:
            msg = (
                f"tool_call arguments invalido: "
                f"{type(raw_args).__name__}"
            )
            self._messages.append({"role": "tool", "content": msg})
            yield self._emit(
                HarnessError,
                component="session",
                message=msg,
                recoverable=True,
            )
            yield from self._fail_tool(name, {}, msg, "error")
            return
        if not name:
            yield self._emit(
                HarnessError,
                component="session",
                message="tool_call sin 'name'",
                recoverable=True,
            )
            yield from self._fail_tool(
                "", {}, "tool_call sin 'name'", "error",
            )
            return

        call_id = self._make_call_id(
            step_index, round_index, ordinal,
        )

        # P2#6: AgentSpec.allowed_tools tambien se hace cumplir al
        # ejecutar. El filtro de _tool_definitions solo oculta las
        # tools al modelo; un modelo que alucine una tool fuera de
        # allowed_tools la ejecutaria sin este guard.
        if not self._is_tool_allowed(name):
            msg = (
                f"ERROR: herramienta no permitida para este agente: "
                f"{name}"
            )
            # P2#16: mutar el estado ANTES de los yields. Si el
            # consumidor abandona el generador, el log y las
            # señales deben reflejar un estado consistente.
            self._messages.append({"role": "tool", "content": msg})
            yield self._emit(
                ToolCallRequested,
                call_id=call_id,
                tool_name=name,
                arguments=arguments,
                auto_approved=False,
            )
            yield self._emit(
                MessageCompleted, role="tool", content=msg,
            )
            yield self._emit(
                ToolCallCompleted,
                call_id=call_id,
                tool_name=name,
                status="error",
                summary=msg[:120],
                detail=msg,
                duration_ms=0,
            )
            yield from self._fail_tool(name, arguments, msg, "error")
            return

        # P2#2 (auditoria externa): idempotencia ANTES del gate.
        #
        # 1. Si la op ya esta completed (reintento de un step
        #    anterior tras crash), devolvemos el resultado
        #    cacheado sin volver a pedir confirmacion.
        # 2. Si hay que ejecutar, marcamos pending ANTES del
        #    dialogo. Si el proceso muere durante la
        #    confirmacion, resume() ve la op como pending y sabe
        #    que habia una tool en vuelo.
        #
        # Antes: mark_pending iba despues del gate, asi que un
        # crash durante el dialogo de confirmacion no dejaba
        # rastro y la tool se re-ejecutaba en el siguiente run.
        key: str | None = None
        if self.idempotency is not None:
            key = idempotency_key(
                self.config.run_id, step_index, call_id,
            )
            state = self.idempotency.get_state(key)
            if state == "completed":
                cached = self.idempotency.get_result(key)
                result = str(cached) if cached is not None else ""
                self._messages.append(
                    {"role": "tool", "content": result},
                )
                yield self._emit(
                    ToolCallRequested,
                    call_id=call_id,
                    tool_name=name,
                    arguments=arguments,
                    auto_approved=True,
                )
                yield self._emit(
                    MessageCompleted, role="tool", content=result,
                )
                yield self._emit(
                    ToolCallCompleted,
                    call_id=call_id,
                    tool_name=name,
                    status="ok",
                    summary=result[:120],
                    detail=result,
                    duration_ms=0,
                )
                return
            self.idempotency.mark_pending(
                key, self.config.run_id, "tool_call", call_id,
            )

        # Gate minimo S4-b.
        requires = False
        if self.tool_registry is not None:
            try:
                requires = bool(
                    self.tool_registry.requires_confirmation(name),
                )
            except Exception:  # noqa: BLE001
                requires = False

        # S4-d: resolver el gate de confirmacion. Orden:
        #   1. Tool no requiere confirmacion -> auto_approved=True.
        #   2. auto_approve global ON        -> auto_approved=True.
        #   3. confirmation_handler inyectado:
        #      True -> aprobado; False -> denegado.
        #      Excepcion -> error con mensaje al modelo.
        #   4. Sin handler y sin auto_approve:
        #      denegar con mensaje explicativo (compat S4-b).
        #
        # El handler se llama SINCRONICAMENTE desde este hilo. Si el
        # cliente necesita bloquearse esperando UI, lo hace él; el
        # harness no crea threading.Event ni impone timeout.
        denial_reason: str | None = None
        handler_error: str | None = None

        if not requires or is_auto_approved(
            name,
            arguments,
            auto_approve=self.config.auto_approve,
            auto_approve_shell=self.config.auto_approve_shell,
            command_allowed=self.command_allowed,
        ):
            auto_approved = True
        elif self.confirmation_handler is not None:
            # P2#9: emitir ConfirmationRequested/Resolved para que
            # la UI y el log sepan que hubo un dialogo (antes solo
            # habia el efecto en el handler).
            confirm_reason = self._build_reason(name, arguments)
            yield self._emit(
                ConfirmationRequested,
                call_id=call_id,
                tool_name=name,
                arguments=dict(arguments),
                reason=confirm_reason,
            )
            try:
                auto_approved = bool(
                    self._call_handler(
                        name, arguments, reason=confirm_reason,
                    ),
                )
                yield self._emit(
                    ConfirmationResolved,
                    call_id=call_id,
                    approved=auto_approved,
                    timeout=False,
                )
            except Exception as exc:  # noqa: BLE001
                auto_approved = False
                handler_error = f"confirmation_handler fallo: {exc}"
                yield self._emit(
                    ConfirmationResolved,
                    call_id=call_id,
                    approved=False,
                    timeout=False,
                )
        else:
            auto_approved = False
            denial_reason = (
                "OPERACIÓN CANCELADA: la tool requiere confirmación "
                "explícita del usuario. El harness no tiene "
                "confirmation_handler inyectado; la tool no se ha "
                "ejecutado."
            )

        yield self._emit(
            ToolCallRequested,
            call_id=call_id,
            tool_name=name,
            arguments=arguments,
            auto_approved=auto_approved,
        )

        if handler_error is not None:
            err_msg = f"ERROR: {handler_error}"
            if self.idempotency is not None and key is not None:
                self.idempotency.mark_failed(key)
            self._messages.append(
                {"role": "tool", "content": err_msg},
            )
            yield self._emit(
                MessageCompleted, role="tool", content=err_msg,
            )
            yield self._emit(
                ToolCallCompleted,
                call_id=call_id,
                tool_name=name,
                status="error",
                summary=handler_error[:120],
                detail=err_msg,
                duration_ms=0,
            )
            yield from self._fail_tool(
                name, arguments, err_msg, "error",
            )
            return

        if denial_reason is not None:
            if self.idempotency is not None and key is not None:
                self.idempotency.mark_failed(key)
            self._messages.append(
                {"role": "tool", "content": denial_reason},
            )
            yield self._emit(
                MessageCompleted, role="tool", content=denial_reason,
            )
            yield self._emit(
                ToolCallCompleted,
                call_id=call_id,
                tool_name=name,
                status="cancelled",
                summary=denial_reason[:120],
                detail=denial_reason,
                duration_ms=0,
            )
            yield from self._fail_tool(
                name, arguments, denial_reason, "cancelled",
            )
            return

        if not auto_approved:
            # Handler devolvio False: el usuario denego.
            result = (
                "OPERACIÓN CANCELADA POR EL USUARIO: la tool "
                "requiere confirmación y fue denegada."
            )
            if self.idempotency is not None and key is not None:
                self.idempotency.mark_failed(key)
            self._messages.append(
                {"role": "tool", "content": result},
            )
            yield self._emit(
                MessageCompleted, role="tool", content=result,
            )
            yield self._emit(
                ToolCallCompleted,
                call_id=call_id,
                tool_name=name,
                status="cancelled",
                summary=result[:120],
                detail=result,
                duration_ms=0,
            )
            yield from self._fail_tool(
                name, arguments, result, "cancelled",
            )
            return

        # P2#2: el pending ya se marco arriba (antes del gate).

        # Ejecutar.
        start = time.monotonic()
        status = "ok"
        result = ""
        try:
            if self.tool_registry is None:
                result = (
                    "ERROR: harness sin tool_registry; no se puede "
                    f"ejecutar {name}."
                )
                status = "error"
            else:
                result = self.tool_registry.call(
                    name,
                    arguments,
                    allow_destructive=auto_approved,
                    cancel_event=self._cancel,
                )
                if isinstance(result, str) and result.startswith(
                    "ERROR",
                ):
                    status = "error"
        except Exception as exc:  # noqa: BLE001
            status = "error"
            result = f"ERROR: {exc}"
        duration_ms = int((time.monotonic() - start) * 1000)

        if self.idempotency is not None and key is not None:
            if status == "ok":
                self.idempotency.mark_completed(key, result)
            else:
                self.idempotency.mark_failed(key)

        # A (2026-10-01): verificar escrituras exitosas.
        if (
            status == "ok"
            and name in ("crear_archivo", "escribir_archivo",
                         "editar_archivo")
            and self.verificador_hook is not None
        ):
            rel = (
                arguments.get("path")
                or arguments.get("nombre")
                or ""
            )
            if isinstance(rel, str) and rel:
                try:
                    extra = self.verificador_hook(rel)
                    if extra:
                        result = (
                            result
                            + "\n\n[VERIFICACIÓN]\n"
                            + str(extra)
                        )
                except Exception:  # noqa: BLE001
                    pass

        if status == "ok":
            self._step_tools_executed.append(name)
        self._messages.append({"role": "tool", "content": result})
        yield self._emit(
            MessageCompleted, role="tool", content=result,
        )
        yield self._emit(
            ToolCallCompleted,
            call_id=call_id,
            tool_name=name,
            status=status,
            summary=result[:120],
            detail=result,
            duration_ms=duration_ms,
        )

        # S4-c-mini: observar el par (tool, args, result) por si es
        # un bucle. Los errores tambien cuentan para health.
        if status == "error":
            self._step_error_count += 1
        yield from self._observe_loop(
            name, arguments, result, status,
        )

    def _fail_tool(
        self,
        name: str,
        arguments: dict,
        result: str,
        status: str,
    ) -> Iterator[Event]:
        """Registra un fallo de tool antes de ejecutarla.

        Cuenta para el health monitor y alimenta al LoopDetector
        con el par (name, args, result) aunque la tool no se
        ejecute (denegacion, timeout, error previo).
        """
        self._step_error_count += 1
        yield from self._observe_loop(name, arguments, result, status)

    # -- loop + health (S4-c-mini) -----------------------------------

    def _observe_loop(
        self,
        name: str,
        arguments: dict,
        result: str,
        status: str,
    ) -> Iterator[Event]:
        """Alimenta al LoopDetector y emite los eventos resultantes."""
        if self.loop_detector is None:
            return
        try:
            decision = self.loop_detector.observe(
                name,
                arguments,
                result=result,
                result_summary=result[:120],
            )
        except Exception as exc:  # noqa: BLE001
            yield self._emit(
                HarnessError,
                component="session",
                message=f"loop_detector.observe fallo: {exc}",
                recoverable=True,
            )
            return

        action = decision.action
        if action == "warning":
            yield self._emit(
                LoopWarning,
                detector=decision.detector,
                signature=decision.signature,
                count=decision.count,
            )
        elif action == "corrective":
            prompt = self._corrective_builder.build(decision)
            self._messages.append(
                {"role": "system", "content": prompt},
            )
            yield self._emit(
                MessageCompleted, role="system", content=prompt,
            )
            yield self._emit(
                LoopCorrectivePrompt,
                prompt=prompt,
                detector=decision.detector,
            )
        elif action == "abort":
            self._step_failed = True
            self._cancel.set()
            yield self._emit(
                LoopAborted,
                detector=decision.detector,
                reason=decision.reason,
            )

    def _observe_health(self, step_index: int) -> Iterator[Event]:
        """Alimenta al HealthMonitor con las 4 metricas disponibles."""
        if self.health_monitor is None:
            return
        # Solo errores del step cuentan como findings por ahora.
        findings = self._step_findings_count
        coverage = 0.0  # sin parser de fases todavia
        tokens = 0  # no expuesto por el Protocol ModelClient
        errors = self._step_error_count
        try:
            result = self.health_monitor.step(
                findings_count=findings,
                coverage_score=coverage,
                total_tokens=tokens,
                error_count=errors,
            )
        except Exception as exc:  # noqa: BLE001
            yield self._emit(
                HarnessError,
                component="session",
                message=f"health_monitor.step fallo: {exc}",
                recoverable=True,
            )
            return
        if not result.signals:
            return
        yield self._emit(
            HealthSnapshot,
            step_index=result.step_index,
            findings_count=findings,
            coverage_score=coverage,
            total_tokens=tokens,
            error_count=errors,
            signals=list(result.signals),
        )

    # -- contexto enviado al modelo (P2#6) -------------------------

    _MCP_WILDCARD = "mcp__*"

    def _build_messages(self) -> list[dict[str, Any]]:
        """System prompt del agente + historial.

        El system prompt no se guarda en `_messages` para no
        duplicarlo en cada ronda ni meterlo en el fold de eventos.
        """
        prompt = self.config.agent.system_prompt or ""
        tools_block = self._tools_block()
        if tools_block:
            prompt = (
                prompt + "\n\n" + tools_block
                if prompt.strip()
                else tools_block
            )
        if not prompt:
            return list(self._messages)
        return [
            {"role": "system", "content": prompt},
            *self._messages,
        ]

    def _tools_block(self) -> str:
        """Lista explicita de tools disponibles para el modelo.

        Mitiga la hallucination de nombres de tool (auditoria
        Run OVERPAPER v2 2026-10-01: el modelo intento llamar a
        una tool "python" porque el phase spec mencionaba el
        comando `python -m py_compile`). Listar los nombres
        exactos da al modelo un ancla contra la que comparar.
        """
        defs = self._tool_definitions() or []
        names = sorted({
            str(d.get("function", {}).get("name", ""))
            for d in defs
            if isinstance(d, dict)
        } - {""})
        if not names:
            return ""
        return (
            "TOOLS DISPONIBLES (usa estos nombres exactos; "
            "no inventes otros): " + ", ".join(names)
        )

    def _is_tool_allowed(self, name: str) -> bool:
        """True si la tool esta en AgentSpec.allowed_tools.

        None = todas. Se acepta el wildcard "mcp__*" para los
        tools MCP, coherente con FilteredToolProvider.
        """
        allowed = self.config.agent.allowed_tools
        if allowed is None:
            return True
        if name in allowed:
            return True
        return (
            self._MCP_WILDCARD in allowed
            and name.startswith("mcp__")
        )

    def _tool_definitions(self) -> list[dict[str, Any]] | None:
        """Schemas de las tools ofrecidas al modelo, ya filtrados."""
        getter = getattr(self.tool_registry, "definitions", None)
        if not callable(getter):
            return None
        try:
            defs = list(getter())
        except Exception:  # noqa: BLE001
            return None
        out = [
            d for d in defs
            if isinstance(d, dict)
            and self._is_tool_allowed(
                str(d.get("function", {}).get("name", "")),
            )
        ]
        return out or None

    # -- emision -----------------------------------------------------

    def _emit(self, cls: type[Event], **kwargs: Any) -> Event:
        """Crea el evento, lo persiste si hay log, y devuelve el
        evento con el seq REAL del log (auditoria P2#15).

        P2#14: si `event_log.append` falla (DB caida, disco lleno,
        permisos), NO se propaga la excepcion. Antes, un fallo de
        append dentro de `_emit` escapaba de `step()`: el except
        re-emitia `HarnessError`, volvia a fallar en append, y la
        excepcion salia sin capturar. Ahora se loggea y se
        continua: el evento se emite en memoria aunque no quede
        persistido. El evento real se ve en las señales, el
        historial de la app sigue funcionando.
        """
        self._seq += 1
        event = cls(
            seq=self._seq,
            run_id=self.config.run_id,
            ts=_now_iso(),
            **kwargs,
        )
        if self.event_log is not None:
            try:
                real_seq = self.event_log.append(event)
                if real_seq > 0 and real_seq != event.seq:
                    event = _dc_replace(event, seq=real_seq)
            except Exception:  # noqa: BLE001
                _logger.exception(
                    "EventLog.append fallo; el evento no se persistio"
                )
        return event
