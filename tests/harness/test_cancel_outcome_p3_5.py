"""P3#5: cancel no emite finished."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from core.harness.events import StepEnded


def _ended(outcome: str) -> StepEnded:
    return StepEnded(
        seq=1,
        run_id="r1",
        ts="2026-10-02T00:00:00+00:00",
        step_index=1,
        outcome=outcome,
    )
from core.harness.policy import (
    AgentSpec,
    HarnessConfig,
    ModelSpec,
)
from core.harness.session import HarnessSession
from ui.harness_worker import HarnessWorker


class _NoopModel:
    def chat(self, *a, **kw):
        return iter([])


def _make_worker(tmp_path):
    cfg = HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="fake"),
        agent=AgentSpec(name="test"),
    )
    s = HarnessSession(cfg, model_client=_NoopModel())
    return HarnessWorker(s, "hola")


def test_cancel_no_emite_finished(tmp_path):
    w = _make_worker(tmp_path)
    calls = {"finished": 0, "cancelled": 0, "error": 0}
    w.finished.connect(lambda *_: calls.__setitem__(
        "finished", calls["finished"] + 1))
    w.cancelled.connect(lambda: calls.__setitem__(
        "cancelled", calls["cancelled"] + 1))
    w.error.connect(lambda *_: calls.__setitem__(
        "error", calls["error"] + 1))

    w._on_step_ended(_ended("cancelled"))
    assert calls["finished"] == 0


def test_ok_emite_finished(tmp_path):
    w = _make_worker(tmp_path)
    calls = {"finished": 0}
    w.finished.connect(lambda *_: calls.__setitem__(
        "finished", calls["finished"] + 1))

    w._on_step_ended(_ended("ok"))
    assert calls["finished"] == 1


def test_failed_sin_error_previo_emite_error(tmp_path):
    w = _make_worker(tmp_path)
    calls = {"error": 0, "finished": 0}
    w.error.connect(lambda *_: calls.__setitem__(
        "error", calls["error"] + 1))
    w.finished.connect(lambda *_: calls.__setitem__(
        "finished", calls["finished"] + 1))

    w._on_step_ended(_ended("failed"))
    assert calls["error"] == 1
    assert calls["finished"] == 0


def test_failed_con_error_previo_no_duplica(tmp_path):
    w = _make_worker(tmp_path)
    w._error_emitted = True
    calls = {"error": 0}
    w.error.connect(lambda *_: calls.__setitem__(
        "error", calls["error"] + 1))

    w._on_step_ended(_ended("failed"))
    assert calls["error"] == 0


# -- P3#7: auto_approved propagado ----------------------------------


def test_auto_approved_emite_signal(tmp_path):
    """ToolCallRequested con auto_approved=True emite la señal."""
    from core.harness.events import ToolCallRequested
    w = _make_worker(tmp_path)
    calls: list[str] = []
    w.tool_auto_approved.connect(lambda name: calls.append(name))

    event = ToolCallRequested(
        seq=1,
        run_id="r1",
        ts="2026-10-02T00:00:00+00:00",
        call_id="tc_1",
        tool_name="escribir_archivo",
        arguments={"path": "a.py"},
        auto_approved=True,
    )
    w._on_tool_requested(event)
    assert calls == ["escribir_archivo"]


def test_no_auto_approved_no_emite_signal(tmp_path):
    from core.harness.events import ToolCallRequested
    w = _make_worker(tmp_path)
    calls: list[str] = []
    w.tool_auto_approved.connect(lambda name: calls.append(name))

    event = ToolCallRequested(
        seq=1,
        run_id="r1",
        ts="2026-10-02T00:00:00+00:00",
        call_id="tc_1",
        tool_name="escribir_archivo",
        arguments={"path": "a.py"},
        auto_approved=False,
    )
    w._on_tool_requested(event)
    assert calls == []


# -- P3#9: metadata.arguments propagada ------------------------------


def test_metadata_arguments_llega_al_tool_result(tmp_path):
    """ToolCallRequested cachea args; ToolCallCompleted los adjunta."""
    from core.harness.events import (
        ToolCallCompleted, ToolCallRequested,
    )
    w = _make_worker(tmp_path)
    results: list = []
    w.tool_result.connect(lambda r: results.append(r))

    w._on_tool_requested(ToolCallRequested(
        seq=1, run_id="r1", ts="2026-10-02T00:00:00+00:00",
        call_id="tc_1",
        tool_name="escribir_archivo",
        arguments={"path": "a.py", "content": "x"},
        auto_approved=False,
    ))
    w._on_tool_completed(ToolCallCompleted(
        seq=2, run_id="r1", ts="2026-10-02T00:00:00+00:00",
        call_id="tc_1",
        tool_name="escribir_archivo",
        status="ok",
        summary="ok",
        detail="ok",
        duration_ms=1,
    ))
    assert len(results) == 1
    assert results[0].metadata["arguments"]["path"] == "a.py"


def test_metadata_vacia_si_no_hay_call_id(tmp_path):
    from core.harness.events import ToolCallCompleted
    w = _make_worker(tmp_path)
    results: list = []
    w.tool_result.connect(lambda r: results.append(r))

    w._on_tool_completed(ToolCallCompleted(
        seq=2, run_id="r1", ts="2026-10-02T00:00:00+00:00",
        call_id="tc_huerfano",
        tool_name="escribir_archivo",
        status="ok",
        summary="ok",
        detail="ok",
        duration_ms=1,
    ))
    assert results[0].metadata == {}


def test_cache_no_crece_indefinidamente(tmp_path):
    """Al consumir el call_id, se saca del dict."""
    from core.harness.events import (
        ToolCallCompleted, ToolCallRequested,
    )
    w = _make_worker(tmp_path)
    w._on_tool_requested(ToolCallRequested(
        seq=1, run_id="r1", ts="2026-10-02T00:00:00+00:00",
        call_id="tc_1",
        tool_name="t", arguments={"x": 1}, auto_approved=False,
    ))
    assert "tc_1" in w._pending_args
    w._on_tool_completed(ToolCallCompleted(
        seq=2, run_id="r1", ts="2026-10-02T00:00:00+00:00",
        call_id="tc_1", tool_name="t", status="ok",
        summary="", detail="", duration_ms=0,
    ))
    assert "tc_1" not in w._pending_args
