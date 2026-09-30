"""HarnessSession — orquestador principal del ciclo de agente.

Spec: docs/harness-v3.md §1, §4.

Estado de slices:
  S4-a         — step() sin tool calls. ModelClient protocol.
  S4-b (este)  — ciclo completo con tool calls + idempotencia.
  S4-c         — integracion con LoopDetector + HealthMonitor +
                 confirmaciones bloqueantes.
"""
from __future__ import annotations

import secrets
import threading
import time
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from core.harness.durable import (
    IdempotencyRegistry,
    idempotency_key,
)
from core.harness.events import (
    Event,
    HarnessError,
    HealthSnapshot,
    LoopAborted,
    LoopCorrectivePrompt,
    LoopWarning,
    MessageCompleted,
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
    ) -> None:
        self.config = config
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.event_log = event_log
        self.idempotency = idempotency
        self.loop_detector = loop_detector
        self.health_monitor = health_monitor
        self._corrective_builder = CorrectivePromptBuilder()
        # Contadores para health monitoring dentro del step en curso.
        self._step_error_count = 0
        self._step_findings_count = 0
        self._seq = 0
        self._step_index = -1
        self._run_started = False
        self._messages: list[dict[str, Any]] = []
        self._cancel = threading.Event()
        self._errors: list[str] = []
        self._step_failed = False

    # -- API ---------------------------------------------------------

    def step(self, user_message: str) -> Iterator[Event]:
        """Ejecuta un step completo (modelo + tool calls)."""
        self._cancel.clear()
        self._step_failed = False
        self._step_error_count = 0
        self._step_findings_count = 0

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
        self._messages.append(
            {"role": "user", "content": user_message},
        )
        yield self._emit(StepStarted, step_index=step_index)

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
            yield self._emit(
                RunEnded, reason="error", summary=fatal,
            )
            return

        outcome = "failed" if self._step_failed else "ok"
        yield self._emit(
            StepEnded, step_index=step_index, outcome=outcome,
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

    # -- loop interno ------------------------------------------------

    def _agent_loop(self, step_index: int) -> Iterator[Event]:
        """Itera modelo -> tool calls -> modelo hasta que el modelo
        termine o se exceda max_tool_rounds.
        """
        for _round in range(self.config.max_tool_rounds):
            text_parts: list[str] = []
            tool_call: dict | None = None

            for delta in self.model_client.chat(
                list(self._messages),
                tools=None,
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
                    tool_call = delta.tool_call
                    break
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
                self._messages.append(
                    {"role": "assistant", "content": full_text},
                )

            if tool_call is None:
                yield from self._observe_health(step_index)
                return  # el modelo no pide nada mas: step ok

            yield from self._execute_tool_call(tool_call, step_index)

            # Si el loop detector aborto, cortamos el ciclo.
            if self._cancel.is_set() or self._step_failed:
                yield from self._observe_health(step_index)
                return

        # Excedido max_tool_rounds.
        msg = (
            f"max_tool_rounds={self.config.max_tool_rounds} superado"
        )
        self._errors.append(msg)
        self._step_failed = True
        yield self._emit(
            HarnessError,
            component="session",
            message=msg,
            recoverable=True,
        )

    def _execute_tool_call(
        self, tool_call: dict, step_index: int,
    ) -> Iterator[Event]:
        """Ejecuta una tool call y emite Requested/Completed."""
        if not isinstance(tool_call, dict):
            yield self._emit(
                HarnessError,
                component="session",
                message=f"tool_call malformado: {tool_call!r}",
                recoverable=True,
            )
            return

        name = str(tool_call.get("name", ""))
        raw_args = tool_call.get("arguments", {})
        arguments: dict = (
            dict(raw_args) if isinstance(raw_args, dict) else {}
        )
        if not name:
            yield self._emit(
                HarnessError,
                component="session",
                message="tool_call sin 'name'",
                recoverable=True,
            )
            return

        call_id = f"tc_{secrets.token_hex(6)}"

        # Gate minimo S4-b.
        requires = False
        if self.tool_registry is not None:
            try:
                requires = bool(
                    self.tool_registry.requires_confirmation(name),
                )
            except Exception:  # noqa: BLE001
                requires = False

        auto_approved = (not requires) or self.config.auto_approve

        yield self._emit(
            ToolCallRequested,
            call_id=call_id,
            tool_name=name,
            arguments=arguments,
            auto_approved=auto_approved,
        )

        # Gate minimo: si requiere y no hay auto_approve, denegar.
        if requires and not self.config.auto_approve:
            result = (
                "OPERACIÓN CANCELADA: la tool requiere confirmación "
                "explícita del usuario. El harness S4-b no soporta "
                "confirmaciones bloqueantes todavía; la tool no se "
                "ha ejecutado."
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
            self._messages.append({"role": "tool", "content": result})
            return

        # Idempotencia.
        key: str | None = None
        if self.idempotency is not None:
            key = idempotency_key(
                self.config.run_id, step_index, call_id,
            )
            state = self.idempotency.get_state(key)
            if state == "completed":
                cached = self.idempotency.get_result(key)
                result = str(cached) if cached is not None else ""
                yield self._emit(
                    ToolCallCompleted,
                    call_id=call_id,
                    tool_name=name,
                    status="ok",
                    summary=result[:120],
                    detail=result,
                    duration_ms=0,
                )
                self._messages.append(
                    {"role": "tool", "content": result},
                )
                return
            self.idempotency.mark_pending(
                key, self.config.run_id, "tool_call", call_id,
            )

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

        yield self._emit(
            ToolCallCompleted,
            call_id=call_id,
            tool_name=name,
            status=status,
            summary=result[:120],
            detail=result,
            duration_ms=duration_ms,
        )
        self._messages.append({"role": "tool", "content": result})

        # S4-c-mini: observar el par (tool, args, result) por si es
        # un bucle. Los errores tambien cuentan para health.
        if status == "error":
            self._step_error_count += 1
        yield from self._observe_loop(
            name, arguments, result, status,
        )

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

    # -- emision -----------------------------------------------------

    def _emit(self, cls: type[Event], **kwargs: Any) -> Event:
        self._seq += 1
        event = cls(
            seq=self._seq,
            run_id=self.config.run_id,
            ts=_now_iso(),
            **kwargs,
        )
        if self.event_log is not None:
            self.event_log.append(event)
        return event
