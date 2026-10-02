"""P3#5: cancel no emite finished."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from core.harness.events import StepEnded
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

    w._on_step_ended(StepEnded(step_index=1, outcome="cancelled"))
    assert calls["finished"] == 0


def test_ok_emite_finished(tmp_path):
    w = _make_worker(tmp_path)
    calls = {"finished": 0}
    w.finished.connect(lambda *_: calls.__setitem__(
        "finished", calls["finished"] + 1))

    w._on_step_ended(StepEnded(step_index=1, outcome="ok"))
    assert calls["finished"] == 1


def test_failed_sin_error_previo_emite_error(tmp_path):
    w = _make_worker(tmp_path)
    calls = {"error": 0, "finished": 0}
    w.error.connect(lambda *_: calls.__setitem__(
        "error", calls["error"] + 1))
    w.finished.connect(lambda *_: calls.__setitem__(
        "finished", calls["finished"] + 1))

    w._on_step_ended(StepEnded(step_index=1, outcome="failed"))
    assert calls["error"] == 1
    assert calls["finished"] == 0


def test_failed_con_error_previo_no_duplica(tmp_path):
    w = _make_worker(tmp_path)
    w._error_emitted = True
    calls = {"error": 0}
    w.error.connect(lambda *_: calls.__setitem__(
        "error", calls["error"] + 1))

    w._on_step_ended(StepEnded(step_index=1, outcome="failed"))
    assert calls["error"] == 0
