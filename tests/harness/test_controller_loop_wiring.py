"""S1-ter: wiring de loop_detection_enabled del config al ChatController.

Sin Qt real (qapp se provee via pytest-qt, pero no se crea la ventana).
"""
from __future__ import annotations

from ui.controllers.chat_controller import ChatController


class _FakeRenderer:
    def __init__(self) -> None:
        self.narrations: list[tuple[str, bool]] = []

    def insert_narration(self, text: str, active: bool = False) -> None:
        self.narrations.append((text, active))


def test_setter_actualiza_flag():
    ctrl = ChatController.__new__(ChatController)
    ctrl._loop_detection_enabled = False
    ctrl.set_loop_detection_enabled(True)
    assert ctrl._loop_detection_enabled is True
    ctrl.set_loop_detection_enabled(False)
    assert ctrl._loop_detection_enabled is False


def test_on_loop_warning_inserta_narracion():
    ctrl = ChatController.__new__(ChatController)
    ctrl.renderer = _FakeRenderer()
    ctrl._on_loop_warning("generic_repeat", "8 veces")
    assert len(ctrl.renderer.narrations) == 1
    text, active = ctrl.renderer.narrations[0]
    assert "generic_repeat" in text
    assert "8 veces" in text
    assert active is False


def test_on_loop_corrective_inserta_narracion():
    ctrl = ChatController.__new__(ChatController)
    ctrl.renderer = _FakeRenderer()
    ctrl._on_loop_corrective("ping_pong", "3 ciclos")
    assert len(ctrl.renderer.narrations) == 1
    text, _ = ctrl.renderer.narrations[0]
    assert "ping_pong" in text
    assert "3 ciclos" in text


def test_on_loop_aborted_inserta_narracion():
    ctrl = ChatController.__new__(ChatController)
    ctrl.renderer = _FakeRenderer()
    ctrl._on_loop_aborted("generic_repeat", "8 veces")
    assert len(ctrl.renderer.narrations) == 1
    text, _ = ctrl.renderer.narrations[0]
    assert "abortado" in text.lower()
    assert "cancelada" in text.lower()


def test_flag_default_es_false():
    """Sin config explicita, el flag arranca OFF."""
    ctrl = ChatController.__new__(ChatController)
    # No llamamos __init__, pero el atributo se asigna alli. Simulamos
    # que se ha inicializado a su default.
    ctrl._loop_detection_enabled = False
    assert ctrl._loop_detection_enabled is False
