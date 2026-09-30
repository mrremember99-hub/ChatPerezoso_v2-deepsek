"""Grupo 1c-2b: P2#2b — plan_resume reconcilia last_tool_call."""
from __future__ import annotations

import pytest

from core.harness.durable import (
    HarnessState,
    IdempotencyRegistry,
    is_idempotent_tool,
    plan_resume,
)

# ── Heuristica is_idempotent_tool ───────────────────────────────


@pytest.mark.parametrize("name", [
    "leer_archivo",
    "listar_carpeta",
    "buscar_en_workspace",
    "git_status",
    "git_log",
    "git_diff",
    "git_show",
])
def test_idempotent_por_prefijo(name):
    assert is_idempotent_tool(name) is True


@pytest.mark.parametrize("name", [
    "escribir_archivo",
    "crear_archivo",
    "editar_archivo",
    "insertar_en_archivo",
    "borrar_archivo",
    "ejecutar_comando",
    "git_push",
    "git_commit",
])
def test_no_idempotent_por_prefijo(name):
    assert is_idempotent_tool(name) is False


# ── plan_resume: reconciliacion ─────────────────────────────────


def _state_with_inflight(
    *, call_id: str = "tc_abc", tool_name: str = "leer_archivo",
) -> HarnessState:
    st = HarnessState(run_id="r1", started=True, current_step=2)
    st.last_tool_call = {
        "call_id": call_id,
        "tool_name": tool_name,
        "arguments": {},
        "auto_approved": True,
    }
    return st


def test_plan_resume_idempotente_en_vuelo(tmp_path) -> None:
    """Tool read-only en vuelo: plan -> resolve_pending + idempotent=True."""
    ir = IdempotencyRegistry(tmp_path / "ir.sqlite")
    try:
        # Simulamos un crash: la op quedo pending sin completarse.
        ir.mark_pending("k1", "r1", "tool_call", "tc_abc")
        state = _state_with_inflight(
            call_id="tc_abc", tool_name="leer_archivo",
        )
        plan = plan_resume(state, ir)
        assert plan.action == "resolve_pending"
        assert plan.pending_call_id == "tc_abc"
        assert plan.idempotent is True
        assert plan.pending_ops == ["k1"]
        assert "reintentar" in plan.reason
    finally:
        ir.close()


def test_plan_resume_no_idempotente_en_vuelo(tmp_path) -> None:
    """Tool mutante en vuelo: plan -> resolve_pending + idempotent=False."""
    ir = IdempotencyRegistry(tmp_path / "ir.sqlite")
    try:
        ir.mark_pending("k1", "r1", "tool_call", "tc_xyz")
        state = _state_with_inflight(
            call_id="tc_xyz", tool_name="escribir_archivo",
        )
        plan = plan_resume(state, ir)
        assert plan.action == "resolve_pending"
        assert plan.pending_call_id == "tc_xyz"
        assert plan.idempotent is False
        assert "failed" in plan.reason or "side effects" in plan.reason
    finally:
        ir.close()


def test_plan_resume_sin_match_en_vuelo(tmp_path) -> None:
    """last_tool_call no coincide con ninguna op pending -> fallback."""
    ir = IdempotencyRegistry(tmp_path / "ir.sqlite")
    try:
        ir.mark_pending("k1", "r1", "tool_call", "otro_call_id")
        # last_tool_call apunta a un call_id distinto.
        state = _state_with_inflight(call_id="tc_no_match")
        plan = plan_resume(state, ir)
        assert plan.action == "resolve_pending"
        assert plan.pending_call_id == ""
        assert "1 op(s) pending" in plan.reason
    finally:
        ir.close()


def test_plan_resume_sin_inflight(tmp_path) -> None:
    """Pending sin last_tool_call -> fallback generico."""
    ir = IdempotencyRegistry(tmp_path / "ir.sqlite")
    try:
        ir.mark_pending("k1", "r1", "tool_call", "tc_abc")
        state = HarnessState(run_id="r1", started=True, current_step=2)
        plan = plan_resume(state, ir)
        assert plan.action == "resolve_pending"
        assert plan.pending_call_id == ""
        assert plan.pending_ops == ["k1"]
    finally:
        ir.close()


# ── plan_resume: casos sin pending ──────────────────────────────


def test_plan_resume_continue_sin_pending(tmp_path) -> None:
    ir = IdempotencyRegistry(tmp_path / "ir.sqlite")
    try:
        state = HarnessState(run_id="r1", started=True, current_step=3)
        plan = plan_resume(state, ir)
        assert plan.action == "continue"
        assert plan.from_step == 3
    finally:
        ir.close()


def test_plan_resume_nothing_si_terminado(tmp_path) -> None:
    ir = IdempotencyRegistry(tmp_path / "ir.sqlite")
    try:
        state = HarnessState(
            run_id="r1", started=True, ended=True,
            end_reason="completed",
        )
        plan = plan_resume(state, ir)
        assert plan.action == "nothing"
    finally:
        ir.close()


def test_plan_resume_start_si_no_empezo() -> None:
    state = HarnessState(run_id="r1")
    plan = plan_resume(state, None)
    assert plan.action == "start"


# ── pending_call_ids ────────────────────────────────────────────


def test_pending_call_ids_devuelve_pares(tmp_path) -> None:
    ir = IdempotencyRegistry(tmp_path / "ir.sqlite")
    try:
        ir.mark_pending("k1", "r1", "tool_call", "tc_a")
        ir.mark_pending("k2", "r1", "tool_call", "tc_b")
        ir.mark_completed("k2", "ok")
        pairs = ir.pending_call_ids("r1")
        assert pairs == [("k1", "tc_a")]
    finally:
        ir.close()
