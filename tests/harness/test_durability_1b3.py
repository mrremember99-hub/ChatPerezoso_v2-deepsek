"""Grupo 1b-3: P2#4 — close() explícito + RunEnded fuera del step."""
from __future__ import annotations

import pathlib
from collections.abc import Iterator

from core.harness.durable import EventLog
from core.harness.model import ModelDelta
from core.harness.policy import AgentSpec, HarnessConfig, ModelSpec
from core.harness.session import HarnessSession


class _Model:
    def __init__(
        self, *, script: list | None = None, raise_exc=None,
    ) -> None:
        self._script = list(script or [])
        self._raise = raise_exc

    def chat(
        self, messages, *, tools=None, stream=True, cancel_event=None,
    ) -> Iterator[ModelDelta]:
        if self._raise is not None:
            raise self._raise
        if self._script:
            n, a = self._script.pop(0)
            yield ModelDelta(
                kind="tool_call",
                tool_call={"name": n, "arguments": a},
            )
            return
        yield ModelDelta(kind="text", text="ok")
        yield ModelDelta(kind="done")


class _Reg:
    def requires_confirmation(self, _n: str) -> bool:
        return False

    def call(self, *_a, **_kw) -> str:
        return "resultado"


def _cfg(tmp_path: pathlib.Path) -> HarnessConfig:
    return HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="m"),
        agent=AgentSpec(name="a"),
    )


# ── step no emite RunEnded ──────────────────────────────────────


def test_step_ok_no_emite_run_ended(tmp_path) -> None:
    s = HarnessSession(_cfg(tmp_path), model_client=_Model())
    events = list(s.step("hola"))
    kinds = [e.kind for e in events]
    assert "run_ended" not in kinds


def test_step_error_no_emite_run_ended(tmp_path) -> None:
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_Model(raise_exc=RuntimeError("boom")),
    )
    events = list(s.step("hola"))
    kinds = [e.kind for e in events]
    assert "run_ended" not in kinds


# ── close emite RunEnded ────────────────────────────────────────


def test_close_completed(tmp_path) -> None:
    s = HarnessSession(_cfg(tmp_path), model_client=_Model())
    list(s.step("hola"))
    events = list(s.close())
    assert len(events) == 1
    assert events[0].kind == "run_ended"
    assert events[0].reason == "completed"


def test_close_reason_custom(tmp_path) -> None:
    s = HarnessSession(_cfg(tmp_path), model_client=_Model())
    list(s.step("hola"))
    events = list(s.close(reason="cancelled", summary="por el usuario"))
    assert events[0].reason == "cancelled"
    assert events[0].summary == "por el usuario"


def test_close_idempotente(tmp_path) -> None:
    s = HarnessSession(_cfg(tmp_path), model_client=_Model())
    list(s.step("hola"))
    first = list(s.close())
    second = list(s.close())
    assert len(first) == 1
    assert second == []


def test_close_sin_step(tmp_path) -> None:
    """close() sin haber hecho ningún step también emite RunEnded.

    Puede pasar si el usuario abre la app y la cierra sin chatear.
    """
    s = HarnessSession(_cfg(tmp_path), model_client=_Model())
    events = list(s.close())
    assert events[0].kind == "run_ended"


def test_close_persiste_en_event_log(tmp_path) -> None:
    log = EventLog(tmp_path / "e.sqlite")
    try:
        s = HarnessSession(
            _cfg(tmp_path),
            model_client=_Model(),
            event_log=log,
        )
        list(s.step("hola"))
        list(s.close())
        kinds = [e.kind for e in log.read("r1")]
        assert "run_ended" in kinds
    finally:
        log.close()


def test_close_tras_multiples_steps(tmp_path) -> None:
    s = HarnessSession(_cfg(tmp_path), model_client=_Model())
    list(s.step("uno"))
    list(s.step("dos"))
    list(s.step("tres"))
    events = list(s.close())
    assert len(events) == 1
    assert events[0].kind == "run_ended"
