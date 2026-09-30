"""P2#8: denegaciones y errores llegan al LoopDetector."""
from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

from core.harness.loop import LoopDetector
from core.harness.model import ModelDelta
from core.harness.policy import (
    AgentSpec,
    HarnessConfig,
    LoopPolicy,
    ModelSpec,
)
from core.harness.session import HarnessSession


class _ScriptedModel:
    def __init__(self, rounds):
        self.rounds = rounds

    def chat(
        self, messages, *, tools=None, stream=True,
        cancel_event=None,
    ) -> Iterator[ModelDelta]:
        if not self.rounds:
            return iter([])
        yield from self.rounds.pop(0)


class _FakeRegistry:
    def __init__(
        self,
        *,
        requires: set[str] | None = None,
        results: dict[str, str] | None = None,
    ) -> None:
        self._requires = requires or set()
        self._results = results or {}

    def requires_confirmation(self, name: str) -> bool:
        return name in self._requires

    def call(self, name, arguments, **kwargs):
        return self._results.get(name, f"ok:{name}")


class _SpyLoopDetector(LoopDetector):
    """LoopDetector real que registra cada observe."""

    def __init__(self, policy: LoopPolicy) -> None:
        super().__init__(policy)
        self.observed: list[tuple] = []

    def observe(self, name, arguments, *, result, result_summary):
        self.observed.append(
            (name, dict(arguments or {}), result),
        )
        return super().observe(
            name, arguments,
            result=result, result_summary=result_summary,
        )


def _config(tmp_path: Path, **kw) -> HarnessConfig:
    return HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="fake"),
        agent=AgentSpec(name="test"),
        **kw,
    )


def _call(name: str, args=None) -> ModelDelta:
    return ModelDelta(
        kind="tool_call",
        tool_call={"name": name, "arguments": args or {}},
    )


def _text(s: str) -> ModelDelta:
    return ModelDelta(kind="text", text=s)


def test_sin_handler_deniega_cuenta_y_observa(tmp_path):
    """Sin handler inyectado: denial -> contador + observe."""
    model = _ScriptedModel([[_call("peligrosa")], [_text("fin")]])
    reg = _FakeRegistry(requires={"peligrosa"})
    spy = _SpyLoopDetector(LoopPolicy())
    s = HarnessSession(
        _config(tmp_path),
        model_client=model,
        tool_registry=reg,
        loop_detector=spy,
    )
    list(s.step("peligrosa"))
    assert s._step_error_count == 1
    assert len(spy.observed) == 1
    name, _args, result = spy.observed[0]
    assert name == "peligrosa"
    assert "CANCELADA" in result


def test_handler_false_cuenta_y_observa(tmp_path):
    """Handler devuelve False -> denial -> contador + observe."""
    model = _ScriptedModel([[_call("peligrosa")], [_text("fin")]])
    reg = _FakeRegistry(requires={"peligrosa"})
    spy = _SpyLoopDetector(LoopPolicy())
    s = HarnessSession(
        _config(tmp_path),
        model_client=model,
        tool_registry=reg,
        loop_detector=spy,
        confirmation_handler=lambda n, a: False,
    )
    list(s.step("peligrosa"))
    assert s._step_error_count == 1
    assert len(spy.observed) == 1


def test_handler_excepcion_cuenta_y_observa(tmp_path):
    """Handler lanza excepcion -> error -> contador + observe."""
    def boom(n, a):
        raise RuntimeError("boom")

    model = _ScriptedModel([[_call("peligrosa")], [_text("fin")]])
    reg = _FakeRegistry(requires={"peligrosa"})
    spy = _SpyLoopDetector(LoopPolicy())
    s = HarnessSession(
        _config(tmp_path),
        model_client=model,
        tool_registry=reg,
        loop_detector=spy,
        confirmation_handler=boom,
    )
    list(s.step("peligrosa"))
    assert s._step_error_count == 1
    assert len(spy.observed) == 1
    _, _, result = spy.observed[0]
    assert "confirmation_handler fallo" in result


def test_args_invalidos_cuenta_y_observa(tmp_path):
    """arguments no-dict -> error previo -> contador + observe."""
    bad = ModelDelta(
        kind="tool_call",
        tool_call={"name": "t", "arguments": "no-dict"},
    )
    model = _ScriptedModel([[bad], [_text("fin")]])
    spy = _SpyLoopDetector(LoopPolicy())
    s = HarnessSession(
        _config(tmp_path),
        model_client=model,
        tool_registry=_FakeRegistry(),
        loop_detector=spy,
    )
    list(s.step("t"))
    assert s._step_error_count == 1
    assert len(spy.observed) == 1


def test_tool_no_permitida_cuenta_y_observa(tmp_path):
    """Tool fuera de allowed_tools -> error -> contador + observe."""
    model = _ScriptedModel([[_call("prohibida")], [_text("fin")]])
    cfg = HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="fake"),
        agent=AgentSpec(name="test", allowed_tools=["permitida"]),
    )
    spy = _SpyLoopDetector(LoopPolicy())
    s = HarnessSession(
        cfg,
        model_client=model,
        tool_registry=_FakeRegistry(),
        loop_detector=spy,
    )
    list(s.step("prohibida"))
    assert s._step_error_count == 1
    assert len(spy.observed) == 1


def test_tool_ok_no_cuenta_pero_observa(tmp_path):
    """POSITIVO: tool OK -> contador 0, detector SI ve el par."""
    model = _ScriptedModel([[_call("t", {"x": 1})], [_text("fin")]])
    spy = _SpyLoopDetector(LoopPolicy())
    s = HarnessSession(
        _config(tmp_path, auto_approve=True),
        model_client=model,
        tool_registry=_FakeRegistry(),
        loop_detector=spy,
    )
    list(s.step("t"))
    assert s._step_error_count == 0
    assert len(spy.observed) == 1
    name, args, _result = spy.observed[0]
    assert name == "t"
    assert args == {"x": 1}
