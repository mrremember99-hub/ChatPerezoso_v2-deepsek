"""Grupo 1a: call_id determinista (P2#1) + seq del log (P2#15)."""
from __future__ import annotations

import pathlib
from collections.abc import Iterator
from typing import Any

from core.harness.durable import (
    EventLog,
    IdempotencyRegistry,
)
from core.harness.model import ModelDelta
from core.harness.policy import AgentSpec, HarnessConfig, ModelSpec
from core.harness.session import HarnessSession


class _Model:
    """Modelo que emite tool_calls programadas y luego para."""

    def __init__(self, script: list[tuple[str, dict]]) -> None:
        self.script = list(script)

    def chat(
        self, messages, *, tools=None, stream=True, cancel_event=None,
    ) -> Iterator[ModelDelta]:
        if not self.script:
            yield ModelDelta(kind="text", text="fin")
            yield ModelDelta(kind="done")
            return
        name, args = self.script.pop(0)
        yield ModelDelta(
            kind="tool_call",
            tool_call={"name": name, "arguments": args},
        )


class _Reg:
    def __init__(self) -> None:
        self.executed: list[str] = []

    def requires_confirmation(self, _name: str) -> bool:
        return False

    def call(
        self, name, args, *, allow_destructive=False, cancel_event=None,
    ) -> str:
        self.executed.append(name)
        return f"ok:{name}"


def _config(tmp_path: pathlib.Path, *, run_id: str = "r1") -> HarnessConfig:
    return HarnessConfig(
        run_id=run_id,
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="fake"),
        agent=AgentSpec(name="test"),
    )


# ── call_id determinista ────────────────────────────────────────


def test_make_call_id_estable() -> None:
    s = HarnessSession(_config(pathlib.Path("/tmp")),
                       model_client=_Model([]))
    a = s._make_call_id(0, 0, 0)
    b = s._make_call_id(0, 0, 0)
    assert a == b
    assert a.startswith("tc_")
    assert len(a) == len("tc_") + 12


def test_make_call_id_distinto_por_posicion() -> None:
    s = HarnessSession(_config(pathlib.Path("/tmp")),
                       model_client=_Model([]))
    ids = {
        s._make_call_id(0, 0, 0),
        s._make_call_id(0, 0, 1),
        s._make_call_id(0, 1, 0),
        s._make_call_id(1, 0, 0),
    }
    assert len(ids) == 4


def test_make_call_id_distinto_por_run() -> None:
    s1 = HarnessSession(_config(pathlib.Path("/tmp"), run_id="r1"),
                        model_client=_Model([]))
    s2 = HarnessSession(_config(pathlib.Path("/tmp"), run_id="r2"),
                        model_client=_Model([]))
    assert s1._make_call_id(0, 0, 0) != s2._make_call_id(0, 0, 0)


# ── Idempotencia real (P2#1) ────────────────────────────────────


def test_idempotencia_reintento_no_re_ejecuta(tmp_path) -> None:
    """Mismo (run, step, round, ordinal) tras crash -> cached."""
    ir = IdempotencyRegistry(tmp_path / "ir.sqlite")
    try:
        # Primera ejecucion: escribe completed con la clave del
        # call_id determinista.
        model1 = _Model([("t", {"x": 1})])
        reg1 = _Reg()
        s1 = HarnessSession(
            _config(tmp_path),
            model_client=model1,
            tool_registry=reg1,
            idempotency=ir,
        )
        list(s1.step("x"))
        assert reg1.executed == ["t"]

        # Segunda ejecucion: mismo run_id, mismo step, mismo
        # round, mismo ordinal -> el idempotency registry debe
        # encontrar la clave y devolver el resultado cacheado
        # sin llamar a la tool.
        model2 = _Model([("t", {"x": 1})])
        reg2 = _Reg()
        s2 = HarnessSession(
            _config(tmp_path),
            model_client=model2,
            tool_registry=reg2,
            idempotency=ir,
        )
        list(s2.step("x"))
        assert reg2.executed == [], "re-ejecuto la tool"
    finally:
        ir.close()


# ── seq del log (P2#15) ─────────────────────────────────────────


def test_seq_con_log_coincide_con_db(tmp_path) -> None:
    log = EventLog(tmp_path / "e.sqlite")
    try:
        model = _Model([])
        s = HarnessSession(
            _config(tmp_path),
            model_client=model,
            event_log=log,
        )
        events = list(s.step("hola"))
        # El ultimo seq del evento debe coincidir con el del log.
        assert events[-1].seq == log.count("r1")
    finally:
        log.close()


def test_seq_local_sin_log(tmp_path) -> None:
    model = _Model([])
    s = HarnessSession(_config(tmp_path), model_client=model)
    events = list(s.step("hola"))
    seqs = [e.seq for e in events]
    assert seqs == sorted(seqs)
    assert len(set(seqs)) == len(seqs)


def test_seq_dos_runs_mismo_log(tmp_path) -> None:
    """Dos runs en el mismo log no mezclan dominios de seq."""
    log = EventLog(tmp_path / "e.sqlite")
    try:
        for run_id in ("r1", "r2"):
            model = _Model([])
            s = HarnessSession(
                _config(tmp_path, run_id=run_id),
                model_client=model,
                event_log=log,
            )
            list(s.step("hola"))
        # Los seqs de r1 y r2 no se solapan.
        seqs_r1 = [e.seq for e in log.read("r1")]
        seqs_r2 = [e.seq for e in log.read("r2")]
        assert set(seqs_r1).isdisjoint(seqs_r2)
        assert max(seqs_r1) < min(seqs_r2)
    finally:
        log.close()
