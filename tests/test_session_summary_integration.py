"""Integración del resumen rolling en ChatController (Hueco 2).

Verifica el disparo cada 20 mensajes, el cap de 2 ciclos, la
inyección del bloque en el system prompt efectivo y la
tolerancia a fallos del modelo pequeño.
"""
from __future__ import annotations

from unittest.mock import MagicMock

from core.session_summary import SessionSummary
from ui.controllers.chat_controller import ChatController


def _fake_reply() -> str:
    return (
        "[RESUMEN DE LA SESIÓN]\n"
        "· Progreso: arreglado el bug de X\n"
        "· Archivos: alpha.py\n"
        "· Pendiente: tests de Y\n"
        "· Contexto: prefiere español\n"
    )


def _make_ctrl() -> ChatController:
    ctrl = ChatController.__new__(ChatController)
    ctrl.messages = []
    ctrl.status = MagicMock()
    ctrl.client = MagicMock()
    ctrl.client.chat = MagicMock(return_value=_fake_reply())
    ctrl._summary_model = "qwen3:1.7b"
    ctrl._session_summary = SessionSummary()
    return ctrl


def _fill(ctrl: ChatController, n: int) -> None:
    # Base absoluta: cada llamada numera desde len(messages), no
    # desde 0. Sin esto, dos _fill(20) producen "msg 0".."msg 19"
    # dos veces y los tests de incrementalidad tienen colisiones
    # de substring (auditoria D3, 2026-09-26).
    base = len(ctrl.messages)
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        ctrl.messages.append({"role": role, "content": f"msg {base + i}"})


def test_no_dispara_con_19_mensajes():
    ctrl = _make_ctrl()
    _fill(ctrl, 19)
    ctrl._maybe_update_summary()
    ctrl.client.chat.assert_not_called()
    assert ctrl._session_summary.text == ""
    assert ctrl._session_summary.cycles == 0


def test_dispara_con_20_mensajes():
    ctrl = _make_ctrl()
    _fill(ctrl, 20)
    ctrl._maybe_update_summary()
    assert ctrl.client.chat.call_count == 1
    assert ctrl._session_summary.text != ""
    assert ctrl._session_summary.cycles == 1
    assert ctrl._session_summary.last_message_count == 20


def test_rolling_continua_mas_alla_de_dos_ciclos():
    ctrl = _make_ctrl()
    for _ in range(4):
        _fill(ctrl, 20)
        ctrl._maybe_update_summary()
    assert ctrl.client.chat.call_count == 4
    assert ctrl._session_summary.cycles == 4
    assert ctrl._session_summary.last_message_count == 80


def test_resumen_se_inyecta_antes_del_system_base():
    ctrl = _make_ctrl()
    ctrl._state = MagicMock(is_active=False)
    ctrl.renderer = MagicMock()
    ctrl._append_message = MagicMock()
    ctrl._last_model = ""
    ctrl._last_options = {}
    ctrl._last_system_prompt = "SYSTEM BASE"
    ctrl._set_state = MagicMock()
    ctrl._spawn_worker = MagicMock()
    ctrl._current_actions = []
    # Forzamos resumen ya listo y bloqueamos el update
    ctrl._session_summary.text = "[RESUMEN DE LA SESIÓN]\n· Progreso: X"
    ctrl._session_summary.cycles = 2

    ctrl.send("hola", "m", None, "SYSTEM BASE")

    assert ctrl._spawn_worker.call_count == 1
    args = ctrl._spawn_worker.call_args[0]
    effective = args[2]
    assert effective.startswith("[RESUMEN DE LA SESIÓN]")
    assert "SYSTEM BASE" in effective
    assert effective.index("[RESUMEN DE LA SESIÓN]") < effective.index("SYSTEM BASE")


def test_excepcion_del_modelo_no_revienta():
    ctrl = _make_ctrl()
    _fill(ctrl, 20)
    ctrl.client.chat.side_effect = RuntimeError("boom")
    ctrl._maybe_update_summary()  # no debe lanzar
    assert ctrl._session_summary.text == ""
    assert ctrl._session_summary.cycles == 0


# -- D2/D3: incremental y sin cap ---------------------------------------


def test_resumen_incremental_no_reenvia_historial_completo():
    ctrl = _make_ctrl()
    _fill(ctrl, 20)
    ctrl._maybe_update_summary()
    _fill(ctrl, 20)  # total 40
    ctrl._maybe_update_summary()
    second_prompt = ctrl.client.chat.call_args_list[1][0][1][0]["content"]
    assert "msg 0" not in second_prompt
    assert "msg 20" in second_prompt


def test_segundo_ciclo_pasa_resumen_previo():
    ctrl = _make_ctrl()
    _fill(ctrl, 20)
    ctrl._maybe_update_summary()
    first_text = ctrl._session_summary.text
    assert first_text
    _fill(ctrl, 20)
    ctrl._maybe_update_summary()
    second_prompt = ctrl.client.chat.call_args_list[1][0][1][0]["content"]
    assert first_text in second_prompt
