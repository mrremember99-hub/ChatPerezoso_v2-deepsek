"""P3#12, P3#13, P3#23."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")


# -- P3#12: reconnect pasa env ----------------------------------------

def test_reconnect_propaga_env():
    from ui.controllers.mcp_controller import MCPController

    ctrl = MCPController.__new__(MCPController)
    cfg = MagicMock()
    cfg.command = "npx"
    cfg.args = ("-y", "pkg")
    cfg.env = {"TOKEN": "abc"}

    ctrl._configs = {"srv": cfg}
    ctrl._threads = {}
    ctrl._dead = set()
    ctrl.status = MagicMock()
    ctrl._connect = MagicMock()

    ctrl.reconnect("srv")

    ctrl._connect.assert_called_once()
    args, kwargs = ctrl._connect.call_args
    assert kwargs.get("env") == {"TOKEN": "abc"}


# -- P3#13: _on_loaded respeta enabled=False ---------------------------

def test_on_loaded_con_disabled_no_activa():
    from ui.controllers.mcp_controller import MCPController

    ctrl = MCPController.__new__(MCPController)
    entry = MagicMock()
    entry.enabled = False
    ctrl.entries_by_id = lambda: {"srv": entry}
    ctrl.bridge = MagicMock()
    ctrl._emit_changed = MagicMock()
    ctrl.status = MagicMock()

    client = MagicMock()
    ctrl._on_loaded("srv", client, [{"function": {"name": "t"}}])

    ctrl.bridge.activate.assert_not_called()
    client.close.assert_called_once()


def test_on_loaded_con_enabled_activa():
    from ui.controllers.mcp_controller import MCPController

    ctrl = MCPController.__new__(MCPController)
    entry = MagicMock()
    entry.enabled = True
    ctrl.entries_by_id = lambda: {"srv": entry}
    ctrl.bridge = MagicMock()
    ctrl.bridge.definitions.return_value = []
    ctrl._emit_changed = MagicMock()
    ctrl.status = MagicMock()

    client = MagicMock()
    ctrl._on_loaded("srv", client, [])
    ctrl.bridge.activate.assert_called_once()


# -- P3#23: fallback contexto ----------------------------------------

def test_capabilities_error_resetea_context_limit():
    from ui.controllers.app_controller import AppController

    ctrl = AppController.__new__(AppController)
    ctrl._caps_generation = 1
    ctrl.view = MagicMock()
    ctrl.view.sidebar.current_model.return_value = "m1"
    ctrl.chat_ctrl = MagicMock()

    ctrl._on_capabilities_error("m1", "timeout", 1)
    ctrl.chat_ctrl.set_context_limit.assert_called_once_with(0)


def test_capabilities_error_generacion_vieja_no_toca():
    from ui.controllers.app_controller import AppController

    ctrl = AppController.__new__(AppController)
    ctrl._caps_generation = 5
    ctrl.view = MagicMock()
    ctrl.chat_ctrl = MagicMock()

    ctrl._on_capabilities_error("m1", "timeout", 1)
    ctrl.chat_ctrl.set_context_limit.assert_not_called()
