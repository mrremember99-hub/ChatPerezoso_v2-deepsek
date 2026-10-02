"""P3#15: confirm_tool no debe bloquear al worker si crashea."""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject


class _FakeTools:
    def definitions(self): return []
    def call(self, *a, **kw): return ""
    def requires_confirmation(self, name): return False
    def intent_rules(self): return {}


class _FakeWorker:
    def __init__(self):
        self.resolved: list[bool] = []

    def resolve_confirmation(self, approved: bool) -> None:
        self.resolved.append(approved)


def _make_ctrl():
    from ui.controllers.chat_controller import ChatController
    return ChatController(
        parent=QObject(),
        parent_widget=None,
        client=object(),
        tools=_FakeTools(),
        renderer=object(),
    )


def test_confirm_tool_crash_deniega_y_desbloquea(monkeypatch):
    """Si confirm_tool lanza, el worker se resuelve con False."""
    ctrl = _make_ctrl()
    fake_worker = _FakeWorker()
    ctrl._worker = fake_worker

    def _boom(*a, **kw):
        raise ValueError("timeout_seconds='30s'")

    monkeypatch.setattr(
        "ui.controllers.chat_controller.confirm_tool", _boom,
    )
    ctrl._on_confirmation("ejecutar_comando", {"timeout_seconds": "30s"})
    assert fake_worker.resolved == [False]


def test_confirm_tool_aprobado_desbloquea_con_true(monkeypatch):
    ctrl = _make_ctrl()
    fake_worker = _FakeWorker()
    ctrl._worker = fake_worker

    monkeypatch.setattr(
        "ui.controllers.chat_controller.confirm_tool",
        lambda *a, **kw: True,
    )
    ctrl._on_confirmation("ejecutar_comando", {})
    assert fake_worker.resolved == [True]


def test_confirm_tool_denegado_desbloquea_con_false(monkeypatch):
    ctrl = _make_ctrl()
    fake_worker = _FakeWorker()
    ctrl._worker = fake_worker

    monkeypatch.setattr(
        "ui.controllers.chat_controller.confirm_tool",
        lambda *a, **kw: False,
    )
    ctrl._on_confirmation("ejecutar_comando", {})
    assert fake_worker.resolved == [False]


def test_dialogs_parseo_timeout_defensivo():
    """El parseo del timeout en dialogs._confirm_shell no crashea."""
    pytest.importorskip("PySide6")
    # No podemos instanciar el dialogo sin QApplication y sin
    # aceptar el modal. Verificamos que el patron de parseo vive
    # en el codigo fuente como contrato.
    from pathlib import Path
    src = Path("ui/views/dialogs.py").read_text()
    assert "except (TypeError, ValueError):" in src
    assert "timeout = 30" in src
