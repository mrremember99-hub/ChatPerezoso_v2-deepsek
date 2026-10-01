"""S5-c-mini: completion verification en session."""
from __future__ import annotations

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
    def requires_confirmation(self, name):
        return False

    def call(self, name, arguments, **kwargs):
        return f"ok:{name}"


_PROMPT_FASE = """
FASE 1 — Ventana basica
━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━━

Crea gui.py.

━━━ VERIFICACIÓN FASE 1 ━━━
python -m py_compile gui.py
"""


def _cfg(tmp_path, **kw):
    return HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="fake"),
        agent=AgentSpec(name="test"),
        auto_approve=True,
        **kw,
    )


def _text(s):
    return ModelDelta(kind="text", text=s)


def _call(name, args=None):
    return ModelDelta(
        kind="tool_call",
        tool_call={"name": name, "arguments": args or {}},
    )


def test_completion_off_por_defecto_no_emite(tmp_path):
    model = _ScriptedModel([[_text("fin")]])
    s = HarnessSession(
        _cfg(tmp_path),
        model_client=model,
        tool_registry=_FakeRegistry(),
    )
    events = list(s.step(_PROMPT_FASE))
    kinds = [e.kind for e in events]
    assert "verification_run" not in kinds


def test_completion_on_prompt_sin_fases_no_emite(tmp_path):
    model = _ScriptedModel([[_text("fin")]])
    s = HarnessSession(
        _cfg(tmp_path, completion_verification_enabled=True),
        model_client=model,
        tool_registry=_FakeRegistry(),
    )
    events = list(s.step("Hola, ¿cómo estás?"))
    kinds = [e.kind for e in events]
    assert "verification_run" not in kinds


def test_completion_on_prompt_con_fases_emite(tmp_path):
    model = _ScriptedModel([[_text("FASE VERIFICADA")]])
    s = HarnessSession(
        _cfg(tmp_path, completion_verification_enabled=True),
        model_client=model,
        tool_registry=_FakeRegistry(),
    )
    events = list(s.step(_PROMPT_FASE))
    vrs = [e for e in events if e.kind == "verification_run"]
    assert len(vrs) == 1
    assert vrs[0].target == "phase-1"


def test_completion_detecta_sin_tools(tmp_path):
    """Prompt con fase, modelo responde texto sin tools."""
    model = _ScriptedModel([[_text("FASE VERIFICADA")]])
    s = HarnessSession(
        _cfg(tmp_path, completion_verification_enabled=True),
        model_client=model,
        tool_registry=_FakeRegistry(),
    )
    events = list(s.step(_PROMPT_FASE))
    vrs = [e for e in events if e.kind == "verification_run"]
    assert len(vrs) == 1
    # phase sin required_tools -> issues vacias (status verified)
    # Documentamos el comportamiento actual.
    assert vrs[0].issues == []


# -- P1.4: flag se propaga desde el controller -----------------


def test_set_completion_verification_enabled():
    """P1.4: el setter guarda el flag para el proximo worker."""
    import pytest
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QObject
    from ui.controllers.chat_controller import ChatController

    class _T:
        def definitions(self): return []
        def call(self, *a, **kw): return ""
        def requires_confirmation(self, name): return False
        def intent_rules(self): return {}

    ctrl = ChatController(
        parent=QObject(),
        parent_widget=None,
        client=object(),
        tools=_T(),
        renderer=object(),
    )
    assert ctrl._completion_verification_enabled is False
    ctrl.set_completion_verification_enabled(True)
    assert ctrl._completion_verification_enabled is True


def test_harness_config_recibe_completion_flag(tmp_path):
    """P1.4: _build_harness_worker pasa el flag al HarnessConfig."""
    import pytest
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QObject
    from ui.controllers.chat_controller import ChatController

    class _T:
        def definitions(self): return []
        def call(self, *a, **kw): return ""
        def requires_confirmation(self, name): return False
        def intent_rules(self): return {}

    ctrl = ChatController(
        parent=QObject(),
        parent_widget=None,
        client=object(),
        tools=_T(),
        renderer=object(),
    )
    ctrl.messages = [{"role": "user", "content": "hola"}]
    ctrl._loop_detection_enabled = False
    ctrl._completion_verification_enabled = True

    worker = ctrl._build_harness_worker("m1", None, "sys")
    assert worker._session.config.completion_verification_enabled is True
