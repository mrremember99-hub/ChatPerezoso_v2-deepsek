"""Tests de contrato P4 — fixes aplicados en 8dde956.

Cubren los 11 hallazgos cerrados en el commit de fixes:
P4#1, #2, #4, #5, #6, #8, #9, #10, #12, #14, #19.
"""
from __future__ import annotations

from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from core.agent.events import (
    AgentError,
    StepEnded,
    ToolCallCompleted,
)
from core.agent.model import ModelDelta
from core.agent.policy import AgentConfig, ModelSpec
from core.agent.session import AgentSession


# ── Helpers ────────────────────────────────────────────────

def _cfg(**overrides: Any) -> AgentConfig:
    defaults = {
        "run_id": "r1",
        "workspace_root": Path("/tmp"),
        "model": ModelSpec(name="q"),
    }
    defaults.update(overrides)
    return AgentConfig(**defaults)


def _text(s: str) -> ModelDelta:
    return ModelDelta(kind="text", text=s)


def _tool(name: str, args: dict | None = None) -> ModelDelta:
    return ModelDelta(
        kind="tool_call",
        tool_call={"name": name, "arguments": args or {}},
    )


def _done() -> ModelDelta:
    return ModelDelta(kind="done")


def _events_of(events, cls) -> list:
    return [e for e in events if isinstance(e, cls)]


class _Scripted:
    def __init__(self, rounds):
        self._rounds = list(rounds)

    def chat(self, messages, *, tools=None, stream=True,
             cancel_event=None):
        if not self._rounds:
            yield _done()
            return
        for d in self._rounds.pop(0):
            yield d


class _RaisingRegistry:
    """requires_confirmation siempre lanza (P4#5)."""

    def __init__(self) -> None:
        self.call_count = 0

    def requires_confirmation(self, name: str) -> bool:
        raise RuntimeError("registry roto")

    def call(self, name, args, *, allow_destructive=False,
             cancel_event=None):
        self.call_count += 1
        return "no deberia ejecutarse"

    def definitions(self):
        return []


class _Registry:
    def __init__(self, requires=None, results=None) -> None:
        self._requires = requires or set()
        self._results = results or {}
        self.calls: list = []

    def requires_confirmation(self, name: str) -> bool:
        return name in self._requires

    def call(self, name, args, *, allow_destructive=False,
             cancel_event=None):
        self.calls.append((name, dict(args)))
        return self._results.get(name, "ok")

    def definitions(self):
        return []


# ═══════════════════════════════════════════════════════════
# Sesión — P4#2, #5, #12, #14, #1
# ═══════════════════════════════════════════════════════════


def test_p4_2_cancel_durante_generacion_no_es_error():
    """P4#2: cancel setteado + excepcion != AgentError."""
    class _CancelModel:
        def __init__(self) -> None:
            self.session = None

        def chat(self, messages, *, tools=None, stream=True,
                 cancel_event=None):
            if self.session is not None:
                self.session._cancel.set()
            raise RuntimeError("OllamaCancelled simulado")
            yield  # pragma: no cover

    m = _CancelModel()
    s = AgentSession(_cfg(), model_client=m)
    m.session = s

    events = list(s.step("x"))
    assert not _events_of(events, AgentError), (
        "P4#2: cancel no debe clasificarse como error del modelo"
    )
    step = _events_of(events, StepEnded)[-1]
    assert step.outcome == "cancelled"


def test_p4_5_requires_confirmation_excepcion_fail_closed():
    """P4#5: si requires_confirmation lanza, se trata como True."""
    m = _Scripted([[_tool("x"), _done()]])
    reg = _RaisingRegistry()
    s = AgentSession(_cfg(), model_client=m, tool_registry=reg)

    events = list(s.step("x"))
    assert reg.call_count == 0, (
        "P4#5: la tool no debe ejecutarse si el gate falla"
    )
    done = _events_of(events, ToolCallCompleted)
    assert done and done[0].status == "cancelled"


def test_p4_12_cancel_tras_aprobacion_no_ejecuta():
    """P4#12: cancel entre aprobacion y call() no ejecuta."""
    m = _Scripted([[_tool("x"), _done()]])
    reg = _Registry(requires={"x"})
    s = AgentSession(_cfg(), model_client=m, tool_registry=reg)

    def handler(name, args, *, reason=""):
        s._cancel.set()
        return True

    s.confirmation_handler = handler
    events = list(s.step("x"))

    assert reg.calls == [], (
        "P4#12: cancel tras aprobacion no debe ejecutar la tool"
    )
    done = _events_of(events, ToolCallCompleted)[0]
    assert done.status == "cancelled"


def test_p4_14_denegacion_explicita_marca_cancelled():
    """P4#14: handler devuelve False -> cancelled, no failed."""
    m = _Scripted([[_tool("x"), _done()]])
    reg = _Registry(requires={"x"})
    s = AgentSession(_cfg(), model_client=m, tool_registry=reg)

    def handler(name, args, *, reason=""):
        return False

    s.confirmation_handler = handler
    events = list(s.step("x"))

    assert not _events_of(events, AgentError), (
        "P4#14: denegacion del usuario no es error del modelo"
    )
    step = _events_of(events, StepEnded)[-1]
    assert step.outcome == "cancelled"


def test_p4_1_step_tras_close_lanza():
    """P4#1: step() tras close() debe lanzar RuntimeError."""
    s = AgentSession(_cfg(), model_client=_Scripted([]))
    list(s.close(reason="completed", summary="fin"))

    with pytest.raises(RuntimeError, match="tras close"):
        list(s.step("x"))


# ═══════════════════════════════════════════════════════════
# Worker — P4#2, #8
# ═══════════════════════════════════════════════════════════


def test_p4_2_worker_no_doble_cancelled_tras_finished():
    """P4#2: StepEnded(ok) marca terminal; cancel tardio no duplica."""
    pytest.importorskip("PySide6")
    from ui.agent_worker import AgentWorker

    s = AgentSession(_cfg(), model_client=None)
    w = AgentWorker(s, "x")

    emitted: list[str] = []
    w.cancelled.connect(lambda: emitted.append("cancelled"))

    # Simular step OK via _on_step_ended
    w._on_step_ended(StepEnded(
        seq=1, run_id="r1", ts="t", step_index=0, outcome="ok",
    ))
    assert emitted == []
    assert w._terminal_emitted is True

    # Simular el check final de run()
    w._cancel.set()
    if w._cancel.is_set() and not w._terminal_emitted:
        w.cancelled.emit()
    assert emitted == [], (
        "P4#2: no emitir cancelled tras finished"
    )


def test_p4_8_cancel_antes_de_emit_no_bloquea():
    """P4#8: cancel ya seteado -> handle_confirmation retorna False."""
    pytest.importorskip("PySide6")
    from ui.agent_worker import AgentWorker

    s = AgentSession(_cfg(), model_client=None)
    w = AgentWorker(s, "x")
    w._cancel.set()

    result = w.handle_confirmation("tool", {})
    assert result is False, (
        "P4#8: cancel previo debe retornar sin esperar"
    )


# ═══════════════════════════════════════════════════════════
# Controller — P4#9, #4, #6, #10, #19
# ═══════════════════════════════════════════════════════════


def _new_ctrl():
    pytest.importorskip("PySide6")
    from ui.controllers.chat_controller import ChatController

    ctrl = ChatController.__new__(ChatController)
    ctrl._state = MagicMock(is_active=False)
    ctrl._spawn_worker = MagicMock()
    ctrl._append_message = MagicMock()
    ctrl.renderer = MagicMock()
    ctrl.status = MagicMock()
    ctrl._last_model = ""
    ctrl._last_options = None
    ctrl._last_system_prompt = ""
    return ctrl


def test_p4_9_send_externo_con_cola_activa_bloqueado():
    """P4#9: send() externo con cola activa no arranca worker."""
    ctrl = _new_ctrl()
    ctrl._queue_active = True

    ctrl.send("hola", "m")

    assert ctrl._spawn_worker.call_count == 0
    assert ctrl.status.emit.call_count >= 1


def test_p4_19_cancel_en_idle_no_cambia_estado():
    """P4#19: cancel() en IDLE no debe tocar el worker ni el estado."""
    ctrl = _new_ctrl()
    ctrl._worker = MagicMock()
    ctrl._set_state = MagicMock()

    ctrl.cancel()

    assert ctrl._set_state.call_count == 0
    assert ctrl._worker.cancel.call_count == 0


def test_p4_4_retry_no_duplica_user():
    """P4#4: resume_queue_retry no deja dos user en historial."""
    ctrl = _new_ctrl()
    ctrl._queue_paused = True
    ctrl._queue_total = 1
    ctrl._queue = []
    ctrl._current_prompt = "hola"
    ctrl._current_retry_count = 0
    ctrl.messages = [{"role": "user", "content": "hola"}]
    ctrl.queue_item_status_changed = MagicMock()
    ctrl.send = MagicMock()

    ctrl.resume_queue_retry()

    user_count = sum(1 for m in ctrl.messages
                     if m.get("role") == "user")
    assert user_count == 0, (
        "P4#4: el user previo debe eliminarse antes del retry"
    )


def test_p4_6_cancel_paused_limpia_phase_plan():
    """P4#6: cancelar cola pausada limpia _phase_plan."""
    ctrl = _new_ctrl()
    ctrl._queue_paused = True
    ctrl._queue_total = 1
    ctrl._queue = []
    ctrl._current_prompt = "x"
    ctrl._current_retry_count = 0
    ctrl._phase_plan = object()
    ctrl._phase_bodies = ["a", "b"]
    ctrl.queue_item_status_changed = MagicMock()
    ctrl._stop_queue_with_message = MagicMock()

    ctrl.cancel_paused_queue()

    assert ctrl._phase_plan is None
    assert ctrl._phase_bodies == []


def test_p4_10_clear_con_cola_pausada_cancela_antes():
    """P4#10: clear() con cola pausada cancela la cola primero."""
    ctrl = _new_ctrl()
    ctrl._queue_paused = True
    ctrl.cancel_paused_queue = MagicMock(return_value=True)
    ctrl.messages = [{"role": "user", "content": "x"}]
    ctrl._persist_timer = MagicMock()
    ctrl._persist_timer.isActive = lambda: False
    ctrl.store = MagicMock()
    ctrl._session_summary = MagicMock()
    ctrl.conversation_changed = MagicMock()

    ctrl.clear()

    assert ctrl.cancel_paused_queue.call_count == 1
