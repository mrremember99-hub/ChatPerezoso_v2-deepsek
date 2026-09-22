"""Tests del estado busy de la sidebar."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from ui.views.sidebar import Sidebar


@pytest.fixture
def sidebar(qapp):
    s = Sidebar()
    yield s
    s.close()
    qapp.processEvents()


def test_sidebar_enabled_by_default(sidebar):
    assert sidebar.model_combo.isEnabled()
    assert sidebar.agent_combo.isEnabled()
    assert sidebar.agent_new_button.isEnabled()
    assert sidebar.agent_edit_button.isEnabled()
    assert sidebar.auto_approve_check.isEnabled()


def test_set_busy_disables_controls(sidebar):
    sidebar.set_busy(True)
    assert not sidebar.model_combo.isEnabled()
    assert not sidebar.agent_combo.isEnabled()
    assert not sidebar.agent_new_button.isEnabled()
    assert not sidebar.agent_edit_button.isEnabled()
    assert not sidebar.auto_approve_check.isEnabled()


def test_set_busy_false_reenables_controls(sidebar):
    sidebar.set_busy(True)
    sidebar.set_busy(False)
    assert sidebar.model_combo.isEnabled()
    assert sidebar.agent_combo.isEnabled()
    assert sidebar.agent_new_button.isEnabled()
    assert sidebar.agent_edit_button.isEnabled()
    assert sidebar.auto_approve_check.isEnabled()
