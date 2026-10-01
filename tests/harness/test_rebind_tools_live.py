"""2026-10-01: rebind_tools propaga a la session viva."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject

from core.harness.policy import (
    AgentSpec,
    HarnessConfig,
    ModelSpec,
)
from core.harness.session import HarnessSession


class _NoopModel:
    def chat(self, *a, **kw):
        return iter([])


class _FakeTools:
    def definitions(self):
        return []

    def call(self, *a, **kw):
        return ""

    def requires_confirmation(self, name):
        return False

    def intent_rules(self):
        return {}


class _StubWorker:
    def __init__(self, session):
        self._session = session


def _make_ctrl():
    from ui.controllers.chat_controller import ChatController

    ctrl = ChatController(
        parent=QObject(),
        parent_widget=None,
        client=object(),
        tools=_FakeTools(),
        renderer=object(),
    )
    return ctrl


def _make_session(tmp_path):
    cfg = HarnessConfig(
        run_id="r1",
        workspace_root=tmp_path / "ws",
        storage_dir=tmp_path / "store",
        model=ModelSpec(name="fake"),
        agent=AgentSpec(name="test"),
    )
    return HarnessSession(cfg, model_client=_NoopModel())


def test_rebind_tools_propaga_a_session_viva(tmp_path):
    ctrl = _make_ctrl()
    old_tools = ctrl.tools
    session = _make_session(tmp_path)
    session.tool_registry = old_tools
    ctrl._worker = _StubWorker(session)

    new_tools = _FakeTools()
    ctrl.rebind_tools(new_tools)

    assert ctrl.tools is new_tools
    assert session.tool_registry is new_tools


def test_rebind_tools_sin_worker_no_explota():
    ctrl = _make_ctrl()
    new_tools = _FakeTools()
    ctrl.rebind_tools(new_tools)
    assert ctrl.tools is new_tools
