"""Tests para la extensión de piloto automático a `ejecutar_comando`.

Diseño:
  - `auto_approve` sigue gobernando todo excepto shell.
  - `auto_approve_shell` (nuevo) es opt-in explícito para shell.
  - El shell solo se auto-aprueba si AMBOS están activos.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtWidgets import QApplication


@pytest.fixture(scope="module")
def qapp():
    app = QApplication.instance()
    if app is None:
        app = QApplication([])
    return app


# -- Sidebar: cascada y estado ----------------------------------------


def test_shell_checkbox_deshabilitado_por_defecto(qapp):
    from ui.views.sidebar import Sidebar
    s = Sidebar()
    assert not s.auto_approve_check.isChecked()
    assert not s.auto_approve_shell_check.isChecked()
    assert not s.auto_approve_shell_check.isEnabled()


def test_activar_piloto_habilita_shell_but_no_lo_activa(qapp):
    from ui.views.sidebar import Sidebar
    s = Sidebar()
    s.auto_approve_check.setChecked(True)
    assert s.auto_approve_shell_check.isEnabled()
    assert not s.auto_approve_shell_check.isChecked()


def test_apagar_piloto_desactiva_shell_en_cascada(qapp):
    from ui.views.sidebar import Sidebar
    s = Sidebar()
    s.auto_approve_check.setChecked(True)
    s.auto_approve_shell_check.setChecked(True)
    assert s.is_auto_approve_shell()

    s.auto_approve_check.setChecked(False)
    assert not s.is_auto_approve_shell()
    assert not s.auto_approve_shell_check.isEnabled()


def test_set_auto_approve_shell_sin_piloto_se_ignora(qapp):
    from ui.views.sidebar import Sidebar
    s = Sidebar()
    s.set_auto_approve_shell(True)
    assert not s.auto_approve_shell_check.isChecked()


def test_set_auto_approve_shell_con_piloto_activo(qapp):
    from ui.views.sidebar import Sidebar
    s = Sidebar()
    s.set_auto_approve(True)
    s.set_auto_approve_shell(True)
    assert s.is_auto_approve_shell()


def test_shell_signal_emitida_al_apagar_piloto(qapp):
    from ui.views.sidebar import Sidebar
    s = Sidebar()
    s.auto_approve_check.setChecked(True)
    s.auto_approve_shell_check.setChecked(True)

    received = []
    s.auto_approve_shell_changed.connect(received.append)

    s.auto_approve_check.setChecked(False)
    assert received == [False]


# -- ChatWorker._is_auto_approved -------------------------------------


class _FakeTools:
    def requires_confirmation(self, name):
        return True
    def call(self, *a, **k):
        return "ok"


class _FakeClient:
    pass


def _make_worker(*, auto_approve: bool, auto_approve_shell: bool):
    from ui.workers import ChatWorker
    return ChatWorker(
        client=_FakeClient(),
        model="test",
        messages=[],
        tools=_FakeTools(),
        auto_approve=auto_approve,
        auto_approve_shell=auto_approve_shell,
    )


def test_worker_archivos_auto_aprobados_con_piloto(qapp):
    w = _make_worker(auto_approve=True, auto_approve_shell=False)
    assert w._is_auto_approved("crear_archivo") is True
    assert w._is_auto_approved("borrar_archivo") is True


def test_worker_shell_no_auto_aprobado_sin_flag(qapp):
    w = _make_worker(auto_approve=True, auto_approve_shell=False)
    assert w._is_auto_approved("ejecutar_comando") is False


def test_worker_shell_auto_aprobado_con_ambos(qapp):
    w = _make_worker(auto_approve=True, auto_approve_shell=True)
    assert w._is_auto_approved("ejecutar_comando") is True


def test_worker_shell_requiere_piloto_principal(qapp):
    """Doble puerta: si el piloto está apagado, la extensión de shell
    no basta para auto-aprobar `ejecutar_comando`."""
    w = _make_worker(auto_approve=False, auto_approve_shell=True)
    assert w._is_auto_approved("ejecutar_comando") is False
    assert w._is_auto_approved("crear_archivo") is False


# -- Config: round-trip -----------------------------------------------


def test_config_round_trip(tmp_path, monkeypatch):
    from core import config as config_mod

    fake_file = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_FILE", fake_file)

    cfg = config_mod.AppConfig(
        auto_approve_tools=True, auto_approve_shell=True,
    )
    cfg.save()

    loaded = config_mod.AppConfig.load()
    assert loaded.auto_approve_tools is True
    assert loaded.auto_approve_shell is True


def test_config_default_off(tmp_path, monkeypatch):
    from core import config as config_mod

    fake_file = tmp_path / "config.json"
    monkeypatch.setattr(config_mod, "CONFIG_FILE", fake_file)

    cfg = config_mod.AppConfig()
    cfg.save()

    loaded = config_mod.AppConfig.load()
    assert loaded.auto_approve_tools is False
    assert loaded.auto_approve_shell is False
