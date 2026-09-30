"""Grupo 2a: P2#10 — cancel() efectivo."""
from __future__ import annotations

import pathlib
import threading
from collections.abc import Iterator

from core.harness.model import ModelDelta
from core.harness.policy import AgentSpec, HarnessConfig, ModelSpec
from core.harness.session import HarnessSession


class _Model:
    def __init__(self, script: list | None = None) -> None:
        self.script = list(script or [])

    def chat(
        self, messages, *, tools=None, stream=True, cancel_event=None,
    ) -> Iterator[ModelDelta]:
        if self.script:
            n, a = self.script.pop(0)
            yield ModelDelta(
                kind="tool_call",
                tool_call={"name": n, "arguments": a},
            )
            return
        yield ModelDelta(kind="text", text="fin")
        yield ModelDelta(kind="done")


class _Reg:
    def __init__(self) -> None:
        self.called: list[str] = []

    def requires_confirmation(self, _n: str) -> bool:
        return False

    def call(
        self, name, args, *, allow_destructive=False, cancel_event=None,
    ) -> str:
        self.called.append(name)
        return "ok"


def _cfg(tmp_path: pathlib.Path) -> HarnessConfig:
    return HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="m"),
        agent=AgentSpec(name="a"),
    )


# ── cancel antes de step ────────────────────────────────────────


def test_cancel_antes_de_step(tmp_path) -> None:
    """Un cancel() llamado antes de abrir el step es respetado:
    el modelo no se llama, outcome=cancelled."""
    reg = _Reg()
    model = _Model()
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=model,
        tool_registry=reg,
    )
    s.cancel()
    events = list(s.step("hola"))
    kinds = [e.kind for e in events]
    assert "step_started" in kinds
    assert "message_completed" in kinds  # role=user
    ended = next(e for e in events if e.kind == "step_ended")
    assert ended.outcome == "cancelled"


def test_cancel_durante_modelo_no_ejecuta_tool(tmp_path) -> None:
    """Si el usuario cancela mientras el modelo genera, no se
    ejecuta la tool."""
    class _SlowModel:
        def __init__(self, session_ref: list) -> None:
            self._session_ref = session_ref

        def chat(
            self, messages, *, tools=None, stream=True,
            cancel_event=None,
        ) -> Iterator[ModelDelta]:
            # Simulamos que el usuario cancela durante el stream.
            self._session_ref[0].cancel()
            yield ModelDelta(
                kind="tool_call",
                tool_call={"name": "t", "arguments": {}},
            )

    ref: list = []
    reg = _Reg()
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_SlowModel(ref),
        tool_registry=reg,
    )
    ref.append(s)
    events = list(s.step("hola"))
    assert reg.called == []
    ended = next(e for e in events if e.kind == "step_ended")
    assert ended.outcome == "cancelled"


def test_flag_limpiado_tras_step(tmp_path) -> None:
    """Tras un step (cancelado o no), _cancel queda limpio para
    el siguiente step."""
    s = HarnessSession(_cfg(tmp_path), model_client=_Model())
    s.cancel()
    list(s.step("uno"))
    assert not s._cancel.is_set()
    # El siguiente step corre normal.
    events = list(s.step("dos"))
    ended = next(e for e in events if e.kind == "step_ended")
    assert ended.outcome == "ok"


def test_outcome_ok_sigue_siendo_ok(tmp_path) -> None:
    """Sin cancel, outcome = ok."""
    s = HarnessSession(_cfg(tmp_path), model_client=_Model())
    events = list(s.step("hola"))
    ended = next(e for e in events if e.kind == "step_ended")
    assert ended.outcome == "ok"


def test_cancel_idempotente(tmp_path) -> None:
    """Llamar cancel() dos veces no rompe."""
    s = HarnessSession(_cfg(tmp_path), model_client=_Model())
    s.cancel()
    s.cancel()
    events = list(s.step("hola"))
    ended = next(e for e in events if e.kind == "step_ended")
    assert ended.outcome == "cancelled"
