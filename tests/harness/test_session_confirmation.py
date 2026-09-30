"""S4-d: confirmation_handler inyectado."""
from __future__ import annotations

import threading
from collections.abc import Iterator
from pathlib import Path

from core.harness.model import ModelDelta
from core.harness.policy import (
    AgentSpec,
    HarnessConfig,
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


# ── Sin handler: gate minimo S4-b (compat) ─────────────────────


def test_sin_handler_deniega_con_mensaje_explicativo(tmp_path):
    model = _ScriptedModel([
        [_call("escribir_archivo", {"path": "a.py"})],
    ])
    reg = _FakeRegistry(requires={"escribir_archivo"})
    s = HarnessSession(
        _config(tmp_path),
        model_client=model,
        tool_registry=reg,
    )
    events = list(s.step("escribí"))

    assert reg.calls == []
    completed = next(
        e for e in events if e.kind == "tool_call_completed"
    )
    assert completed.status == "cancelled"
    assert "confirmation_handler" in completed.detail


def test_sin_handler_pero_tool_no_requiere_ejecuta(tmp_path):
    """Tool que NO requiere confirmacion se ejecuta sin handler."""
    model = _ScriptedModel([
        [_call("leer_archivo", {"path": "a.py"})],
        [_text("ok")],
    ])
    reg = _FakeRegistry()
    s = HarnessSession(
        _config(tmp_path),
        model_client=model,
        tool_registry=reg,
    )
    list(s.step("leé"))
    assert len(reg.calls) == 1
    _n, _args, kwargs = reg.calls[0]
    assert kwargs.get("allow_destructive") is True


# ── Handler aprueba ───────────────────────────────────────────


def test_handler_aprueba_ejecuta(tmp_path):
    model = _ScriptedModel([
        [_call("escribir_archivo", {"path": "a.py"})],
        [_text("ok")],
    ])
    reg = _FakeRegistry(requires={"escribir_archivo"})
    s = HarnessSession(
        _config(tmp_path),
        model_client=model,
        tool_registry=reg,
        confirmation_handler=lambda name, args: True,
    )
    list(s.step("escribí"))
    assert len(reg.calls) == 1
    _n, _args, kwargs = reg.calls[0]
    assert kwargs.get("allow_destructive") is True


def test_handler_recibe_name_y_args(tmp_path):
    captured: list[tuple] = []

    def handler(name, args):
        captured.append((name, dict(args)))
        return True

    model = _ScriptedModel([
        [_call("escribir_archivo",
               {"path": "a.py", "content": "x"})],
        [_text("ok")],
    ])
    reg = _FakeRegistry(requires={"escribir_archivo"})
    s = HarnessSession(
        _config(tmp_path),
        model_client=model,
        tool_registry=reg,
        confirmation_handler=handler,
    )
    list(s.step("escribí"))
    assert captured == [("escribir_archivo",
                         {"path": "a.py", "content": "x"})]


# ── Handler deniega ───────────────────────────────────────────


def test_handler_deniega_no_ejecuta(tmp_path):
    model = _ScriptedModel([
        [_call("escribir_archivo", {"path": "a.py"})],
    ])
    reg = _FakeRegistry(requires={"escribir_archivo"})
    s = HarnessSession(
        _config(tmp_path),
        model_client=model,
        tool_registry=reg,
        confirmation_handler=lambda name, args: False,
    )
    events = list(s.step("escribí"))

    assert reg.calls == []
    completed = next(
        e for e in events if e.kind == "tool_call_completed"
    )
    assert completed.status == "cancelled"
    assert "DENEGADA" in completed.detail or "denegada" in completed.detail


def test_handler_no_se_llama_si_tool_no_requiere(tmp_path):
    """Si requires_confirmation es False, el handler no se llama."""
    calls: list = []

    def handler(name, args):
        calls.append((name, args))
        return True

    model = _ScriptedModel([
        [_call("leer_archivo", {"path": "a.py"})],
        [_text("ok")],
    ])
    reg = _FakeRegistry()  # sin requires
    s = HarnessSession(
        _config(tmp_path),
        model_client=model,
        tool_registry=reg,
        confirmation_handler=handler,
    )
    list(s.step("leé"))
    assert calls == []
    assert len(reg.calls) == 1


# ── Handler lanza excepcion ───────────────────────────────────


def test_handler_excepcion_status_error(tmp_path):
    def boom(name, args):
        raise RuntimeError("dialogo roto")

    model = _ScriptedModel([
        [_call("escribir_archivo", {"path": "a.py"})],
        [_text("ok")],
    ])
    reg = _FakeRegistry(requires={"escribir_archivo"})
    s = HarnessSession(
        _config(tmp_path),
        model_client=model,
        tool_registry=reg,
        confirmation_handler=boom,
    )
    events = list(s.step("escribí"))

    assert reg.calls == []
    completed = next(
        e for e in events if e.kind == "tool_call_completed"
    )
    assert completed.status == "error"
    assert "dialogo roto" in completed.detail


# ── auto_approve gana al handler ──────────────────────────────


def test_auto_approve_no_llama_al_handler(tmp_path):
    calls: list = []

    def handler(name, args):
        calls.append(name)
        return False

    model = _ScriptedModel([
        [_call("escribir_archivo", {"path": "a.py"})],
        [_text("ok")],
    ])
    reg = _FakeRegistry(requires={"escribir_archivo"})
    s = HarnessSession(
        _config(tmp_path, auto_approve=True),
        model_client=model,
        tool_registry=reg,
        confirmation_handler=handler,
    )
    list(s.step("escribí"))
    assert calls == []
    assert len(reg.calls) == 1
