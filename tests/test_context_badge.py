"""Tests del badge de contexto (Hueco 4)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")


def test_context_summary_sin_budget():
    from ui.controllers.chat_controller import ChatController
    ctrl = ChatController.__new__(ChatController)
    ctrl._last_budget = None
    assert ctrl.context_summary() == (0, 0, 0)


def test_context_summary_con_budget():
    from ui.controllers.chat_controller import ChatController
    from core.context_window import ContextBudget
    ctrl = ChatController.__new__(ChatController)
    ctrl._last_budget = ContextBudget(
        limit_tokens=32000,
        output_reserve=1024,
        prompt_budget=26000,
        estimated_prompt=12400,
        dropped_messages=3,
    )
    assert ctrl.context_summary() == (12400, 26000, 3)


def test_sidebar_set_context_usage_oculta_si_budget_cero():
    from PySide6.QtWidgets import QApplication
    _ = QApplication.instance() or QApplication([])
    from ui.views.sidebar import Sidebar
    sb = Sidebar()
    sb.set_context_usage(100, 0, 0)
    assert not sb.context_usage_label.isVisible()


def test_sidebar_set_context_usage_muestra():
    from PySide6.QtWidgets import QApplication
    _ = QApplication.instance() or QApplication([])
    from ui.views.sidebar import Sidebar
    sb = Sidebar()
    sb.set_context_usage(12400, 26000, 3)
    assert "Contexto: 12.4k / 26.0k" in sb.context_usage_label.text()
    assert "3 podados" in sb.context_usage_label.text()
    assert sb.context_usage_label.property("state") == "ok"


def test_sidebar_estado_mid():
    from PySide6.QtWidgets import QApplication
    _ = QApplication.instance() or QApplication([])
    from ui.views.sidebar import Sidebar
    sb = Sidebar()
    sb.set_context_usage(20000, 26000, 0)
    assert sb.context_usage_label.property("state") == "mid"


def test_sidebar_estado_high():
    from PySide6.QtWidgets import QApplication
    _ = QApplication.instance() or QApplication([])
    from ui.views.sidebar import Sidebar
    sb = Sidebar()
    sb.set_context_usage(25000, 26000, 10)
    assert sb.context_usage_label.property("state") == "high"


def test_format_tokens():
    from ui.views.sidebar import _format_tokens
    assert _format_tokens(0) == "0"
    assert _format_tokens(999) == "999"
    assert _format_tokens(1000) == "1.0k"
    assert _format_tokens(12400) == "12.4k"
