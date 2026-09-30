"""S2-c: tests de fold_events, load_state, plan_resume."""
from __future__ import annotations

import pytest

from core.harness import events as ev
from core.harness.durable import (
    CheckpointManager,
    CheckpointSnapshot,
    EventLog,
    HarnessState,
    IdempotencyRegistry,
    Inconsistency,
    detect_inconsistencies,
    fold_events,
    idempotency_key,
    load_state,
    plan_resume,
    state_from_checkpoint,
)

# ── Helpers ───────────────────────────────────────────────────────


def _started(run_id: str = "r1", seq: int = 0) -> ev.RunStarted:
    return ev.RunStarted(
        seq=seq, run_id=run_id, ts="2026-09-30T00:00:00",
        user_message="hola", agent_name="A", model_name="M",
    )


def _step(seq: int, idx: int, run_id: str = "r1") -> ev.StepStarted:
    return ev.StepStarted(
        seq=seq, run_id=run_id, ts="2026-09-30T00:00:00",
        step_index=idx,
    )


def _step_end(seq: int, idx: int, run_id: str = "r1") -> ev.StepEnded:
    return ev.StepEnded(
        seq=seq, run_id=run_id, ts="2026-09-30T00:00:00",
        step_index=idx, outcome="ok",
    )


def _msg(seq: int, role: str = "assistant", content: str = "x") -> ev.MessageCompleted:
    return ev.MessageCompleted(
        seq=seq, run_id="r1", ts="2026-09-30T00:00:00",
        role=role, content=content,
    )


def _tool_req(seq: int, call_id: str = "c1") -> ev.ToolCallRequested:
    return ev.ToolCallRequested(
        seq=seq, run_id="r1", ts="2026-09-30T00:00:00",
        call_id=call_id, tool_name="t", arguments={},
        auto_approved=False,
    )


def _tool_done(seq: int, call_id: str = "c1") -> ev.ToolCallCompleted:
    return ev.ToolCallCompleted(
        seq=seq, run_id="r1", ts="2026-09-30T00:00:00",
        call_id=call_id, tool_name="t", status="ok",
        summary="ok", detail="", duration_ms=1,
    )


def _run_end(seq: int, reason: str = "completed") -> ev.RunEnded:
    return ev.RunEnded(
        seq=seq, run_id="r1", ts="2026-09-30T00:00:00",
        reason=reason, summary="",
    )


def _snap(
    run_id: str = "r1", step: int = 0, seq: int = 0,
    policy_hash: str = "", agent_spec_hash: str = "",
) -> CheckpointSnapshot:
    return CheckpointSnapshot(
        run_id=run_id,
        last_event_seq=seq,
        step_index=step,
        messages_summary=[],
        tools_executed=[],
        loop_state={},
        correctives_count=0,
        verification_issues=[],
        agent_spec_hash=agent_spec_hash,
        policy_hash=policy_hash,
    )


# ══════════════════ fold_events ═══════════════════════════════════


def test_fold_vacio():
    state = fold_events([])
    assert state.started is False
    assert state.ended is False
    assert state.current_step == -1
    assert state.messages == []


def test_fold_run_started():
    state = fold_events([_started()])
    assert state.started is True
    assert state.run_id == "r1"


def test_fold_steps_avanzan():
    events = [_step(1, 0), _step_end(2, 0), _step(3, 1), _step_end(4, 1)]
    state = fold_events(events)
    assert state.current_step == 1
    assert state.last_event_seq == 4


def test_fold_run_ended():
    events = [_started(), _run_end(seq=1, reason="completed")]
    state = fold_events(events)
    assert state.ended is True
    assert state.end_reason == "completed"


def test_fold_messages_limitadas():
    events = [_msg(seq=i, content=f"m{i}") for i in range(30)]
    state = fold_events(events)
    assert len(state.messages) == 20
    assert state.messages[-1]["content"] == "m29"


def test_fold_tool_requested_en_vuelo():
    events = [_tool_req(seq=1, call_id="c1")]
    state = fold_events(events)
    assert state.last_tool_call is not None
    assert state.last_tool_call["call_id"] == "c1"


def test_fold_tool_completed_limpia_en_vuelo():
    events = [
        _tool_req(seq=1, call_id="c1"),
        _tool_done(seq=2, call_id="c1"),
    ]
    state = fold_events(events)
    assert state.last_tool_call is None


def test_fold_tool_completed_distinto_no_limpia():
    """Si el call_id no coincide, queda el ultimo pending."""
    events = [
        _tool_req(seq=1, call_id="c1"),
        _tool_done(seq=2, call_id="c2"),
    ]
    state = fold_events(events)
    assert state.last_tool_call is not None
    assert state.last_tool_call["call_id"] == "c1"


def test_fold_confirmation_pending():
    req = ev.ConfirmationRequested(
        seq=1, run_id="r1", ts="t", call_id="c1",
        tool_name="t", arguments={}, reason="x",
    )
    state = fold_events([req])
    assert state.pending_confirmation == "c1"


def test_fold_confirmation_resolved_limpia():
    req = ev.ConfirmationRequested(
        seq=1, run_id="r1", ts="t", call_id="c1",
        tool_name="t", arguments={}, reason="x",
    )
    res = ev.ConfirmationResolved(
        seq=2, run_id="r1", ts="t", call_id="c1",
        approved=True, timeout=False,
    )
    state = fold_events([req, res])
    assert state.pending_confirmation is None


def test_fold_loop_correctives_cuenta():
    p = ev.LoopCorrectivePrompt(
        seq=1, run_id="r1", ts="t", prompt="x", detector="d",
    )
    state = fold_events([p, p, p])
    assert state.loop_correctives_count == 3


def test_fold_loop_aborted():
    a = ev.LoopAborted(
        seq=1, run_id="r1", ts="t", detector="d", reason="x",
    )
    state = fold_events([a])
    assert state.loop_aborted is True


def test_fold_desde_initial_preserva_hashes():
    """fold sobre initial debe preservar campos ya rellenados."""
    base = HarnessState(run_id="r1", policy_hash="abc")
    state = fold_events([_step(seq=1, idx=0)], initial=base)
    assert state.policy_hash == "abc"
    assert state.current_step == 0


# ══════════════════ state_from_checkpoint ════════════════════════


def test_state_from_checkpoint_basico():
    snap = _snap(step=5, seq=42, policy_hash="p1", agent_spec_hash="a1")
    state = state_from_checkpoint(snap)
    assert state.run_id == "r1"
    assert state.started is True
    assert state.current_step == 5
    assert state.last_event_seq == 42
    assert state.policy_hash == "p1"
    assert state.agent_spec_hash == "a1"


# ══════════════════ load_state ════════════════════════════════════


def test_load_state_sin_checkpoint(tmp_path):
    log = EventLog(tmp_path / "e.sqlite")
    cm = CheckpointManager(tmp_path / "cp.sqlite")
    try:
        log.append(_started())
        log.append(_step(seq=1, idx=0))
        state = load_state(log, cm, "r1")
        assert state.started is True
        assert state.current_step == 0
    finally:
        log.close()
        cm.close()


def test_load_state_con_checkpoint(tmp_path):
    """El checkpoint evita re-leer eventos anteriores."""
    log = EventLog(tmp_path / "e.sqlite")
    cm = CheckpointManager(tmp_path / "cp.sqlite")
    try:
        # 5 eventos antes del checkpoint.
        log.append(_started())
        for i in range(4):
            log.append(_step(seq=i + 1, idx=i))
        # Checkpoint tras el step 4.
        last_seq = list(log.read("r1"))[-1].seq
        cm.save(_snap(step=4, seq=last_seq))
        # 2 eventos despues.
        log.append(_step(seq=last_seq + 1, idx=5))
        log.append(_step(seq=last_seq + 2, idx=6))

        state = load_state(log, cm, "r1")
        assert state.current_step == 6
        assert state.started is True
    finally:
        log.close()
        cm.close()


def test_load_state_run_inexistente(tmp_path):
    log = EventLog(tmp_path / "e.sqlite")
    cm = CheckpointManager(tmp_path / "cp.sqlite")
    try:
        state = load_state(log, cm, "nope")
        assert state.started is False
    finally:
        log.close()
        cm.close()


# ══════════════════ detect_inconsistencies ═══════════════════════


def test_inconsistencias_vacio_sin_cambios():
    state = HarnessState(run_id="r1", policy_hash="p1",
                         agent_spec_hash="a1")
    out = detect_inconsistencies(
        state,
        current_agent_spec_hash="a1",
        current_policy_hash="p1",
    )
    assert out == []


def test_inconsistencias_policy_changed():
    state = HarnessState(run_id="r1", policy_hash="p1")
    out = detect_inconsistencies(
        state, current_policy_hash="p2",
    )
    assert len(out) == 1
    assert out[0].kind == "policy_changed"
    assert out[0].severity == "critical"


def test_inconsistencias_agent_changed():
    state = HarnessState(run_id="r1", agent_spec_hash="a1")
    out = detect_inconsistencies(
        state, current_agent_spec_hash="a2",
    )
    assert len(out) == 1
    assert out[0].kind == "agent_changed"


def test_inconsistencias_ignora_hashes_vacios():
    """Si el state no tiene hashes (checkpoint viejo), no alerta."""
    state = HarnessState(run_id="r1")
    out = detect_inconsistencies(
        state,
        current_policy_hash="p1",
        current_agent_spec_hash="a1",
    )
    assert out == []


# ══════════════════ plan_resume ═══════════════════════════════════


def test_plan_resume_nothing_si_termino():
    state = HarnessState(run_id="r1", started=True, ended=True,
                         end_reason="completed")
    plan = plan_resume(state)
    assert plan.action == "nothing"


def test_plan_resume_start_si_no_empezo():
    state = HarnessState(run_id="r1")
    plan = plan_resume(state)
    assert plan.action == "start"


def test_plan_resume_continue():
    state = HarnessState(run_id="r1", started=True, current_step=5)
    plan = plan_resume(state)
    assert plan.action == "continue"
    assert plan.from_step == 5


def test_plan_resume_resolve_pending(tmp_path):
    ir = IdempotencyRegistry(tmp_path / "ir.sqlite")
    try:
        k = idempotency_key("r1", 3, "c1")
        ir.mark_pending(k, "r1", "tool_call", "c1")
        state = HarnessState(run_id="r1", started=True, current_step=3)
        plan = plan_resume(state, ir)
        assert plan.action == "resolve_pending"
        assert k in plan.pending_ops
    finally:
        ir.close()


def test_plan_resume_abort_por_inconsistencia():
    state = HarnessState(run_id="r1", started=True, current_step=2)
    inc = [Inconsistency(
        kind="policy_changed", severity="critical",
        message="policy cambio",
    )]
    plan = plan_resume(state, inconsistencies=inc)
    assert plan.action == "abort"
    assert "policy" in plan.reason


def test_plan_resume_inconsistencia_warning_no_aborta():
    state = HarnessState(run_id="r1", started=True, current_step=2)
    inc = [Inconsistency(
        kind="model_changed", severity="warning",
        message="modelo cambio",
    )]
    plan = plan_resume(state, inconsistencies=inc)
    assert plan.action == "continue"
