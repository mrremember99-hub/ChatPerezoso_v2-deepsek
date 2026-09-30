"""S4-c-mini: LoopDetector + HealthMonitor en HarnessSession."""
from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

from core.harness.health import HealthMonitor
from core.harness.loop import LoopDetector
from core.harness.model import ModelDelta
from core.harness.policy import (
    AgentSpec,
    HarnessConfig,
    HealthPolicy,
    LoopPolicy,
    ModelSpec,
)
from core.harness.session import HarnessSession


class _ScriptedModel:
    def __init__(self, rounds: list[list[ModelDelta]]) -> None:
        self.rounds = rounds

    def chat(
        self,
        messages: list[dict],
        *,
        tools=None,
        stream: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[ModelDelta]:
        if not self.rounds:
            return iter([])
        yield from self.rounds.pop(0)


class _FakeRegistry:
    def __init__(self, *, results: dict[str, str] | None = None) -> None:
        self._results = results or {}
        self.calls: list[tuple] = []

    def requires_confirmation(self, name: str) -> bool:
        return False

    def call(self, name, arguments, **kwargs):
        self.calls.append((name, dict(arguments)))
        return self._results.get(name, f"ok:{name}")


def _config(tmp_path: Path, **kw) -> HarnessConfig:
    return HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="fake"),
        agent=AgentSpec(name="test"),
        **kw,
    )


def _call(name: str, args: dict | None = None) -> ModelDelta:
    return ModelDelta(
        kind="tool_call",
        tool_call={"name": name, "arguments": args or {}},
    )


def _text(s: str) -> ModelDelta:
    return ModelDelta(kind="text", text=s)


# ── Sin detector: comportamiento identico ──────────────────────


def test_sin_detector_ni_monitor_no_cambia(tmp_path):
    """Sin loop_detector ni health_monitor, S4-b intacto."""
    model = _ScriptedModel([[_text("hola")]])
    s = HarnessSession(
        _config(tmp_path),
        model_client=model,
        tool_registry=_FakeRegistry(),
    )
    events = list(s.step("hola"))
    kinds = [e.kind for e in events]
    assert "loop_warning" not in kinds
    assert "loop_corrective_prompt" not in kinds
    assert "loop_aborted" not in kinds
    assert "health_snapshot" not in kinds
    assert kinds[-1] == "step_ended"
    assert next(
        e for e in events if e.kind == "step_ended"
    ).outcome == "ok"


# ── LoopDetector integrado ─────────────────────────────────────


def test_loop_warning_a_las_3_repeticiones(tmp_path):
    """3 tool calls iguales con mismo result -> warning."""
    rounds = [[_call("t", {"x": 1}), ] for _ in range(4)]
    rounds.append([_text("fin")])
    model = _ScriptedModel(rounds)
    reg = _FakeRegistry(results={"t": "mismo-resultado"})
    detector = LoopDetector(LoopPolicy())
    s = HarnessSession(
        _config(tmp_path),
        model_client=model,
        tool_registry=reg,
        loop_detector=detector,
    )
    events = list(s.step("repite"))
    kinds = [e.kind for e in events]
    assert "loop_warning" in kinds or "loop_corrective_prompt" in kinds


def test_loop_corrective_inyecta_system(tmp_path):
    """A las 5 repeticiones inyecta prompt correctivo."""
    rounds = [[_call("t", {"x": 1})] for _ in range(6)]
    model = _ScriptedModel(rounds)
    reg = _FakeRegistry(results={"t": "mismo"})
    detector = LoopDetector(LoopPolicy())
    s = HarnessSession(
        _config(tmp_path),
        model_client=model,
        tool_registry=reg,
        loop_detector=detector,
    )
    list(s.step("repite"))
    # Se ha inyectado al menos un mensaje system con el prompt.
    systems = [
        m for m in s._messages
        if m["role"] == "system" and "Loop detectado" in m["content"]
    ]
    assert systems, "no se inyecto prompt correctivo"


def test_loop_abort_cancela_y_falla(tmp_path):
    """A las 8 repeticiones aborta y el step queda failed."""
    rounds = [[_call("t", {"x": 1})] for _ in range(10)]
    model = _ScriptedModel(rounds)
    reg = _FakeRegistry(results={"t": "mismo"})
    detector = LoopDetector(
        LoopPolicy(generic_repeat=(3, 5, 8)),
    )
    s = HarnessSession(
        _config(tmp_path),
        model_client=model,
        tool_registry=reg,
        loop_detector=detector,
    )
    events = list(s.step("repite"))
    kinds = [e.kind for e in events]
    assert "loop_aborted" in kinds
    assert s._cancel.is_set()
    ended = next(e for e in events if e.kind == "step_ended")
    assert ended.outcome == "failed"


# ── HealthMonitor integrado ────────────────────────────────────


def test_health_emite_snapshot_con_señales(tmp_path):
    """3 errores consecutivos -> thrash -> HealthSnapshot."""
    rounds = [[_call("t")] for _ in range(4)]
    model = _ScriptedModel(rounds)
    reg = _FakeRegistry(results={"t": "ERROR: siempre falla"})
    monitor = HealthMonitor(
        HealthPolicy(thrash_error_threshold=1),
    )
    s = HarnessSession(
        _config(tmp_path),
        model_client=model,
        tool_registry=reg,
        health_monitor=monitor,
    )
    events = list(s.step("falla"))
    kinds = [e.kind for e in events]
    assert "health_snapshot" in kinds
    snap = next(e for e in events if e.kind == "health_snapshot")
    assert "thrash" in snap.signals


def test_health_sin_señales_no_emite(tmp_path):
    """Sin problemas, no emite HealthSnapshot."""
    model = _ScriptedModel([[_text("ok")]])
    monitor = HealthMonitor(HealthPolicy())
    s = HarnessSession(
        _config(tmp_path),
        model_client=model,
        tool_registry=_FakeRegistry(),
        health_monitor=monitor,
    )
    events = list(s.step("ok"))
    kinds = [e.kind for e in events]
    assert "health_snapshot" not in kinds
