"""Eventos del harness. Append-only, inmutables, serializables.

Spec: docs/harness-v3.md §2.

Cada subclase fija `kind` como ClassVar. El metodo to_dict() anade
el kind al dict plano para persistir en el event log (SQLite).
"""
from __future__ import annotations

from dataclasses import dataclass, field, fields
from typing import Any, ClassVar


@dataclass(frozen=True, kw_only=True)
class Event:
    """Evento base. Todos los eventos llevan seq/run_id/ts."""

    seq: int
    run_id: str
    ts: str
    kind: ClassVar[str] = "event"

    # Registro de subclases por kind. Se rellena automaticamente
    # via __init_subclass__ al declarar cada subclase.
    _registry: ClassVar[dict[str, type[Event]]] = {}

    def __init_subclass__(cls, **kwargs: Any) -> None:
        super().__init_subclass__(**kwargs)
        kind = cls.__dict__.get("kind")
        if isinstance(kind, str) and kind:
            Event._registry[kind] = cls

    def to_dict(self) -> dict[str, Any]:
        """Representacion plana para el event log."""
        d = {f.name: getattr(self, f.name) for f in fields(self)}
        d["kind"] = type(self).kind
        return d

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> Event:
        """Reconstruye un evento desde su dict plano. Tolerante
        (auditoria externa P2#17).

        Reglas:
          - kind desconocido -> UnknownEvent con el raw guardado.
          - campos desconocidos -> ignorados.
          - campos faltantes o mal formados -> UnknownEvent.

        Antes: cualquier `kind` o campo raro lanzaba excepcion y
        rompia `fold_events` de todo el run. Un cambio de
        dataclass o un evento añadido por otra version dejaba el
        log ilegible a posteriori.
        """
        kind = data.get("kind")
        seq = data.get("seq", 0)
        run_id = data.get("run_id", "")
        ts = data.get("ts", "")
        if not isinstance(kind, str) or not kind:
            return UnknownEvent(
                seq=int(seq) if isinstance(seq, int) else 0,
                run_id=str(run_id),
                ts=str(ts),
                original_kind="",
                raw=dict(data),
            )
        sub = cls._registry.get(kind)
        if sub is None:
            return UnknownEvent(
                seq=int(seq) if isinstance(seq, int) else 0,
                run_id=str(run_id),
                ts=str(ts),
                original_kind=kind,
                raw=dict(data),
            )
        # Filtrar campos desconocidos: si la dataclass gano o
        # perdio campos entre versiones, el payload viejo puede
        # traer extras.
        allowed = {f.name for f in fields(sub)}
        payload = {
            k: v for k, v in data.items()
            if k != "kind" and k in allowed
        }
        try:
            return sub(**payload)
        except (TypeError, ValueError):
            return UnknownEvent(
                seq=int(seq) if isinstance(seq, int) else 0,
                run_id=str(run_id),
                ts=str(ts),
                original_kind=kind,
                raw=dict(data),
            )

    @classmethod
    def registry(cls) -> dict[str, type[Event]]:
        """Copia del registro kind -> subclase."""
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
    reason: str           # "completed" | "cancelled" | "error" | "loop_aborted"
    summary: str


@dataclass(frozen=True, kw_only=True)
class StepStarted(Event):
    kind: ClassVar[str] = "step_started"
    step_index: int


@dataclass(frozen=True, kw_only=True)
class StepEnded(Event):
    kind: ClassVar[str] = "step_ended"
    step_index: int
    outcome: str          # "ok" | "failed" | "loop_broken"


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
    status: str           # "ok" | "error" | "cancelled"
    summary: str
    detail: str
    duration_ms: int


@dataclass(frozen=True, kw_only=True)
class ConfirmationRequested(Event):
    kind: ClassVar[str] = "confirmation_requested"
    call_id: str
    tool_name: str
    arguments: dict[str, Any]
    reason: str           # "allowlist" | "destructive" | "policy"


@dataclass(frozen=True, kw_only=True)
class ConfirmationResolved(Event):
    kind: ClassVar[str] = "confirmation_resolved"
    call_id: str
    approved: bool
    timeout: bool


# ── Verificacion ───────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class VerificationRun(Event):
    kind: ClassVar[str] = "verification_run"
    call_id: str
    target: str
    issues: list[dict[str, Any]]


@dataclass(frozen=True, kw_only=True)
class VerificationAttached(Event):
    kind: ClassVar[str] = "verification_attached"
    call_id: str
    summary: str


# ── Loop detection ─────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class LoopWarning(Event):
    kind: ClassVar[str] = "loop_warning"
    detector: str
    signature: str
    count: int


@dataclass(frozen=True, kw_only=True)
class LoopCorrectivePrompt(Event):
    kind: ClassVar[str] = "loop_corrective_prompt"
    prompt: str
    detector: str


@dataclass(frozen=True, kw_only=True)
class LoopAborted(Event):
    kind: ClassVar[str] = "loop_aborted"
    detector: str
    reason: str


# ── Contexto ───────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class ContextCompacted(Event):
    kind: ClassVar[str] = "context_compacted"
    tokens_before: int
    tokens_after: int


# ── Checkpoints ────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class CheckpointSaved(Event):
    kind: ClassVar[str] = "checkpoint_saved"
    checkpoint_id: str
    step_index: int
    snapshot: dict[str, Any]


# ── Health ─────────────────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class HealthSnapshot(Event):
    kind: ClassVar[str] = "health_snapshot"
    step_index: int
    findings_count: int
    coverage_score: float
    total_tokens: int
    error_count: int
    signals: list[str]


# ── Errores y warnings ─────────────────────────────────────

@dataclass(frozen=True, kw_only=True)
class HarnessError(Event):
    kind: ClassVar[str] = "harness_error"
    component: str        # "model" | "tool" | "storage"
    message: str
    recoverable: bool


@dataclass(frozen=True, kw_only=True)
class HarnessWarning(Event):
    kind: ClassVar[str] = "harness_warning"
    warning_kind: str     # "resume_with_changes" | ...
    details: list[dict[str, Any]]


# ── Fallback para eventos no reconocidos ─────────────────────────

@dataclass(frozen=True, kw_only=True)
class UnknownEvent(Event):
    """Envoltorio para eventos cuyo `kind` no esta registrado.

    No deberia aparecer en uso normal. Sirve para que
    `from_dict` no rompa `fold_events` si el log tiene eventos
    de otra version del harness (auditoria P2#17).
    """

    kind: ClassVar[str] = "unknown"
    original_kind: str = ""
    raw: dict[str, Any] = field(default_factory=dict)
