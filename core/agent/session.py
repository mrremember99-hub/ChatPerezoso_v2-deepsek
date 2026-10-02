"""AgentSession — loop modelo ↔ tools con gate de confirmación.

Núcleo del agente (v3). Sin durable, sin health, sin loop
detection, sin idempotencia, sin resume. Eventos solo en memoria.
"""
from __future__ import annotations

import hashlib
import inspect
import logging
import threading
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from typing import Any

from core.approval import is_auto_approved

from .events import (
    AgentError,
    ConfirmationRequested,
    ConfirmationResolved,
    Event,
    MessageCompleted,
    MessageDelta,
    RunEnded,
    RunStarted,
    StepEnded,
    StepStarted,
    ToolCallCompleted,
    ToolCallRequested,
    VerificationRun,
)
from .model import ModelClient
from .policy import AgentConfig

_logger = logging.getLogger(__name__)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class AgentSession:
    """Sesión del agente. Ejecuta un step (modelo + tools)."""

    _WRITE_TOOLS = frozenset({
        "crear_archivo", "escribir_archivo",
        "editar_archivo", "insertar_en_archivo",
    })
    _MCP_WILDCARD = "mcp__*"

    def __init__(
        self,
        config: AgentConfig,
        *,
        model_client: ModelClient,
        tool_registry: Any = None,
        confirmation_handler: Callable[..., bool] | None = None,
        command_allowed: Callable[[str], bool] | None = None,
        verificador_hook: Callable[[str], str] | None = None,
    ) -> None:
        self.config = config
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.confirmation_handler = confirmation_handler
        self.command_allowed = command_allowed
        self.verificador_hook = verificador_hook

        self._seq = 0
        self._step_index = -1
        self._run_started = False
        self._run_ended = False
        self._messages: list[dict[str, Any]] = []
        self._cancel = threading.Event()
        self._errors: list[str] = []
        self._step_failed = False
        self._step_in_progress = False
        self._step_tools_executed: list[str] = []

    # ── API pública ────────────────────────────────────────

    def load_history(
        self, messages: list[dict[str, Any]],
    ) -> None:
        """Precarga historial conversacional.

        Solo roles user/assistant/tool. Los system se descartan
        (van por AgentSpec.system_prompt).
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
        """Ejecuta un step. No reentrante. Fallo tras close()."""
        # P4#1: no aceptar steps tras close().
        if self._run_ended:
            raise RuntimeError(
                "AgentSession.step() llamado tras close()",
            )
        if self._step_in_progress:
            raise RuntimeError(
                "AgentSession.step() no es reentrante",
            )
        self._step_in_progress = True
        try:
            yield from self._step_impl(user_message)
        finally:
            self._step_in_progress = False
            self._cancel.clear()

    def cancel(self) -> None:
        self._cancel.set()

    def close(
        self,
        reason: str = "completed",
        summary: str = "",
    ) -> Iterator[Event]:
        """Cierra el run. Idempotente."""
        if self._run_ended:
            return
        self._run_ended = True
        yield self._emit(RunEnded, reason=reason, summary=summary)

    def health(self) -> dict[str, Any]:
        return {
            "run_id": self.config.run_id,
            "messages": len(self._messages),
            "steps": self._step_index + 1,
            "errors": len(self._errors),
        }

    # ── Implementación del step ────────────────────────────

    def _step_impl(self, user_message: str) -> Iterator[Event]:
        self._step_failed = False
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
        yield self._emit(
            MessageCompleted, role="user", content=user_message,
        )

        if self._cancel.is_set():
            yield self._emit(
                StepEnded,
                step_index=step_index,
                outcome="cancelled",
            )
            return

        fatal: str | None = None
        fatal_is_cancel = False
        try:
            yield from self._agent_loop(step_index)
        except Exception as exc:  # noqa: BLE001
            fatal = str(exc) or type(exc).__name__
            # P4#2: si el cancel esta seteado es cancelacion
            # (OllamaCancelled), no error del modelo.
            fatal_is_cancel = self._cancel.is_set()

        if fatal is not None and not fatal_is_cancel:
            self._errors.append(fatal)
            yield self._emit(
                AgentError,
                component="model",
                message=fatal,
                recoverable=False,
            )
            yield self._emit(
                StepEnded, step_index=step_index, outcome="failed",
            )
            return

        if (
            self.config.completion_verification_enabled
            and not self._step_failed
            and not self._cancel.is_set()
        ):
            yield from self._verify_completion(
                step_index, user_message,
            )

        if self._step_failed:
            outcome = "failed"
        elif self._cancel.is_set():
            outcome = "cancelled"
        else:
            outcome = "ok"
        yield self._emit(
            StepEnded, step_index=step_index, outcome=outcome,
        )

    def _agent_loop(self, step_index: int) -> Iterator[Event]:
        """Loop modelo → tools → modelo hasta que el modelo pare."""
        for round_index in range(self.config.max_tool_rounds):
            text_parts: list[str] = []
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
                return

            if self._cancel.is_set():
                return

            for ordinal, tc in enumerate(tool_calls):
                yield from self._execute_tool_call(
                    tc, step_index, round_index, ordinal,
                )
                if self._cancel.is_set() or self._step_failed:
                    return

        msg = (
            f"max_tool_rounds={self.config.max_tool_rounds} superado"
        )
        self._errors.append(msg)
        self._step_failed = True
        yield self._emit(
            AgentError,
            component="session",
            message=msg,
            recoverable=True,
        )

    def _make_call_id(
        self, step_index: int, round_index: int, ordinal: int,
    ) -> str:
        raw = (
            f"{self.config.run_id}|{step_index}|"
            f"{round_index}|{ordinal}"
        ).encode()
        return "tc_" + hashlib.sha256(raw).hexdigest()[:12]

    def _build_reason(
        self, name: str, arguments: dict,
    ) -> str:
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

    def _call_handler(
        self, name: str, arguments: dict, *, reason: str = "",
    ) -> bool:
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

    def _execute_tool_call(
        self,
        tool_call: dict,
        step_index: int,
        round_index: int = 0,
        ordinal: int = 0,
    ) -> Iterator[Event]:
        if not isinstance(tool_call, dict):
            yield self._emit(
                AgentError,
                component="session",
                message=f"tool_call malformado: {tool_call!r}",
                recoverable=True,
            )
            self._step_failed = True
            return

        name = str(tool_call.get("name", ""))
        raw_args = tool_call.get("arguments", {})
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
                AgentError,
                component="session",
                message=msg,
                recoverable=True,
            )
            self._step_failed = True
            return

        if not name:
            yield self._emit(
                AgentError,
                component="session",
                message="tool_call sin 'name'",
                recoverable=True,
            )
            self._step_failed = True
            return

        call_id = self._make_call_id(
            step_index, round_index, ordinal,
        )

        if not self._is_tool_allowed(name):
            msg = (
                f"ERROR: herramienta no permitida para este agente: "
                f"{name}"
            )
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
            self._step_failed = True
            return

        requires = False
        if self.tool_registry is not None:
            try:
                requires = bool(
                    self.tool_registry.requires_confirmation(name),
                )
            except Exception:  # noqa: BLE001
                # P4#5: fail-closed. Antes una excepcion aqui
                # dejaba requires=False y la tool se ejecutaba
                # sin confirmacion.
                requires = True

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
            self._step_failed = True
            return

        if denial_reason is not None:
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
            self._step_failed = True
            return

        if not auto_approved:
            result = (
                "OPERACIÓN CANCELADA POR EL USUARIO: la tool "
                "requiere confirmación y fue denegada."
            )
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
            # P4#14: denegacion del usuario = cancelacion,
            # no fallo del step. El worker emitira cancelled.
            self._cancel.set()
            return

        # P4#12: re-chequear cancel justo antes de invocar.
        # Un cancel que llega tras la aprobacion pero antes
        # de call() ejecutaria la tool igualmente.
        if self._cancel.is_set():
            result = (
                "OPERACIÓN CANCELADA: cancelado antes de "
                "ejecutar la herramienta."
            )
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
            return

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

        if (
            status == "ok"
            and name in self._WRITE_TOOLS
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

    def _verify_completion(
        self, step_index: int, user_message: str,
    ) -> Iterator[Event]:
        from .completion import CompletionVerifier, parse_phases

        try:
            phases = parse_phases(user_message)
        except Exception:  # noqa: BLE001
            return
        if not phases:
            return

        phase = phases[-1]
        verifier = CompletionVerifier()
        try:
            result = verifier.verify(
                phase,
                executed_tools=list(self._step_tools_executed),
            )
        except Exception:  # noqa: BLE001
            return

        # P4#3: no emitir VerificationRun cuando no hay comando
        # real de verificación. El CompletionVerifier no ejecuta
        # `verification_command`; sin él, emitir un evento vacío
        # con status="verified" da falsa sensación de verificación.
        if (
            result.status == "verified"
            and not phase.verification_command
        ):
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

    # ── Contexto enviado al modelo ─────────────────────────

    def _build_messages(self) -> list[dict[str, Any]]:
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

    # ── Emisión ────────────────────────────────────────────

    def _emit(self, cls: type[Event], **kwargs: Any) -> Event:
        self._seq += 1
        return cls(
            seq=self._seq,
            run_id=self.config.run_id,
            ts=_now_iso(),
            **kwargs,
        )
