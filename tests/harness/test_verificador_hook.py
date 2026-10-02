"""A (2026-10-01): verificador_hook en HarnessSession."""
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

    def chat(
        self, messages, *, tools=None, stream=True,
        cancel_event=None,
    ) -> Iterator[ModelDelta]:
        if not self.rounds:
            return iter([])
        yield from self.rounds.pop(0)


class _FakeRegistry:
    def __init__(self, results=None):
        self._results = results or {}

    def requires_confirmation(self, name):
        return False

    def call(self, name, arguments, **kwargs):
        return self._results.get(name, f"ok:{name}")


def _call(name, args=None):
    return ModelDelta(
        kind="tool_call",
        tool_call={"name": name, "arguments": args or {}},
    )


def _text(s):
    return ModelDelta(kind="text", text=s)


def _cfg(tmp_path, **kw):
    return HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="fake"),
        agent=AgentSpec(name="test"),
        **kw,
    )


def _last_tool_msg(session):
    for m in reversed(session._messages):
        if m.get("role") == "tool":
            return m["content"]
    return ""


def test_verificador_anexa_en_escritura(tmp_path):
    calls = []

    def hook(rel):
        calls.append(rel)
        return f"{rel}: 1 problema"

    model = _ScriptedModel([
        [_call("escribir_archivo", {"path": "a.py"})],
        [_text("fin")],
    ])
    s = HarnessSession(
        _cfg(tmp_path, auto_approve=True),
        model_client=model,
        tool_registry=_FakeRegistry(),
        verificador_hook=hook,
    )
    list(s.step("x"))
    content = _last_tool_msg(s)
    assert "[VERIFICACIÓN]" in content
    assert "a.py: 1 problema" in content
    assert calls == ["a.py"]


def test_verificador_silencioso_no_anexa(tmp_path):
    def hook(rel):
        return ""

    model = _ScriptedModel([
        [_call("crear_archivo", {"nombre": "b.py"})],
        [_text("fin")],
    ])
    s = HarnessSession(
        _cfg(tmp_path, auto_approve=True),
        model_client=model,
        tool_registry=_FakeRegistry(),
        verificador_hook=hook,
    )
    list(s.step("x"))
    assert "[VERIFICACIÓN]" not in _last_tool_msg(s)


def test_verificador_no_en_lecturas(tmp_path):
    calls = []

    def hook(rel):
        calls.append(rel)
        return "no deberia llamarse"

    model = _ScriptedModel([
        [_call("leer_archivo", {"path": "c.py"})],
        [_text("fin")],
    ])
    s = HarnessSession(
        _cfg(tmp_path, auto_approve=True),
        model_client=model,
        tool_registry=_FakeRegistry(),
        verificador_hook=hook,
    )
    list(s.step("x"))
    assert calls == []
    assert "[VERIFICACIÓN]" not in _last_tool_msg(s)


def test_verificador_no_en_errores(tmp_path):
    calls = []

    def hook(rel):
        calls.append(rel)
        return "no deberia llamarse"

    model = _ScriptedModel([
        [_call("escribir_archivo", {"path": "d.py"})],
        [_text("fin")],
    ])
    s = HarnessSession(
        _cfg(tmp_path, auto_approve=True),
        model_client=model,
        tool_registry=_FakeRegistry(
            results={"escribir_archivo": "ERROR: algo fallo"},
        ),
        verificador_hook=hook,
    )
    list(s.step("x"))
    assert calls == []
    assert "[VERIFICACIÓN]" not in _last_tool_msg(s)


def test_verificador_excepcion_no_rompe(tmp_path):
    def hook(rel):
        raise RuntimeError("boom")

    model = _ScriptedModel([
        [_call("editar_archivo", {"path": "e.py"})],
        [_text("fin")],
    ])
    s = HarnessSession(
        _cfg(tmp_path, auto_approve=True),
        model_client=model,
        tool_registry=_FakeRegistry(),
        verificador_hook=hook,
    )
    events = list(s.step("x"))
    # No crashea, no anexa
    assert "[VERIFICACIÓN]" not in _last_tool_msg(s)
    ended = next(e for e in events if e.kind == "step_ended")
    assert ended.outcome == "ok"


def test_verificador_sin_hook_no_hace_nada(tmp_path):
    model = _ScriptedModel([
        [_call("escribir_archivo", {"path": "f.py"})],
        [_text("fin")],
    ])
    s = HarnessSession(
        _cfg(tmp_path, auto_approve=True),
        model_client=model,
        tool_registry=_FakeRegistry(),
    )
    list(s.step("x"))
    assert "[VERIFICACIÓN]" not in _last_tool_msg(s)




def test_verificador_anexa_en_insertar(tmp_path):
    """P3#17: insertar_en_archivo tambien pasa por el verificador."""
    calls = []

    def hook(rel):
        calls.append(rel)
        return f"{rel}: issue"

    model = _ScriptedModel([
        [_call("insertar_en_archivo", {"path": "a.py", "line": 1})],
        [_text("fin")],
    ])
    s = HarnessSession(
        _cfg(tmp_path, auto_approve=True),
        model_client=model,
        tool_registry=_FakeRegistry(),
        verificador_hook=hook,
    )
    list(s.step("x"))
    assert calls == ["a.py"]
    content = _last_tool_msg(s)
    assert "[VERIFICACIÓN]" in content
