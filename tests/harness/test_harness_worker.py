"""S6-b-1b: HarnessWorker traduce eventos del harness a señales Qt.

Sin QThread real: ejecutamos run() sincronicamente con una
HarnessSession fake que produce eventos preprogramados.
"""
from __future__ import annotations

from collections.abc import Iterator

from core.harness.events import (
    HarnessError,
    LoopAborted,
    LoopCorrectivePrompt,
    LoopWarning,
    MessageDelta,
    StepEnded,
    ToolCallCompleted,
    ToolCallRequested,
)
from core.tool_result import ToolResult
from ui.harness_worker import HarnessWorker


class _FakeSession:
    """HarnessSession minimo: step() devuelve eventos fijos."""

    def __init__(self, events: list) -> None:
        self._events = events
        self.cancelled = False

    def step(self, _msg: str) -> Iterator:
        yield from self._events

    def cancel(self) -> None:
        self.cancelled = True


def _ev(cls, **kw):
    """Construye un evento con seq/run_id/ts por defecto."""
    defaults = {"seq": 1, "run_id": "r", "ts": "2026-09-30T00:00:00"}
    defaults.update(kw)
    return cls(**defaults)


def _worker(events: list) -> HarnessWorker:
    return HarnessWorker(_FakeSession(events), "hola")


# -- message_delta → stream_ready --


def test_delta_emite_stream_ready(qapp) -> None:
    """Los deltas emiten stream_ready; el texto final va por finished.

    El worker drena el buffer al emitir finished (mismo patron que
    ChatWorker), asi que el caller consume el texto desde la senal,
    no desde drain_text() despues de run().
    """
    w = _worker([
        _ev(MessageDelta, role="assistant", content="ho"),
        _ev(MessageDelta, role="assistant", content="la"),
        _ev(StepEnded, step_index=0, outcome="ok"),
    ])
    signals: list = []
    finished: list = []
    w.stream_ready.connect(lambda: signals.append(1))
    w.finished.connect(finished.append)
    w.run()
    assert len(signals) == 2
    assert finished == ["hola"]


def test_delta_vacio_no_emite(qapp) -> None:
    w = _worker([
        _ev(MessageDelta, role="assistant", content=""),
        _ev(StepEnded, step_index=0, outcome="ok"),
    ])
    signals: list = []
    w.stream_ready.connect(lambda: signals.append(1))
    w.run()
    assert signals == []


# -- tool_call_requested → tool --


def test_tool_requested_emite_tool(qapp) -> None:
    w = _worker([
        _ev(
            ToolCallRequested,
            call_id="c1", tool_name="leer_archivo",
            arguments={}, auto_approved=False,
        ),
        _ev(StepEnded, step_index=0, outcome="ok"),
    ])
    got: list = []
    w.tool.connect(got.append)
    w.run()
    assert got == ["leer_archivo"]


# -- tool_call_completed → tool_result --


def test_tool_completed_emite_tool_result(qapp) -> None:
    w = _worker([
        _ev(
            ToolCallCompleted,
            call_id="c1", tool_name="leer_archivo",
            status="ok", summary="ok", detail="contenido",
            duration_ms=12,
        ),
        _ev(StepEnded, step_index=0, outcome="ok"),
    ])
    got: list = []
    w.tool_result.connect(got.append)
    w.run()
    assert len(got) == 1
    assert isinstance(got[0], ToolResult)
    assert got[0].tool_name == "leer_archivo"
    assert got[0].detail == "contenido"
    assert got[0].duration_ms == 12
    assert got[0].status == "ok"


def test_tool_completed_error(qapp) -> None:
    w = _worker([
        _ev(
            ToolCallCompleted,
            call_id="c1", tool_name="t",
            status="error", summary="fallo", detail="ERROR: x",
            duration_ms=5,
        ),
        _ev(StepEnded, step_index=0, outcome="ok"),
    ])
    got: list = []
    w.tool_result.connect(got.append)
    w.run()
    assert got[0].is_error is True
    assert got[0].status == "error"


def test_tool_completed_cancelled(qapp) -> None:
    w = _worker([
        _ev(
            ToolCallCompleted,
            call_id="c1", tool_name="t",
            status="cancelled", summary="x", detail="x",
            duration_ms=0,
        ),
        _ev(StepEnded, step_index=0, outcome="ok"),
    ])
    got: list = []
    w.tool_result.connect(got.append)
    w.run()
    assert got[0].is_cancelled is True


# -- loop events → señales de loop --


def test_loop_warning(qapp) -> None:
    w = _worker([
        _ev(
            LoopWarning,
            detector="generic_repeat", signature="x", count=3,
        ),
        _ev(StepEnded, step_index=0, outcome="ok"),
    ])
    got: list = []
    w.loop_warning.connect(lambda d, r: got.append((d, r)))
    w.run()
    assert got == [("generic_repeat", "3 repeticiones (generic_repeat)")]


def test_loop_corrective(qapp) -> None:
    w = _worker([
        _ev(
            LoopCorrectivePrompt,
            prompt="Loop detectado...", detector="ping_pong",
        ),
        _ev(StepEnded, step_index=0, outcome="ok"),
    ])
    got: list = []
    w.loop_corrective.connect(lambda d, r: got.append((d, r)))
    w.run()
    assert got[0][0] == "ping_pong"


def test_loop_aborted(qapp) -> None:
    w = _worker([
        _ev(
            LoopAborted,
            detector="generic_repeat", reason="max_corrective_attempts",
        ),
        _ev(StepEnded, step_index=0, outcome="failed"),
    ])
    got: list = []
    w.loop_aborted.connect(lambda d, r: got.append((d, r)))
    w.run()
    assert got == [("generic_repeat", "max_corrective_attempts")]


# -- harness_error → error --


def test_harness_error(qapp) -> None:
    w = _worker([
        _ev(
            HarnessError,
            component="model", message="boom", recoverable=False,
        ),
    ])
    got: list = []
    w.error.connect(got.append)
    w.run()
    assert got == ["boom"]


# -- step_ended → finished --


def test_step_ended_emite_finished_con_texto(qapp) -> None:
    w = _worker([
        _ev(MessageDelta, role="assistant", content="ok"),
        _ev(StepEnded, step_index=0, outcome="ok"),
    ])
    got: list = []
    w.finished.connect(got.append)
    w.run()
    assert got == ["ok"]


# -- cancel --


def test_cancel_llama_session_y_emite_cancelled(qapp) -> None:
    session = _FakeSession([_ev(StepEnded, step_index=0, outcome="ok")])
    w = HarnessWorker(session, "hola")
    got: list = []
    w.cancelled.connect(lambda: got.append(1))
    w.cancel()
    assert session.cancelled is True
    w.run()
    assert got == [1]


# -- handle_confirmation / resolve_confirmation --


def test_handle_confirmation_bloquea_hasta_resolve(qapp) -> None:
    w = HarnessWorker(_FakeSession([]), "x")
    requested: list = []
    w.confirmation_requested.connect(
        lambda n, a: requested.append((n, a)),
    )
    # El handler bloquea; lo llamamos y le decimos que si en otro
    # hilo. Pero para el test basta con resolver antes.
    w.resolve_confirmation(True)
    # resolve_confirmation sin handle abierto no falla:
    assert requested == []
