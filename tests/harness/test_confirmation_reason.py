"""2026-10-01: reason del dialogo de confirmacion llega al handler."""
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
    def __init__(self, rounds):
        self.rounds = rounds

    def chat(self, messages, *, tools=None, stream=True,
             cancel_event=None) -> Iterator[ModelDelta]:
        if not self.rounds:
            return iter([])
        yield from self.rounds.pop(0)


class _FakeRegistry:
    def __init__(self, requires=None):
        self._requires = requires or set()

    def requires_confirmation(self, name):
        return name in self._requires

    def call(self, name, arguments, **kwargs):
        return f"ok:{name}"


def _cfg(tmp_path, **kw):
    return HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="fake"),
        agent=AgentSpec(name="test"),
        **kw,
    )


def _call(name, args=None):
    return ModelDelta(
        kind="tool_call",
        tool_call={"name": name, "arguments": args or {}},
    )


def _text(s):
    return ModelDelta(kind="text", text=s)


def test_reason_llega_al_handler(tmp_path):
    """El handler moderno recibe reason= con el motivo real."""
    captured = {}

    def handler(name, arguments, *, reason="", cancel_event=None):
        captured["reason"] = reason
        return True  # aprobar

    model = _ScriptedModel([
        [_call("borrar_archivo", {"path": "a.py"})],
        [_text("fin")],
    ])
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=model,
        tool_registry=_FakeRegistry(requires={"borrar_archivo"}),
        confirmation_handler=handler,
    )
    list(s.step("x"))
    assert "destructiva" in captured["reason"]


def test_reason_shell_allowlist(tmp_path):
    captured = {}

    def handler(name, arguments, *, reason="", cancel_event=None):
        captured["reason"] = reason
        return False  # denegar

    model = _ScriptedModel([
        [_call("ejecutar_comando", {"command": "rm -rf x"})],
        [_text("fin")],
    ])
    s = HarnessSession(
        _cfg(tmp_path, auto_approve=True, auto_approve_shell=True),
        model_client=model,
        tool_registry=_FakeRegistry(requires={"ejecutar_comando"}),
        confirmation_handler=handler,
        command_allowed=lambda cmd: False,  # allowlist rechaza
    )
    list(s.step("x"))
    assert "allowlist" in captured["reason"]


def test_handler_sin_reason_sigue_funcionando(tmp_path):
    """Firma vieja (name, args) sigue OK."""
    captured = {}

    def handler(name, arguments):
        captured["name"] = name
        return False

    model = _ScriptedModel([
        [_call("borrar_archivo", {"path": "b.py"})],
        [_text("fin")],
    ])
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=model,
        tool_registry=_FakeRegistry(requires={"borrar_archivo"}),
        confirmation_handler=handler,
    )
    list(s.step("x"))
    assert captured["name"] == "borrar_archivo"


def test_reason_auto_approve_off(tmp_path):
    captured = {}

    def handler(name, arguments, *, reason="", cancel_event=None):
        captured["reason"] = reason
        return False

    model = _ScriptedModel([
        [_call("crear_carpeta", {"path": "x"})],
        [_text("fin")],
    ])
    s = HarnessSession(
        _cfg(tmp_path, auto_approve=False),
        model_client=model,
        tool_registry=_FakeRegistry(requires={"crear_carpeta"}),
        confirmation_handler=handler,
    )
    list(s.step("x"))
    assert "auto_approve" in captured["reason"]
