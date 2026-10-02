"""Eventos del núcleo del agente (v3).

Append-only, inmutables, serializables. Sin EventLog persistente:
solo viajan por señales hacia la UI. `seq` es un contador local
de sesión.

Solo eventos que la UI consume. Sin health, sin loop detection,
sin checkpoints, sin UnknownEvent.
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, ClassVar


@dataclass(frozen=True, kw_only=True)
class Event:
    """Evento base. Todos llevan seq/run_id/ts."""

    seq: int
    run_id: str
    ts: str
    kind: ClassVar[str] = "event"

    _registry: ClassVar[dict[str, type["Event"]]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        kind = cls.__dict__.get("kind")
        if isinstance(kind, str) and kind:
            Event._registry[kind] = cls

    def to_dict(self) -> dict[str, Any]:
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        d["kind"] = type(self).kind
        return d

    @classmethod
    def registry(cls) -> dict[str, type["Event"]]:
        return dict(cls._registry)


# ── Ciclo de vida ──────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class RunStarted(Event):
    kind: ClassVar[str] = "run_started"
    user_message: str
    agent_name: str
    model_name: str


@dataclass(frozen=True, kw_only=True)
class RunEnded(Event):
    kind: ClassVar[str] = "run_ended"
    reason: str  # "completed" | "cancelled" | "error"
    summary: str


@dataclass(frozen=True, kw_only=True)
class StepStarted(Event):
    kind: ClassVar[str] = "step_started"
    step_index: int


@dataclass(frozen=True, kw_only=True)
class StepEnded(Event):
    kind: ClassVar[str] = "step_ended"
    step_index: int
    outcome: str  # "ok" | "failed" | "cancelled"


# ── Streaming del modelo ───────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class MessageDelta(Event):
    kind: ClassVar[str] = "message_delta"
    role: str
    content: str


@dataclass(frozen=True, kw_only=True)
class MessageCompleted(Event):
    kind: ClassVar[str] = "message_completed"
    role: str
    content: str


# ── Tool calls ─────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class ToolCallRequested(Event):
    kind: ClassVar[str] = "tool_call_requested"
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    auto_approved: bool


@dataclass(frozen=True, kw_only=True)
class ToolCallCompleted(Event):
    kind: ClassVar[str] = "tool_call_completed"
    call_id: str
    tool_name: str
    status: str  # "ok" | "error" | "cancelled"
    summary: str
    detail: str
    duration_ms: int


@dataclass(frozen=True, kw_only=True)
class ConfirmationRequested(Event):
    kind: ClassVar[str] = "confirmation_requested"
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    reason: str


@dataclass(frozen=True, kw_only=True)
class ConfirmationResolved(Event):
    kind: ClassVar[str] = "confirmation_resolved"
    call_id: str
    approved: bool
    timeout: bool


# ── Verificación de completion ─────────────────────────────

@dataclass(frozen=True, kw_only=True)
class VerificationRun(Event):
    kind: ClassVar[str] = "verification_run"
    call_id: str
    target: str
    issues: list[dict[str, Any]]


# ── Errores ────────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class AgentError(Event):
    kind: ClassVar[str] = "agent_error"
    component: str  # "model" | "tool" | "session"
    message: str
    recoverable: bool
