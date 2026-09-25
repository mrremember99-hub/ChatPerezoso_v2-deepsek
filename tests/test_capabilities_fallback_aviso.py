"""H8: aviso cuando /api/show no expone context_length."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")


def _make_ctrl():
    from ui.controllers.app_controller import AppController

    ctrl = AppController.__new__(AppController)
    ctrl._caps_generation = 1
    ctrl.view = MagicMock()
    ctrl.view.sidebar.current_model.return_value = "test-model"
    ctrl.chat_ctrl = MagicMock()
    return ctrl


def test_aviso_cuando_context_length_es_cero():
    ctrl = _make_ctrl()
    caps = MagicMock()
    caps.tool_mode = "native"
    caps.probed = True
    caps.context_length = 0
    caps.source = "probe"
    ctrl._on_capabilities_ready("test-model", caps, 1)

    ctrl.view.set_status.assert_called_once()
    msg = ctrl.view.set_status.call_args.args[0]
    assert "Ventana de contexto desconocida" in msg
    assert "4096" in msg
    # set_context_limit se sigue llamando (0), no se evita.
    ctrl.chat_ctrl.set_context_limit.assert_called_once_with(0)


def test_no_aviso_cuando_context_length_conocido():
    ctrl = _make_ctrl()
    caps = MagicMock()
    caps.tool_mode = "native"
    caps.probed = True
    caps.context_length = 131072
    caps.source = "probe"
    ctrl._on_capabilities_ready("test-model", caps, 1)

    # Solo puede haberse llamado por el override (aqui source=probe).
    assert not ctrl.view.set_status.called


def test_no_aviso_si_no_probed():
    ctrl = _make_ctrl()
    caps = MagicMock()
    caps.tool_mode = "unknown"
    caps.probed = False
    caps.context_length = 0
    caps.source = "fallback"
    ctrl._on_capabilities_ready("test-model", caps, 1)

    # Si no pudimos probar, no avisamos: ya hay otro aviso de
    # capabilities desconocidas.
    assert not ctrl.view.set_status.called
