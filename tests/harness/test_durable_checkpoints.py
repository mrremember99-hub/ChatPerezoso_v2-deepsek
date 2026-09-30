"""S2-b: tests de CheckpointManager + IdempotencyRegistry."""
from __future__ import annotations

import threading

import pytest

from core.harness.durable import (
    CheckpointManager,
    CheckpointSnapshot,
    IdempotencyRegistry,
    idempotency_key,
)
from core.harness.policy import DurablePolicy


def _snap(run_id: str, step: int = 0, seq: int = 0) -> CheckpointSnapshot:
    return CheckpointSnapshot(
        run_id=run_id,
        last_event_seq=seq,
        step_index=step,
        messages_summary=[{"role": "user", "content": "x"}],
        tools_executed=[],
        loop_state={},
        correctives_count=0,
        verification_issues=[],
        agent_spec_hash="a" * 16,
        policy_hash="p" * 16,
    )


@pytest.fixture
def cm(tmp_path):
    m = CheckpointManager(tmp_path / "cp.sqlite")
    yield m
    m.close()


@pytest.fixture
def ir(tmp_path):
    r = IdempotencyRegistry(tmp_path / "ir.sqlite")
    yield r
    r.close()


# ── CheckpointManager ─────────────────────────────────────────────


def test_cm_save_load_roundtrip(cm):
    s = _snap("r1", step=3, seq=42)
    cid = cm.save(s)
    assert cid.startswith("cp_")
    got = cm.load(cid)
    assert got == s


def test_cm_load_inexistente(cm):
    assert cm.load("no-existe") is None


def test_cm_get_latest(cm):
    cm.save(_snap("r1", step=1, seq=10))
    cm.save(_snap("r1", step=2, seq=20))
    cm.save(_snap("r1", step=3, seq=30))
    latest = cm.get_latest("r1")
    assert latest is not None
    assert latest.step_index == 3
    assert latest.last_event_seq == 30


def test_cm_get_latest_run_inexistente(cm):
    assert cm.get_latest("nope") is None


def test_cm_count_y_list(cm):
    for i in range(4):
        cm.save(_snap("r1", step=i, seq=i * 10))
    assert cm.count("r1") == 4
    assert cm.count("r2") == 0
    assert len(cm.list_run("r1")) == 4


def test_cm_delete(cm):
    cid = cm.save(_snap("r1"))
    assert cm.delete(cid) is True
    assert cm.load(cid) is None
    assert cm.delete(cid) is False


def test_cm_retention_por_run(cm):
    for i in range(5):
        cm.save(_snap("r1", step=i, seq=i * 10))
    policy = DurablePolicy(keep_last_checkpoints=2)
    deleted = cm.apply_retention(policy)
    assert deleted == 3
    assert cm.count("r1") == 2
    assert cm.get_latest("r1").step_index == 4


def test_cm_retention_multiples_runs(cm):
    for i in range(4):
        cm.save(_snap("r1", step=i, seq=i))
    for i in range(4):
        cm.save(_snap("r2", step=i, seq=i))
    policy = DurablePolicy(keep_last_checkpoints=2)
    deleted = cm.apply_retention(policy)
    assert deleted == 4
    assert cm.count("r1") == 2
    assert cm.count("r2") == 2


# ── IdempotencyRegistry ──────────────────────────────────────────


def test_key_estable():
    k1 = idempotency_key("r1", 3, "c1")
    k2 = idempotency_key("r1", 3, "c1")
    k3 = idempotency_key("r1", 4, "c1")
    assert k1 == k2
    assert k1 != k3
    assert len(k1) == 24


def test_ir_mark_pending_completed(ir):
    k = idempotency_key("r1", 0, "c1")
    assert ir.get_state(k) is None
    ir.mark_pending(k, "r1", "tool_call", "c1")
    assert ir.get_state(k) == "pending"
    ir.mark_completed(k, {"output": "ok"})
    assert ir.get_state(k) == "completed"
    assert ir.get_result(k) == {"output": "ok"}


def test_ir_mark_failed(ir):
    k = idempotency_key("r1", 0, "c1")
    ir.mark_pending(k, "r1", "tool_call", "c1")
    ir.mark_failed(k)
    assert ir.get_state(k) == "failed"


def test_ir_list_pending(ir):
    k1 = idempotency_key("r1", 0, "c1")
    k2 = idempotency_key("r1", 1, "c2")
    k3 = idempotency_key("r1", 2, "c3")
    ir.mark_pending(k1, "r1", "tool_call", "c1")
    ir.mark_pending(k2, "r1", "tool_call", "c2")
    ir.mark_pending(k3, "r1", "tool_call", "c3")
    ir.mark_completed(k2, "done")
    pending = ir.list_pending("r1")
    assert k1 in pending
    assert k3 in pending
    assert k2 not in pending


def test_ir_clear_run(ir):
    for i in range(3):
        k = idempotency_key("r1", i, f"c{i}")
        ir.mark_pending(k, "r1", "tool_call", f"c{i}")
    assert ir.clear("r1") == 3
    assert ir.list_pending("r1") == []


def test_ir_result_none_si_pending(ir):
    k = idempotency_key("r1", 0, "c1")
    ir.mark_pending(k, "r1", "tool_call", "c1")
    assert ir.get_result(k) is None


def test_ir_threadsafe(tmp_path):
    r = IdempotencyRegistry(tmp_path / "ir.sqlite")
    errs: list[Exception] = []

    def worker(tid: int) -> None:
        try:
            for i in range(20):
                k = idempotency_key(f"r{tid}", i, f"c{i}")
                r.mark_pending(k, f"r{tid}", "tool_call", f"c{i}")
                r.mark_completed(k, i)
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    threads = [
        threading.Thread(target=worker, args=(i,)) for i in range(3)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)
    r.close()
    assert errs == []


# ── Context manager ──────────────────────────────────────────────


def test_cm_context_manager(tmp_path):
    with CheckpointManager(tmp_path / "c.sqlite") as m:
        m.save(_snap("r1"))
    assert (tmp_path / "c.sqlite").exists()
