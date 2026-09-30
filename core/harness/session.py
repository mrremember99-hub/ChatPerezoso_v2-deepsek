"""HarnessSession — orquestador principal del ciclo de agente.

Spec: docs/harness-v3.md §1, §4.

Estado de slices:
  S4-a (este)  — step() sin tool calls. ModelClient protocol.
  S4-b         — ciclo completo con tool calls + confirmaciones.
  S4-c         — integracion con LoopDetector + HealthMonitor +
                 EventLog + CheckpointManager.
"""
from __future__ import annotations

import threading
from collections.abc import Iterator
from datetime import UTC, datetime
from typing import Any

from core.harness.events import (
    Event,
    HarnessError,
    MessageCompleted,
    MessageDelta,
    RunEnded,
    RunStarted,
    StepEnded,
    StepStarted,
)
from core.harness.model import ModelClient
from core.harness.policy import HarnessConfig


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


class HarnessSession:
    """Sesion de agente. Orquesta el ciclo de un step.

    Uso:
        session = HarnessSession(config, model_client=client)
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
    ) -> None:
        self.config = config
        self.model_client = model_client
        self.tool_registry = tool_registry
        self.event_log = event_log
        self._seq = 0
        self._step_index = -1
        self._run_started = False
        self._messages: list[dict[str, Any]] = []
        self._cancel = threading.Event()
        self._errors: list[str] = []

    # -- API ---------------------------------------------------------

    def step(self, user_message: str) -> Iterator[Event]:
        """Ejecuta un step completo. Emite eventos segun ocurren."""
        self._cancel.clear()

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

        text_parts: list[str] = []
        tool_call: dict | None = None
        error: str | None = None

        try:
            for delta in self.model_client.chat(
                list(self._messages),
                tools=None,  # S4-a: sin tools todavia
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
        except Exception as exc:  # noqa: BLE001
            error = str(exc) or type(exc).__name__

        full_text = "".join(text_parts)
        if full_text:
            yield self._emit(
                MessageCompleted, role="assistant", content=full_text,
            )
            self._messages.append(
                {"role": "assistant", "content": full_text},
            )

        if error is not None:
            self._errors.append(error)
            yield self._emit(
                HarnessError,
                component="model",
                message=error,
                recoverable=False,
            )
            yield self._emit(
                StepEnded, step_index=step_index, outcome="failed",
            )
            yield self._emit(
                RunEnded, reason="error", summary=error,
            )
            return

        if tool_call is not None:
            msg = (
                "tool calls no soportadas en S4-a "
                "(planificado para S4-b)"
            )
            self._errors.append(msg)
            yield self._emit(
                HarnessError,
                component="model",
                message=msg,
                recoverable=True,
            )
            yield self._emit(
                StepEnded, step_index=step_index, outcome="failed",
            )
            return

        yield self._emit(
            StepEnded, step_index=step_index, outcome="ok",
        )

    def cancel(self) -> None:
        """Marca la cancelacion del step en curso."""
        self._cancel.set()

    def resume(self) -> Iterator[Event]:
        """Reanuda desde el ultimo checkpoint. S4-a: no-op."""
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
        """S4-a: no-op. Confirmaciones en S4-b."""

    # -- internos ----------------------------------------------------

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
