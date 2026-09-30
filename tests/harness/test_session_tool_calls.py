"""S4-b: tests del ciclo completo con tool calls."""
from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

from core.harness.durable import EventLog, IdempotencyRegistry
from core.harness.model import ModelDelta
from core.harness.policy import (
    AgentSpec,
    HarnessConfig,
    ModelSpec,
)
from core.harness.session import HarnessSession


class _ScriptedModel:
    """ModelClient con respuestas preprogramadas por ronda."""

    def __init__(self, rounds: list[list[ModelDelta]]) -> None:
        self.rounds = rounds
        self.calls: list[list[dict]] = []

    def chat(
        self,
        messages: list[dict],
        *,
        tools=None,
        stream: bool = True,
        cancel_event: threading.Event | None = None,
    ) -> Iterator[ModelDelta]:
        self.calls.append([dict(m) for m in messages])
        if not self.rounds:
            return iter([])
        round_deltas = self.rounds.pop(0)
        yield from round_deltas


class _FakeRegistry:
    """ToolRegistry minimo."""

    def __init__(
        self,
        *,
        requires: set[str] | None = None,
        results: dict[str, str] | None = None,
    ) -> None:
        self._requires = requires or set()
        self._results = results or {}
        self.calls: list[tuple] = []

    def requires_confirmation(self, name: str) -> bool:
        return name in self._requires

    def call(self, name, arguments, **kwargs):
        self.calls.append((name, dict(arguments), dict(kwargs)))
        return self._results.get(name, f"ok:{name}")


def _config(
    *, tmp_path: Path, auto_approve: bool = False,
    max_tool_rounds: int = 15,
) -> HarnessConfig:
    return HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="fake"),
        agent=AgentSpec(name="test"),
        max_tool_rounds=max_tool_rounds,
        auto_approve=auto_approve,
    )


def _call(name: str, args: dict | None = None) -> ModelDelta:
    return ModelDelta(
        kind="tool_call",
        tool_call={"name": name, "arguments": args or {}},
    )


def _text(s: str) -> ModelDelta:
    return ModelDelta(kind="text", text=s)


# ── Ciclos simples ──────────────────────────────────────────────


def test_tool_call_se_ejecuta(tmp_path):
    model = _ScriptedModel([
        [_text("leyendo"), _call("leer_archivo", {"path": "a.py"})],
        [_text("listo")],
    ])
    reg = _FakeRegistry(results={"leer_archivo": "contenido"})
    s = HarnessSession(
        _config(tmp_path=tmp_path),
        model_client=model,
        tool_registry=reg,
    )
    events = list(s.step("leé a.py"))

    kinds = [e.kind for e in events]
    assert "tool_call_requested" in kinds
    assert "tool_call_completed" in kinds
    completed = next(
        e for e in events if e.kind == "tool_call_completed"
    )
    assert completed.status == "ok"
    assert completed.detail == "contenido"

    tool_msg = next(m for m in s._messages if m["role"] == "tool")
    assert tool_msg["content"] == "contenido"
    assert len(reg.calls) == 1
    _n, _args, kwargs = reg.calls[0]
    assert kwargs.get("allow_destructive") is True


def test_sin_tool_calls_un_solo_round(tmp_path):
    model = _ScriptedModel([[_text("hola")]])
    s = HarnessSession(
        _config(tmp_path=tmp_path),
        model_client=model,
        tool_registry=_FakeRegistry(),
    )
    events = list(s.step("hola"))
    assert not any(
        e.kind == "tool_call_requested" for e in events
    )
    ended = next(e for e in events if e.kind == "step_ended")
    assert ended.outcome == "ok"


def test_multiples_tool_calls_en_secuencia(tmp_path):
    model = _ScriptedModel([
        [_call("leer_archivo", {"path": "a.py"})],
        [_call("leer_archivo", {"path": "b.py"})],
        [_text("todo leido")],
    ])
    reg = _FakeRegistry()
    s = HarnessSession(
        _config(tmp_path=tmp_path),
        model_client=model,
        tool_registry=reg,
    )
    events = list(s.step("leé ambos"))
    completed = [
        e for e in events if e.kind == "tool_call_completed"
    ]
    assert len(completed) == 2
    assert len(reg.calls) == 2


# ── Gate de confirmacion ────────────────────────────────────────


def test_requiere_confirmacion_sin_auto_approve_deniega(tmp_path):
    model = _ScriptedModel([
        [_call("escribir_archivo", {"path": "a.py", "content": "x"})],
    ])
    reg = _FakeRegistry(requires={"escribir_archivo"})
    s = HarnessSession(
        _config(tmp_path=tmp_path, auto_approve=False),
        model_client=model,
        tool_registry=reg,
    )
    events = list(s.step("escribí"))

    assert reg.calls == []
    requested = next(
        e for e in events if e.kind == "tool_call_requested"
    )
    assert requested.auto_approved is False
    completed = next(
        e for e in events if e.kind == "tool_call_completed"
    )
    assert completed.status == "cancelled"
    assert "confirmación" in completed.detail


def test_requiere_confirmacion_con_auto_approve_ejecuta(tmp_path):
    model = _ScriptedModel([
        [_call("escribir_archivo", {"path": "a.py", "content": "x"})],
        [_text("listo")],
    ])
    reg = _FakeRegistry(requires={"escribir_archivo"})
    s = HarnessSession(
        _config(tmp_path=tmp_path, auto_approve=True),
        model_client=model,
        tool_registry=reg,
    )
    list(s.step("escribí"))
    assert len(reg.calls) == 1
    _n, _args, kwargs = reg.calls[0]
    assert kwargs.get("allow_destructive") is True


# ── max_tool_rounds ─────────────────────────────────────────────


def test_max_tool_rounds_superado(tmp_path):
    model = _ScriptedModel(
        [[_call("t", {"i": i})] for i in range(20)],
    )
    reg = _FakeRegistry()
    s = HarnessSession(
        _config(tmp_path=tmp_path, max_tool_rounds=3),
        model_client=model,
        tool_registry=reg,
    )
    events = list(s.step("bucle"))

    kinds = [e.kind for e in events]
    assert "harness_error" in kinds
    err = next(e for e in events if e.kind == "harness_error")
    assert err.component == "session"
    assert "max_tool_rounds" in err.message
    ended = next(e for e in events if e.kind == "step_ended")
    assert ended.outcome == "failed"


# ── Errores de tool ─────────────────────────────────────────────


def test_tool_devuelve_error_status_error(tmp_path):
    model = _ScriptedModel([
        [_call("t")],
        [_text("ok")],
    ])
    reg = _FakeRegistry(results={"t": "ERROR: algo fallo"})
    s = HarnessSession(
        _config(tmp_path=tmp_path),
        model_client=model,
        tool_registry=reg,
    )
    events = list(s.step("x"))
    completed = next(
        e for e in events if e.kind == "tool_call_completed"
    )
    assert completed.status == "error"


def test_tool_lanza_excepcion_status_error(tmp_path):
    class _BoomRegistry(_FakeRegistry):
        def call(self, name, arguments, **kwargs):
            raise RuntimeError("boom")

    model = _ScriptedModel([
        [_call("t")],
        [_text("ok")],
    ])
    s = HarnessSession(
        _config(tmp_path=tmp_path),
        model_client=model,
        tool_registry=_BoomRegistry(),
    )
    events = list(s.step("x"))
    completed = next(
        e for e in events if e.kind == "tool_call_completed"
    )
    assert completed.status == "error"
    assert "boom" in completed.detail


# ── Idempotencia ────────────────────────────────────────────────


def test_idempotencia_deja_pending_limpio(tmp_path):
    ir = IdempotencyRegistry(tmp_path / "ir.sqlite")
    try:
        model = _ScriptedModel([
            [_call("t")],
            [_text("ok")],
        ])
        reg = _FakeRegistry(results={"t": "resultado-unico"})
        s = HarnessSession(
            _config(tmp_path=tmp_path),
            model_client=model,
            tool_registry=reg,
            idempotency=ir,
        )
        list(s.step("x"))
        assert ir.list_pending("r1") == []
    finally:
        ir.close()


def test_idempotencia_sin_registry_no_falla(tmp_path):
    model = _ScriptedModel([
        [_call("t")],
        [_text("ok")],
    ])
    s = HarnessSession(
        _config(tmp_path=tmp_path),
        model_client=model,
        tool_registry=_FakeRegistry(),
    )
    events = list(s.step("x"))
    ended = next(e for e in events if e.kind == "step_ended")
    assert ended.outcome == "ok"


# ── Event log con tools ─────────────────────────────────────────


def test_event_log_recibe_tool_calls(tmp_path):
    log = EventLog(tmp_path / "e.sqlite")
    try:
        model = _ScriptedModel([
            [_call("t")],
            [_text("ok")],
        ])
        s = HarnessSession(
            _config(tmp_path=tmp_path),
            model_client=model,
            tool_registry=_FakeRegistry(),
            event_log=log,
        )
        list(s.step("x"))
        kinds = [e.kind for e in log.read("r1")]
        assert "tool_call_requested" in kinds
        assert "tool_call_completed" in kinds
    finally:
        log.close()
