"""Tests de 'Enviar todo' con orquestación determinista.

Verifica que si el texto pegado contiene headers FASE N consecutivos,
se delega en ``send_user_input`` (orquestación) y no en el splitter
clásico por separadores ``---`` / ``===``.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

pytest.importorskip("PySide6")


def _make_app_controller(text: str):
    """AppController sin pasar por __init__, con view y controllers mock.

    Solo se monta lo mínimo que toca ``_on_send_all_requested``.
    """
    from ui.controllers.app_controller import AppController

    ctrl = AppController.__new__(AppController)
    ctrl.view = MagicMock()
    ctrl.view.sidebar.current_model.return_value = "gpt-oss:20b"
    ctrl.view.chat_panel.take_input.return_value = text

    agent = MagicMock()
    agent.options.return_value = {}
    agent.system_prompt = "sys"
    ctrl.agent_ctrl = MagicMock()
    ctrl.agent_ctrl.active_agent.return_value = agent

    ctrl.chat_ctrl = MagicMock()
    return ctrl


# -- con fases: delega en send_user_input -----------------------------------

def test_send_all_con_fases_delega_en_send_user_input():
    prompt = "FASE 1 — algo\n\nFASE 2 — otra cosa\n"
    ctrl = _make_app_controller(prompt)
    ctrl._on_send_all_requested()

    ctrl.chat_ctrl.send_user_input.assert_called_once()
    ctrl.chat_ctrl.enqueue.assert_not_called()
    ctrl.chat_ctrl.send.assert_not_called()
    # El texto íntegro se pasa a send_user_input, no troceado.
    assert ctrl.chat_ctrl.send_user_input.call_args.args[0] == prompt


def test_send_all_con_fases_no_toca_el_input_si_modelo_vacio():
    ctrl = _make_app_controller("FASE 1 — a\n\nFASE 2 — b\n")
    ctrl.view.sidebar.current_model.return_value = None
    ctrl._on_send_all_requested()

    ctrl.chat_ctrl.send_user_input.assert_not_called()
    ctrl.chat_ctrl.enqueue.assert_not_called()
    ctrl.view.chat_panel.take_input.assert_not_called()


# -- sin fases: comportamiento clásico intacto ------------------------------

def test_send_all_sin_fases_usa_split_prompts_y_enqueue():
    prompt = "uno\n---\ndos\n---\ntres"
    ctrl = _make_app_controller(prompt)
    ctrl._on_send_all_requested()

    ctrl.chat_ctrl.send_user_input.assert_not_called()
    ctrl.chat_ctrl.enqueue.assert_called_once()
    assert ctrl.chat_ctrl.enqueue.call_args.args[0] == ["uno", "dos", "tres"]


def test_send_all_sin_fases_un_solo_prompt_usa_send():
    ctrl = _make_app_controller("una sola cosa")
    ctrl._on_send_all_requested()

    ctrl.chat_ctrl.send_user_input.assert_not_called()
    ctrl.chat_ctrl.enqueue.assert_not_called()
    ctrl.chat_ctrl.send.assert_called_once()