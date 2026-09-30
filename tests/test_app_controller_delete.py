"""UI backlog paso 2/2: AppController conecta delete_requested."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from ui.controllers import app_controller as app_mod  # noqa: E402
from ui.controllers.app_controller import AppController  # noqa: E402


class _FakeView:
    def __init__(self) -> None:
        self.statuses: list[str] = []

    def set_status(self, text: str) -> None:
        self.statuses.append(text)


class _FakeTools:
    def __init__(self, result: str = "Archivo borrado: a.txt") -> None:
        self.calls: list[tuple] = []
        self._result = result

    def call(self, name, arguments, **kwargs):
        self.calls.append((name, dict(arguments), dict(kwargs)))
        return self._result


def _make() -> AppController:
    ctrl = AppController.__new__(AppController)
    ctrl.view = _FakeView()
    ctrl.tools = _FakeTools()
    return ctrl


def test_confirm_false_no_borra(monkeypatch, qapp):
    monkeypatch.setattr(
        app_mod, "confirm_tool", lambda *a, **kw: False,
    )
    ctrl = _make()
    ctrl._on_delete_requested("/tmp/ws/a.txt")
    assert ctrl.tools.calls == []
    assert any("cancel" in s.lower() for s in ctrl.view.statuses)


def test_confirm_true_borra_con_allow_destructive(monkeypatch, qapp):
    monkeypatch.setattr(
        app_mod, "confirm_tool", lambda *a, **kw: True,
    )
    ctrl = _make()
    ctrl._on_delete_requested("/tmp/ws/a.txt")
    assert len(ctrl.tools.calls) == 1
    name, args, kwargs = ctrl.tools.calls[0]
    assert name == "borrar_archivo"
    assert args == {"path": "/tmp/ws/a.txt"}
    assert kwargs.get("allow_destructive") is True


def test_error_muestra_status_con_error(monkeypatch, qapp):
    monkeypatch.setattr(
        app_mod, "confirm_tool", lambda *a, **kw: True,
    )
    ctrl = _make()
    ctrl.tools = _FakeTools(result="ERROR: no se pudo borrar")
    ctrl._on_delete_requested("/tmp/ws/a.txt")
    assert any("error" in s.lower() for s in ctrl.view.statuses)


def test_exito_muestra_status_borrado(monkeypatch, qapp):
    monkeypatch.setattr(
        app_mod, "confirm_tool", lambda *a, **kw: True,
    )
    ctrl = _make()
    ctrl.tools = _FakeTools(result="Archivo borrado: a.txt")
    ctrl._on_delete_requested("/tmp/ws/a.txt")
    assert any("borrado" in s.lower() for s in ctrl.view.statuses)


def test_argumentos_al_confirmar(monkeypatch, qapp):
    captured: list = []

    def fake_confirm(parent, name, arguments):
        captured.append((parent, name, dict(arguments)))
        return False

    monkeypatch.setattr(app_mod, "confirm_tool", fake_confirm)
    ctrl = _make()
    ctrl._on_delete_requested("/tmp/ws/x.py")
    assert len(captured) == 1
    _parent, name, args = captured[0]
    assert name == "borrar_archivo"
    assert args == {"path": "/tmp/ws/x.py"}
