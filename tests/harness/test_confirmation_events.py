"""Grupo 2b: P2#9 — validar args + eventos + cancel_event."""
from __future__ import annotations

import pathlib
import threading
from collections.abc import Iterator

from core.harness.model import ModelDelta
from core.harness.policy import AgentSpec, HarnessConfig, ModelSpec
from core.harness.session import HarnessSession


class _Model:
    """Emite UNA tool call con arguments arbitrarios y luego para.

    Tras el primer chat(), las siguientes llamadas emiten 'done'
    para que el _agent_loop no itere hasta max_tool_rounds.
    """

    def __init__(self, tool_name: str, arguments) -> None:
        self._name = tool_name
        self._args = arguments
        self._done = False

    def chat(
        self, messages, *, tools=None, stream=True, cancel_event=None,
    ) -> Iterator[ModelDelta]:
        if self._done:
            yield ModelDelta(kind="text", text="fin")
            yield ModelDelta(kind="done")
            return
        self._done = True
        yield ModelDelta(
            kind="tool_call",
            tool_call={"name": self._name, "arguments": self._args},
        )


class _Reg:
    def __init__(self, *, requires: set[str] | None = None) -> None:
        self._requires = requires or set()
        self.called: list[str] = []

    def requires_confirmation(self, name: str) -> bool:
        return name in self._requires

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


# ── Validacion de arguments ─────────────────────────────────────


def test_arguments_none_ok(tmp_path) -> None:
    """None es valido: muchos modelos no mandan args."""
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_Model("t", None),
        tool_registry=_Reg(),
    )
    events = list(s.step("hola"))
    assert any(e.kind == "tool_call_completed" for e in events)


def test_arguments_string_error(tmp_path) -> None:
    """args no-dict (string): HarnessError sin llamar a la tool."""
    reg = _Reg()
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_Model("t", "no-dict"),
        tool_registry=reg,
    )
    events = list(s.step("hola"))
    kinds = [e.kind for e in events]
    assert "harness_error" in kinds
    err = next(e for e in events if e.kind == "harness_error")
    assert "arguments invalido" in err.message
    assert reg.called == []


def test_arguments_lista_error(tmp_path) -> None:
    reg = _Reg()
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_Model("t", [1, 2, 3]),
        tool_registry=reg,
    )
    events = list(s.step("hola"))
    assert any(
        e.kind == "harness_error" and "arguments invalido" in e.message
        for e in events
    )
    assert reg.called == []


# ── Eventos de confirmacion ─────────────────────────────────────


def test_emite_requested_y_resolved_aprobado(tmp_path) -> None:
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_Model("w", {}),
        tool_registry=_Reg(requires={"w"}),
        confirmation_handler=lambda n, a: True,
    )
    events = list(s.step("hola"))
    kinds = [e.kind for e in events]
    assert "confirmation_requested" in kinds
    assert "confirmation_resolved" in kinds
    resolved = next(
        e for e in events if e.kind == "confirmation_resolved"
    )
    assert resolved.approved is True
    assert resolved.timeout is False


def test_emite_resolved_denegado(tmp_path) -> None:
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_Model("w", {}),
        tool_registry=_Reg(requires={"w"}),
        confirmation_handler=lambda n, a: False,
    )
    events = list(s.step("hola"))
    resolved = next(
        e for e in events if e.kind == "confirmation_resolved"
    )
    assert resolved.approved is False


def test_emite_resolved_si_handler_lanza(tmp_path) -> None:
    def boom(_n, _a):
        raise RuntimeError("dialogo roto")

    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_Model("w", {}),
        tool_registry=_Reg(requires={"w"}),
        confirmation_handler=boom,
    )
    events = list(s.step("hola"))
    resolved = next(
        e for e in events if e.kind == "confirmation_resolved"
    )
    assert resolved.approved is False


def test_sin_handler_no_emite_requested(tmp_path) -> None:
    """Sin handler, no hay dialogo: no se emite Requested."""
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_Model("w", {}),
        tool_registry=_Reg(requires={"w"}),
    )
    events = list(s.step("hola"))
    kinds = [e.kind for e in events]
    assert "confirmation_requested" not in kinds


# ── Firma del handler ──────────────────────────────────────────


def test_handler_firma_vieja_funciona(tmp_path) -> None:
    def handler(name, args):
        return True

    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_Model("w", {}),
        tool_registry=_Reg(requires={"w"}),
        confirmation_handler=handler,
    )
    events = list(s.step("hola"))
    resolved = next(
        e for e in events if e.kind == "confirmation_resolved"
    )
    assert resolved.approved is True


def test_handler_recibe_cancel_event(tmp_path) -> None:
    captured: list = []

    def handler(name, args, *, cancel_event=None):
        captured.append(cancel_event)
        return True

    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_Model("w", {}),
        tool_registry=_Reg(requires={"w"}),
        confirmation_handler=handler,
    )
    list(s.step("hola"))
    assert len(captured) == 1
    assert isinstance(captured[0], threading.Event)
