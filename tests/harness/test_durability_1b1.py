"""Grupo 1b-1: P2#16 — mutar antes de yield + step no reentrante."""
from __future__ import annotations

import pathlib
from collections.abc import Iterator

import pytest

from core.harness.model import ModelDelta
from core.harness.policy import AgentSpec, HarnessConfig, ModelSpec
from core.harness.session import HarnessSession


class _Model:
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
    def __init__(
        self,
        *,
        requires: set[str] | None = None,
        result: str = "ok",
    ) -> None:
        self._requires = requires or set()
        self._result = result
        self.called: list[str] = []

    def requires_confirmation(self, name: str) -> bool:
        return name in self._requires

    def call(
        self, name, args, *, allow_destructive=False, cancel_event=None,
    ) -> str:
        self.called.append(name)
        return self._result


def _cfg(tmp_path: pathlib.Path) -> HarnessConfig:
    return HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="m"),
        agent=AgentSpec(name="a"),
    )


# ── Mutación antes de yield ─────────────────────────────────────


def test_messages_actualizados_al_emitir_tool_completed(tmp_path) -> None:
    """Cuando se emite ToolCallCompleted, `_messages` ya incluye
    el role=tool. Si el consumidor abandona el generador tras ese
    evento, el estado sigue siendo coherente."""
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_Model([("t", {})]),
        tool_registry=_Reg(result="resultado"),
    )
    saw_messages_at_completed: list[list] = []
    for event in s.step("hola"):
        if event.kind == "tool_call_completed":
            saw_messages_at_completed.append(list(s._messages))
    assert saw_messages_at_completed, "no se emitio ToolCallCompleted"
    snapshot = saw_messages_at_completed[0]
    roles = [m["role"] for m in snapshot]
    assert "tool" in roles, (
        f"role=tool no estaba en _messages al emitir "
        f"ToolCallCompleted: {roles}"
    )


def test_messages_actualizados_al_emitir_error_tool(tmp_path) -> None:
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_Model([("t", {})]),
        tool_registry=_Reg(result="ERROR: fallo"),
    )
    snapshots: list[list] = []
    for event in s.step("hola"):
        if event.kind == "tool_call_completed":
            snapshots.append(list(s._messages))
    assert snapshots
    roles = [m["role"] for m in snapshots[0]]
    assert "tool" in roles


def test_messages_actualizados_al_denegar(tmp_path) -> None:
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_Model([("w", {})]),
        tool_registry=_Reg(requires={"w"}),
        confirmation_handler=lambda n, a: False,
    )
    snapshots: list[list] = []
    for event in s.step("hola"):
        if event.kind == "tool_call_completed":
            snapshots.append(list(s._messages))
    assert snapshots
    roles = [m["role"] for m in snapshots[0]]
    assert "tool" in roles


def test_messages_actualizados_al_not_allowed(tmp_path) -> None:
    cfg = HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="m"),
        agent=AgentSpec(name="a", allowed_tools=["permitida"]),
    )
    s = HarnessSession(
        cfg,
        model_client=_Model([("prohibida", {})]),
        tool_registry=_Reg(),
    )
    snapshots: list[list] = []
    for event in s.step("hola"):
        if event.kind == "tool_call_completed":
            snapshots.append(list(s._messages))
    assert snapshots
    roles = [m["role"] for m in snapshots[0]]
    assert "tool" in roles


# ── step no reentrante ─────────────────────────────────────────


def test_step_no_reentrante(tmp_path) -> None:
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_Model([]),
    )
    # Abrimos el generador sin consumirlo del todo.
    gen = s.step("hola")
    next(gen)  # arranca el step: _step_in_progress = True
    try:
        with pytest.raises(RuntimeError, match="no es reentrante"):
            list(s.step("otra"))
    finally:
        gen.close()


def test_step_libera_flag_tras_exito(tmp_path) -> None:
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_Model([]),
    )
    list(s.step("hola"))
    assert s._step_in_progress is False


def test_step_libera_flag_tras_error(tmp_path) -> None:
    class _Boom:
        def chat(self, *_a, **_kw):
            raise RuntimeError("boom")

    s = HarnessSession(_cfg(tmp_path), model_client=_Boom())
    list(s.step("hola"))
    assert s._step_in_progress is False


def test_step_libera_flag_tras_abandono(tmp_path) -> None:
    """Si el consumidor cierra el generador a mitad, el finally
    debe limpiar el flag."""
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=_Model([]),
    )
    gen = s.step("hola")
    next(gen)
    gen.close()  # simula abandono del consumidor
    assert s._step_in_progress is False
