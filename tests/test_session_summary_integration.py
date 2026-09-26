"""Integración del resumen rolling en ChatController (Hueco 2).

D1 (auditoria 2026-09-26): el cálculo se movió al ChatWorker. Los
tests aquí verifican:
  - send() NO llama al modelo de resumen (no bloquea UI).
  - send() prepara el prompt correcto cuando toca.
  - _on_summary_ready aplica el bloque en el hilo de UI.
  - El resumen se vuelve rolling sin cap y sin huecos.
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


def _make_ctrl_for_send():
    """Como _make_ctrl pero con _spawn_worker capturado y send() funcional."""
    ctrl = _make_ctrl()
    ctrl._state = MagicMock(is_active=False)
    ctrl.renderer = MagicMock()
    ctrl._set_state = MagicMock()
    ctrl._last_model = "m"
    ctrl._last_options = {}
    ctrl._last_system_prompt = ""
    ctrl._current_actions = []
    ctrl._build_tool_trace = lambda: ""
    ctrl._append_message = lambda msg: ctrl.messages.append(msg)
    captured = {"calls": []}

    def fake_spawn(model, options, system_prompt, summary_prompt="", summary_new_index=0):
        captured["calls"].append({
            "system_prompt": system_prompt,
            "summary_prompt": summary_prompt,
            "summary_new_index": summary_new_index,
        })

    ctrl._spawn_worker = fake_spawn
    return ctrl, captured


def _fill(ctrl: ChatController, n: int) -> None:
    # Base absoluta: cada llamada numera desde len(messages), no
    # desde 0 (auditoria D3, 2026-09-26).
    base = len(ctrl.messages)
    for i in range(n):
        role = "user" if i % 2 == 0 else "assistant"
        ctrl.messages.append({"role": role, "content": f"msg {base + i}"})


# -- send() no bloquea: solo prepara el prompt ------------------------


def test_send_no_llama_al_modelo_de_resumen():
    ctrl, captured = _make_ctrl_for_send()
    _fill(ctrl, 20)
    ctrl.send("hola", "m", None, "SYS")
    ctrl.client.chat.assert_not_called()
    assert len(captured["calls"]) == 1


def test_send_bajo_umbral_no_prepara_prompt():
    ctrl, captured = _make_ctrl_for_send()
    _fill(ctrl, 18)  # 18 + user = 19 < 20
    ctrl.send("hola", "m", None, "SYS")
    assert captured["calls"][0]["summary_prompt"] == ""
    assert captured["calls"][0]["summary_new_index"] == 0


def test_send_justo_en_umbral_prepara_prompt():
    ctrl, captured = _make_ctrl_for_send()
    _fill(ctrl, 19)  # 19 + user = 20
    ctrl.send("hola", "m", None, "SYS")
    assert captured["calls"][0]["summary_prompt"] != ""
    assert captured["calls"][0]["summary_new_index"] == 20


# -- _on_summary_ready aplica ----------------------------------------


def test_on_summary_ready_aplica_bloque():
    ctrl = _make_ctrl()
    ctrl._on_summary_ready(_fake_reply(), 20)
    assert ctrl._session_summary.cycles == 1
    assert ctrl._session_summary.text != ""
    assert ctrl._session_summary.last_message_count == 20


def test_on_summary_ready_raw_vacio_no_aplica():
    ctrl = _make_ctrl()
    ctrl._on_summary_ready("", 20)
    assert ctrl._session_summary.cycles == 0
    assert ctrl._session_summary.text == ""


def test_on_summary_ready_rolling_3_ciclos():
    ctrl = _make_ctrl()
    for i in range(1, 4):
        ctrl._on_summary_ready(_fake_reply(), 20 * i)
    assert ctrl._session_summary.cycles == 3
    assert ctrl._session_summary.last_message_count == 60


# -- incrementalidad --------------------------------------------------


def test_segundo_ciclo_prompt_lleva_resumen_previo():
    ctrl, captured = _make_ctrl_for_send()
    _fill(ctrl, 20)
    ctrl.send("hola", "m", None, "SYS")
    first = captured["calls"][0]
    ctrl._on_summary_ready(_fake_reply(), first["summary_new_index"])
    first_summary_text = ctrl._session_summary.text
    _fill(ctrl, 20)  # total 41
    ctrl.send("hola2", "m", None, "SYS")
    second_prompt = captured["calls"][1]["summary_prompt"]
    assert first_summary_text in second_prompt


def test_segundo_ciclo_no_reenvia_historial_viejo():
    ctrl, captured = _make_ctrl_for_send()
    _fill(ctrl, 20)
    ctrl.send("hola", "m", None, "SYS")
    ctrl._on_summary_ready(
        _fake_reply(), captured["calls"][0]["summary_new_index"]
    )
    _fill(ctrl, 20)  # total 41
    ctrl.send("hola2", "m", None, "SYS")
    second_prompt = captured["calls"][1]["summary_prompt"]
    assert "msg 0" not in second_prompt
    assert "msg 21" in second_prompt


# -- inyección en system prompt ---------------------------------------


def test_resumen_se_inyecta_despues_del_system_base():
    """H2 (auditoria 2026-09-26): el system base va PRIMERO.

    Antes el resumen (hasta 2k chars) se prependia al system base,
    desplazando las instrucciones del rol del agente hacia el final
    y degradando instruction-following. Ahora el rol va primero y
    el resumen se anade despues.
    """
    ctrl, captured = _make_ctrl_for_send()
    ctrl._session_summary.text = "[RESUMEN DE LA SESIÓN]\n· Progreso: X"
    ctrl._session_summary.last_message_count = 20
    ctrl._session_summary.cycles = 1
    _fill(ctrl, 19)
    ctrl.send("hola", "m", None, "SYSTEM BASE")
    sys_prompt = captured["calls"][0]["system_prompt"]
    assert "SYSTEM BASE" in sys_prompt
    assert "[RESUMEN DE LA SESIÓN]" in sys_prompt
    # Base primero, resumen despues.
    assert sys_prompt.index("SYSTEM BASE") < sys_prompt.index("[RESUMEN DE LA SESIÓN]")
