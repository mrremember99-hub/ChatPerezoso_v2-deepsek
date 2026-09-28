"""Tests de la feature editar cola (2026-09-28).

Cubre ChatController.queue_edit_item / queue_remove_item / queue_move_item.
Validacion: solo pendientes, no orquestacion, no fuera de rango.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from ui.controllers.chat_controller import ChatController


def _make_ctrl(queue, total=None, active=True, phase_plan=None):
    """ChatController minimo via __new__ (patron de otros tests)."""
    ctrl = ChatController.__new__(ChatController)
    ctrl._queue = list(queue)
    ctrl._queue_total = total if total is not None else (len(queue) + 1)
    ctrl._queue_active = active
    ctrl._phase_plan = phase_plan
    ctrl.status = type("S", (), {"emit": lambda self, msg: None})()
    return ctrl


# -- edit ------------------------------------------------------------------

def test_edit_pending_updates_queue():
    ctrl = _make_ctrl(["a", "b", "c"], total=4)  # current=2, slot 0 = idx 3
    assert ctrl.queue_edit_item(3, "B2")
    assert ctrl._queue[0] == "B2"


def test_edit_rejects_empty_text():
    ctrl = _make_ctrl(["a", "b"], total=3)
    assert not ctrl.queue_edit_item(2, "   ")


def test_edit_rejects_already_sent():
    ctrl = _make_ctrl(["a", "b"], total=3)  # current=2
    assert not ctrl.queue_edit_item(1, "X")


def test_edit_rejects_current():
    ctrl = _make_ctrl(["a", "b"], total=3)
    assert not ctrl.queue_edit_item(2, "X")


def test_edit_rejects_out_of_range():
    ctrl = _make_ctrl(["a"], total=2)
    assert not ctrl.queue_edit_item(99, "X")


def test_edit_rejects_when_no_active_queue():
    ctrl = _make_ctrl(["a"], total=2, active=False)
    assert not ctrl.queue_edit_item(2, "X")


def test_edit_rejects_with_phase_plan():
    ctrl = _make_ctrl(["a"], total=2, phase_plan=object())
    assert not ctrl.queue_edit_item(2, "X")


# -- remove ----------------------------------------------------------------

def test_remove_pending_decrements_total():
    ctrl = _make_ctrl(["a", "b", "c"], total=4)
    assert ctrl.queue_remove_item(3)
    assert ctrl._queue == ["b", "c"]
    assert ctrl._queue_total == 3


def test_remove_rejects_already_sent():
    ctrl = _make_ctrl(["a", "b"], total=3)
    assert not ctrl.queue_remove_item(1)


def test_remove_rejects_out_of_range():
    ctrl = _make_ctrl(["a"], total=2)
    assert not ctrl.queue_remove_item(5)


# -- move ------------------------------------------------------------------

def test_move_down_swaps():
    ctrl = _make_ctrl(["a", "b", "c"], total=4)
    assert ctrl.queue_move_item(3, +1)
    assert ctrl._queue == ["b", "a", "c"]


def test_move_up_swaps():
    ctrl = _make_ctrl(["a", "b", "c"], total=4)
    assert ctrl.queue_move_item(4, -1)
    assert ctrl._queue == ["b", "a", "c"]


def test_move_rejects_at_edge():
    ctrl = _make_ctrl(["a", "b"], total=3)
    # idx 3 es el primero pendiente; subir a idx 2 (current) falla
    assert not ctrl.queue_move_item(3, -1)
    # idx 4 es el ultimo pendiente; bajar a idx 5 falla
    assert not ctrl.queue_move_item(4, +1)


def test_move_rejects_invalid_delta():
    ctrl = _make_ctrl(["a", "b", "c"], total=4)
    assert not ctrl.queue_move_item(3, +2)
    assert not ctrl.queue_move_item(3, 0)


def test_move_rejects_when_sent():
    ctrl = _make_ctrl(["a", "b"], total=3)
    assert not ctrl.queue_move_item(1, +1)
