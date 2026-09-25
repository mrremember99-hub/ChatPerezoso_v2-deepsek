"""H2 auditoria 2026-09-26: presupuesto consistente con el trace."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")


def test_compact_reserva_trace_chars():
    from ui.controllers.chat_controller import ChatController

    ctrl = ChatController.__new__(ChatController)
    ctrl._last_system_prompt = "BASE"
    ctrl._TOOL_TRACE_MAX_TOTAL_CHARS = 3000
    ctrl._TRACE_RESERVE_CHARS = 3000
    ctrl.tools = MagicMock()
    ctrl.tools.definitions.return_value = []
    ctrl.messages = []
    # Accion previa: hay trace que reservar.
    ctrl._current_actions = [MagicMock()]

    fake_window = MagicMock()
    fake_window.fit.return_value = ([], MagicMock(dropped_messages=0))
    ctrl._get_context_window = MagicMock(return_value=fake_window)

    ctrl._compact_if_needed()

    assert fake_window.fit.called
    sys_arg = fake_window.fit.call_args.kwargs["system_prompt"]
    # Debe ser BASE + padding de 3000 espacios.
    assert sys_arg.startswith("BASE")
    assert len(sys_arg) == len("BASE") + 3000


def test_compact_sin_last_system_prompt():
    from ui.controllers.chat_controller import ChatController

    ctrl = ChatController.__new__(ChatController)
    ctrl._last_system_prompt = ""
    ctrl._TOOL_TRACE_MAX_TOTAL_CHARS = 3000
    ctrl._TRACE_RESERVE_CHARS = 3000
    ctrl.tools = MagicMock()
    ctrl.tools.definitions.return_value = []
    ctrl.messages = []
    ctrl._current_actions = [MagicMock()]

    fake_window = MagicMock()
    fake_window.fit.return_value = ([], MagicMock(dropped_messages=0))
    ctrl._get_context_window = MagicMock(return_value=fake_window)

    ctrl._compact_if_needed()

    sys_arg = fake_window.fit.call_args.kwargs["system_prompt"]
    assert sys_arg == " " * 3000


def test_compact_sin_acciones_no_reserva():
    """Sin acciones previas, _build_tool_trace devuelve "".

    No hay nada que reservar, asi que _compact_if_needed usa el
    system_prompt original tal cual. Esto evita aplastar ventanas
    pequeñas (limit=500) cuando no hay trace pendiente.
    """
    from ui.controllers.chat_controller import ChatController

    ctrl = ChatController.__new__(ChatController)
    ctrl._last_system_prompt = "BASE"
    ctrl._TOOL_TRACE_MAX_TOTAL_CHARS = 3000
    ctrl._TRACE_RESERVE_CHARS = 3000
    ctrl.tools = MagicMock()
    ctrl.tools.definitions.return_value = []
    ctrl.messages = []
    ctrl._current_actions = []

    fake_window = MagicMock()
    fake_window.fit.return_value = ([], MagicMock(dropped_messages=0))
    ctrl._get_context_window = MagicMock(return_value=fake_window)

    ctrl._compact_if_needed()

    sys_arg = fake_window.fit.call_args.kwargs["system_prompt"]
    assert sys_arg == "BASE"
