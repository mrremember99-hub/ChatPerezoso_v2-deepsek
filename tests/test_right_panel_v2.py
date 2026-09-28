"""Tests de RightPanelV2: contrato heredado + estructura de cards."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QFrame, QLabel

from ui.views.right_panel import RightPanel
from ui.views.right_panel_v2 import RightPanelV2


@pytest.fixture
def panel_v2(qapp):
    p = RightPanelV2()
    yield p
    p.deleteLater()
    qapp.processEvents()


def test_v2_inherits_from_v1():
    assert issubclass(RightPanelV2, RightPanel)


def test_v2_preserves_signals():
    for name in (
        "queue_retry_requested",
        "queue_skip_requested",
        "queue_cancel_requested",
        "mcp_toggle_requested",
    ):
        assert hasattr(RightPanelV2, name), f"falta senal {name}"


def test_v2_has_same_public_api(panel_v2):
    for name in (
        "set_mcp_servers",
        "set_busy",
        "set_workspace",
        "set_queue_list",
        "update_queue_item",
        "show_queue_paused",
    ):
        assert callable(getattr(panel_v2, name, None)), (
            f"falta metodo {name}"
        )


def test_v2_has_three_cards(panel_v2):
    cards = panel_v2.findChildren(QFrame, "Card")
    assert len(cards) == 3


def test_v2_card_titles(panel_v2):
    titles = [
        lbl.text()
        for lbl in panel_v2.findChildren(QLabel, "CardTitle")
    ]
    assert titles == ["MCP", "COLA DE PROMPTS", "ARCHIVOS"]


def test_v2_queue_card_hidden_when_empty(panel_v2):
    panel_v2.set_queue_list([])
    assert panel_v2._queue_card is not None
    assert not panel_v2._queue_card.isVisibleTo(panel_v2)


def test_v2_queue_card_visible_with_prompts(panel_v2):
    panel_v2.set_queue_list(["uno", "dos"])
    assert panel_v2._queue_card is not None
    assert panel_v2._queue_card.isVisibleTo(panel_v2)
    # El queue_title interno debe seguir oculto (redundante con
    # el CardTitle).
    assert not panel_v2.queue_title.isVisibleTo(panel_v2)


def test_v2_workspace_tree_still_works(panel_v2, tmp_path):
    (tmp_path / "src").mkdir()
    (tmp_path / "src" / "foo.py").write_text("x", encoding="utf-8")
    panel_v2.set_workspace(tmp_path)
    assert panel_v2.workspace_tree.rootIndex().isValid()
