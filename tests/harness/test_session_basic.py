"""S4-a: tests de HarnessSession.step() sin tool calls."""
from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

from core.harness.durable import EventLog
from core.harness.model import ModelDelta
from core.harness.policy import (
    AgentSpec,
    HarnessConfig,
    ModelSpec,
)
from core.harness.session import HarnessSession

# ── Helpers ────────────────────────────────────────────────────────


class _FakeModel:
    """ModelClient que devuelve deltas predefinidos."""

    def __init__(
        self,
        deltas: list[ModelDelta] | None = None,
        *,
        raise_exc: Exception | None = None,
    ) -> None:
        self.deltas = deltas or []
        self.raise_exc = raise_exc
        self.calls: list[dict] = []

    def chat(
        self,
        messages: list[dict],
        *,
        tools=None,
        stream: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[ModelDelta]:
        self.calls.append({
            "messages": [dict(m) for m in messages],
            "tools": tools,
        })
        if self.raise_exc is not None:
            raise self.raise_exc
        yield from self.deltas


def _config(
    *,
    run_id: str = "r1",
    tmp_path: Path | None = None,
) -> HarnessConfig:
    storage = (tmp_path or Path("/tmp")) / "store"
    return HarnessConfig(
        run_id=run_id,
        workspace_root=(tmp_path or Path("/tmp")) / "ws",
        storage_dir=storage,
        model=ModelSpec(name="fake"),
        agent=AgentSpec(name="test"),
    )


# ── Streaming normal ──────────────────────────────────────────────


def test_step_emite_deltas_y_completed(tmp_path):
    model = _FakeModel([
        ModelDelta(kind="text", text="ho"),
        ModelDelta(kind="text", text="la"),
        ModelDelta(kind="done"),
    ])
    s = HarnessSession(_config(tmp_path=tmp_path), model_client=model)
    events = list(s.step("hi"))

    kinds = [e.kind for e in events]
    assert kinds[0] == "run_started"
    assert kinds[1] == "step_started"
    assert kinds.count("message_delta") == 2
    assert "message_completed" in kinds
    assert kinds[-1] == "step_ended"

    completed = next(e for e in events if e.kind == "message_completed")
    assert completed.content == "hola"
    ended = next(e for e in events if e.kind == "step_ended")
    assert ended.outcome == "ok"


def test_step_guarda_mensajes_en_history(tmp_path):
    model = _FakeModel([ModelDelta(kind="text", text="ok")])
    s = HarnessSession(_config(tmp_path=tmp_path), model_client=model)
    list(s.step("hola"))
    assert len(s._messages) == 2
    assert s._messages[0] == {"role": "user", "content": "hola"}
    assert s._messages[1] == {"role": "assistant", "content": "ok"}


def test_run_started_solo_en_primer_step(tmp_path):
    model = _FakeModel([ModelDelta(kind="text", text="x")])
    s = HarnessSession(_config(tmp_path=tmp_path), model_client=model)
    first = list(s.step("a"))
    second = list(s.step("b"))
    assert any(e.kind == "run_started" for e in first)
    assert not any(e.kind == "run_started" for e in second)


# ── Secuencia de seqs ────────────────────────────────────────────


def test_seqs_monotonos(tmp_path):
    model = _FakeModel([ModelDelta(kind="text", text="x")])
    s = HarnessSession(_config(tmp_path=tmp_path), model_client=model)
    events = list(s.step("hola"))
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)
    assert seqs[0] == 1


# ── Tool calls (no soportadas en S4-a) ───────────────────────────


def test_tool_call_sin_registry_emite_completed_con_error(tmp_path):
    """S4-b: sin tool_registry, la tool call no se ejecuta; se emite
    ToolCallCompleted con status='error' y el mensaje se guarda
    como rol tool en el historial."""
    model = _FakeModel([
        ModelDelta(kind="text", text="voy a "),
        ModelDelta(
            kind="tool_call",
            tool_call={"name": "leer_archivo", "arguments": {}},
        ),
    ])
    s = HarnessSession(_config(tmp_path=tmp_path), model_client=model)
    events = list(s.step("leé x"))

    kinds = [e.kind for e in events]
    assert "tool_call_requested" in kinds
    assert "tool_call_completed" in kinds
    completed = next(
        e for e in events if e.kind == "tool_call_completed"
    )
    assert completed.status == "error"
    assert "tool_registry" in completed.detail


def test_excepcion_modelo_emite_error_y_run_ended(tmp_path):
    model = _FakeModel(raise_exc=RuntimeError("boom"))
    s = HarnessSession(_config(tmp_path=tmp_path), model_client=model)
    events = list(s.step("x"))

    kinds = [e.kind for e in events]
    assert "harness_error" in kinds
    assert "run_ended" in kinds
    err = next(e for e in events if e.kind == "harness_error")
    assert "boom" in err.message
    ended = next(e for e in events if e.kind == "run_ended")
    assert ended.reason == "error"


# ── Cancel ────────────────────────────────────────────────────────


def test_cancel_marca_event(tmp_path):
    s = HarnessSession(
        _config(tmp_path=tmp_path),
        model_client=_FakeModel([]),
    )
    assert not s._cancel.is_set()
    s.cancel()
    assert s._cancel.is_set()


# ── Durable integration ──────────────────────────────────────────


def test_event_log_recibe_eventos(tmp_path):
    log = EventLog(tmp_path / "e.sqlite")
    try:
        model = _FakeModel([ModelDelta(kind="text", text="ok")])
        s = HarnessSession(
            _config(tmp_path=tmp_path),
            model_client=model,
            event_log=log,
        )
        list(s.step("hola"))
        stored = list(log.read("r1"))
        kinds = [e.kind for e in stored]
        assert "run_started" in kinds
        assert "message_completed" in kinds
        assert "step_ended" in kinds
    finally:
        log.close()


def test_events_replay_desde_event_log(tmp_path):
    log = EventLog(tmp_path / "e.sqlite")
    try:
        model = _FakeModel([ModelDelta(kind="text", text="ok")])
        s = HarnessSession(
            _config(tmp_path=tmp_path),
            model_client=model,
            event_log=log,
        )
        list(s.step("hola"))
        replayed = list(s.events())
        assert len(replayed) >= 4
        assert replayed[0].kind == "run_started"
    finally:
        log.close()


# ── health ────────────────────────────────────────────────────────


def test_health_reporta_estado(tmp_path):
    model = _FakeModel([ModelDelta(kind="text", text="x")])
    s = HarnessSession(_config(tmp_path=tmp_path), model_client=model)
    h0 = s.health()
    assert h0["steps"] == 0
    list(s.step("a"))
    h1 = s.health()
    assert h1["steps"] == 1
    assert h1["messages"] == 2
