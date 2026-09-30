"""Grupo 1b-2: P2#3 — fold reconstruye historial completo.

Antes: fold_events solo pliegaba MessageCompleted de role=assistant
(unico que se emitia). Los roles user/tool/system iban directos a
_messages sin evento. Tras crash+resume el estado reconstruido
perdia ese contexto.

Ahora: session emite MessageCompleted(role=user|tool|system) en
los 9 sitios donde muta _messages.
"""
from __future__ import annotations

import pathlib
from collections.abc import Iterator

from core.harness.durable import EventLog, fold_events
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
        result: str = "resultado",
    ) -> None:
        self._requires = requires or set()
        self._result = result

    def requires_confirmation(self, name: str) -> bool:
        return name in self._requires

    def call(
        self, name, args, *, allow_destructive=False, cancel_event=None,
    ) -> str:
        return self._result


def _cfg(tmp_path: pathlib.Path, *, allowed=None) -> HarnessConfig:
    return HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="m"),
        agent=AgentSpec(name="a", allowed_tools=allowed),
    )


def _roles_in_log(log: EventLog) -> list[str]:
    return [
        e.role
        for e in log.read("r1")
        if e.kind == "message_completed"
    ]


# ── Emision de MessageCompleted por rol ─────────────────────────


def test_user_y_assistant_al_log(tmp_path) -> None:
    log = EventLog(tmp_path / "e.sqlite")
    try:
        s = HarnessSession(
            _cfg(tmp_path),
            model_client=_Model([]),
            event_log=log,
        )
        list(s.step("hola"))
        roles = _roles_in_log(log)
        assert "user" in roles
        assert "assistant" in roles
    finally:
        log.close()


def test_tool_al_log(tmp_path) -> None:
    log = EventLog(tmp_path / "e.sqlite")
    try:
        s = HarnessSession(
            _cfg(tmp_path),
            model_client=_Model([("t", {})]),
            tool_registry=_Reg(result="contenido"),
            event_log=log,
        )
        list(s.step("hola"))
        roles = _roles_in_log(log)
        assert "tool" in roles
    finally:
        log.close()


def test_system_correctivo_al_log(tmp_path) -> None:
    """Cuando el LoopDetector dispara corrective, el prompt system
    va al log."""
    from core.harness.loop import LoopDetector
    from core.harness.policy import LoopPolicy

    log = EventLog(tmp_path / "e.sqlite")
    try:
        # Repetimos la misma tool 6 veces para forzar corrective.
        script = [("t", {"x": 1}) for _ in range(6)]
        s = HarnessSession(
            _cfg(tmp_path),
            model_client=_Model(script),
            tool_registry=_Reg(result="mismo"),
            event_log=log,
            loop_detector=LoopDetector(LoopPolicy()),
        )
        list(s.step("repite"))
        roles = _roles_in_log(log)
        assert "system" in roles
    finally:
        log.close()


# ── Fold reconstruye el historial completo ──────────────────────


def test_fold_reconstruye_user_y_assistant(tmp_path) -> None:
    log = EventLog(tmp_path / "e.sqlite")
    try:
        s = HarnessSession(
            _cfg(tmp_path),
            model_client=_Model([]),
            event_log=log,
        )
        list(s.step("hola"))
        state = fold_events(log.read("r1"))
        roles = [m["role"] for m in state.messages]
        assert "user" in roles
        assert "assistant" in roles
    finally:
        log.close()


def test_fold_reconstruye_tool(tmp_path) -> None:
    log = EventLog(tmp_path / "e.sqlite")
    try:
        s = HarnessSession(
            _cfg(tmp_path),
            model_client=_Model([("t", {})]),
            tool_registry=_Reg(result="contenido"),
            event_log=log,
        )
        list(s.step("hola"))
        state = fold_events(log.read("r1"))
        roles = [m["role"] for m in state.messages]
        assert "user" in roles
        assert "tool" in roles
        tool_msgs = [m for m in state.messages if m["role"] == "tool"]
        assert tool_msgs[0]["content"] == "contenido"
    finally:
        log.close()


def test_fold_reconstruye_dos_steps(tmp_path) -> None:
    """Dos steps consecutivos: fold devuelve ambos user."""
    log = EventLog(tmp_path / "e.sqlite")
    try:
        s = HarnessSession(
            _cfg(tmp_path),
            model_client=_Model([]),
            event_log=log,
        )
        list(s.step("hola"))
        list(s.step("adios"))
        state = fold_events(log.read("r1"))
        user_msgs = [m for m in state.messages if m["role"] == "user"]
        assert len(user_msgs) == 2
        assert user_msgs[0]["content"] == "hola"
        assert user_msgs[1]["content"] == "adios"
    finally:
        log.close()


# ── Orden del log ───────────────────────────────────────────────


def test_step_started_antes_de_message_completed_user(tmp_path) -> None:
    log = EventLog(tmp_path / "e.sqlite")
    try:
        s = HarnessSession(
            _cfg(tmp_path),
            model_client=_Model([]),
            event_log=log,
        )
        list(s.step("hola"))
        kinds = [e.kind for e in log.read("r1")]
        i_step = kinds.index("step_started")
        i_msg = kinds.index("message_completed")
        assert i_step < i_msg
    finally:
        log.close()
