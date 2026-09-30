"""Fix B: borrar_archivo nunca se auto-aprueba (run #5 OVERPAPER).

En el run #5, con autopilot ON, el modelo borró gui.py para
"arreglar" un f-string roto. El borrado de ficheros es la operacion
mas destructiva del workspace y no debe auto-aprobarse jamas.
"""
from __future__ import annotations

from ui.workers import ChatWorker


class _FakeTools:
    def requires_confirmation(self, _name: str) -> bool:
        return True

    def call(self, *_a, **_kw):
        return "ok"


class _FakeClient:
    pass


def _make(*, auto_approve: bool) -> ChatWorker:
    return ChatWorker(
        client=_FakeClient(),
        model="test",
        messages=[],
        tools=_FakeTools(),
        auto_approve=auto_approve,
    )


def test_borrar_archivo_no_auto_aprobado_con_autopilot():
    w = _make(auto_approve=True)
    assert w._is_auto_approved("borrar_archivo") is False


def test_borrar_archivo_no_auto_aprobado_sin_autopilot():
    w = _make(auto_approve=False)
    assert w._is_auto_approved("borrar_archivo") is False


def test_otras_tools_mantienen_auto_aprobacion():
    w = _make(auto_approve=True)
    assert w._is_auto_approved("escribir_archivo") is True
    assert w._is_auto_approved("editar_archivo") is True
    assert w._is_auto_approved("crear_archivo") is True


def test_shell_sigue_con_doble_puerta():
    w = _make(auto_approve=True)
    assert w._is_auto_approved("ejecutar_comando") is False  # sin auto_approve_shell
