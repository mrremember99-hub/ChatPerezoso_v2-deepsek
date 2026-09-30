"""Grupo 1c-1: P2#2 — mark_pending antes del gate."""
from __future__ import annotations

import pathlib
from collections.abc import Iterator

from core.harness.durable import IdempotencyRegistry
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


# ── pending marcado antes del dialogo ───────────────────────────


def test_pending_marcado_antes_del_handler(tmp_path) -> None:
    """Si el proceso muere durante el dialogo, `resume` debe ver
    la op como pending. Para verificarlo: el handler inspecciona
    el registry DURANTE la confirmacion."""
    ir = IdempotencyRegistry(tmp_path / "ir.sqlite")
    try:
        visto_en_handlers: list[list[str]] = []

        def handler(_name, _args):
            # En el instante del dialogo, la op ya debe estar
            # como pending en el registry.
            visto_en_handlers.append(ir.list_pending("r1"))
            return True

        s = HarnessSession(
            _cfg(tmp_path),
            model_client=_Model([("w", {})]),
            tool_registry=_Reg(requires={"w"}),
            idempotency=ir,
            confirmation_handler=handler,
        )
        list(s.step("hola"))

        assert len(visto_en_handlers) == 1
        pendientes_durante_dialogo = visto_en_handlers[0]
        assert len(pendientes_durante_dialogo) == 1, (
            "durante el dialogo la op debe estar pending"
        )
        # Al terminar el step, ya no queda pending.
        assert ir.list_pending("r1") == []
    finally:
        ir.close()


# ── mark_failed en los 3 caminos de denegacion ──────────────────


def test_mark_failed_si_handler_deniega(tmp_path) -> None:
    ir = IdempotencyRegistry(tmp_path / "ir.sqlite")
    try:
        reg = _Reg(requires={"w"})
        s = HarnessSession(
            _cfg(tmp_path),
            model_client=_Model([("w", {})]),
            tool_registry=reg,
            idempotency=ir,
            confirmation_handler=lambda n, a: False,
        )
        list(s.step("hola"))
        assert reg.called == []
        assert ir.list_pending("r1") == []
    finally:
        ir.close()


def test_mark_failed_si_handler_lanza(tmp_path) -> None:
    ir = IdempotencyRegistry(tmp_path / "ir.sqlite")
    try:
        def boom(_n, _a):
            raise RuntimeError("dialogo roto")

        s = HarnessSession(
            _cfg(tmp_path),
            model_client=_Model([("w", {})]),
            tool_registry=_Reg(requires={"w"}),
            idempotency=ir,
            confirmation_handler=boom,
        )
        list(s.step("hola"))
        assert ir.list_pending("r1") == []
    finally:
        ir.close()


def test_mark_failed_si_no_hay_handler(tmp_path) -> None:
    ir = IdempotencyRegistry(tmp_path / "ir.sqlite")
    try:
        s = HarnessSession(
            _cfg(tmp_path),
            model_client=_Model([("w", {})]),
            tool_registry=_Reg(requires={"w"}),
            idempotency=ir,
            # sin confirmation_handler
        )
        list(s.step("hola"))
        assert ir.list_pending("r1") == []
    finally:
        ir.close()


# ── cached no vuelve a pedir confirmacion ───────────────────────


def test_cached_no_llama_al_handler(tmp_path) -> None:
    """Tras completed, un reintento del mismo step devuelve el
    resultado cacheado SIN volver a pedir confirmacion."""
    ir = IdempotencyRegistry(tmp_path / "ir.sqlite")
    try:
        reg = _Reg(requires={"w"}, result="cached-result")
        handlers: list[int] = []

        def handler(_n, _a):
            handlers.append(1)
            return True

        s1 = HarnessSession(
            _cfg(tmp_path),
            model_client=_Model([("w", {})]),
            tool_registry=reg,
            idempotency=ir,
            confirmation_handler=handler,
        )
        list(s1.step("hola"))
        assert len(handlers) == 1
        assert reg.called == ["w"]

        # Segunda sesion, mismo run_id: reintento.
        reg2 = _Reg(requires={"w"})
        s2 = HarnessSession(
            _cfg(tmp_path),
            model_client=_Model([("w", {})]),
            tool_registry=reg2,
            idempotency=ir,
            confirmation_handler=handler,
        )
        events = list(s2.step("hola"))

        assert len(handlers) == 1, (
            "el handler no deberia llamarse dos veces"
        )
        assert reg2.called == [], (
            "la tool no deberia re-ejecutarse con el cache"
        )
        # El resultado cacheado va al historial.
        done = [
            e for e in events
            if e.kind == "tool_call_completed"
        ]
        assert done and done[0].detail == "cached-result"
    finally:
        ir.close()


def test_cached_emite_eventos(tmp_path) -> None:
    """El reintento del cached emite Requested + MessageCompleted
    + Completed, igual que una ejecucion normal."""
    ir = IdempotencyRegistry(tmp_path / "ir.sqlite")
    try:
        s1 = HarnessSession(
            _cfg(tmp_path),
            model_client=_Model([("t", {})]),
            tool_registry=_Reg(result="rr"),
            idempotency=ir,
        )
        list(s1.step("hola"))

        s2 = HarnessSession(
            _cfg(tmp_path),
            model_client=_Model([("t", {})]),
            tool_registry=_Reg(),
            idempotency=ir,
        )
        events = list(s2.step("hola"))
        kinds = [e.kind for e in events]
        assert "tool_call_requested" in kinds
        assert "tool_call_completed" in kinds
    finally:
        ir.close()
