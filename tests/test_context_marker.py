"""Regresión #2: marcador de poda de contexto.

Cuando ContextWindow.fit() elimina mensajes completos, el system
prompt debe llevar un marcador que informe al modelo. Sin él, el
modelo sigue razonando como si tuviera el historial completo.
"""
from __future__ import annotations

from core.context_window import ContextWindow


def _make_window():
    # Límite pequeño para forzar poda fácilmente.
    return ContextWindow(limit_tokens=512, output_reserve=64)


def test_fit_sin_poda_no_tiene_dropped():
    w = _make_window()
    msgs = [
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "hola"},
    ]
    pruned, budget = w.fit(
        system_prompt="",
        tool_definitions=[],
        messages=msgs,
    )
    assert budget.dropped_messages == 0


def test_fit_con_poda_reporta_dropped():
    w = _make_window()
    # Muchos mensajes largos que no caben.
    msgs = []
    for i in range(20):
        msgs.append({"role": "user", "content": "x" * 300})
        msgs.append({"role": "assistant", "content": "y" * 300})
    pruned, budget = w.fit(
        system_prompt="",
        tool_definitions=[],
        messages=msgs,
    )
    assert budget.dropped_messages > 0


def test_marker_se_inserta_en_system_prompt():
    from core.ollama import OllamaClient
    w = _make_window()
    # Historial con system + muchos mensajes largos.
    history = [
        {"role": "system", "content": "Eres un asistente."},
    ]
    for i in range(20):
        history.append({"role": "user", "content": "x" * 300})
        history.append({"role": "assistant", "content": "y" * 300})

    pruned = OllamaClient._fit_round_history(
        w, history, tool_definitions=[],
    )
    # El primer mensaje debe ser system y contener el marcador.
    assert pruned[0]["role"] == "system"
    assert "CONTEXTO RECORTADO" in pruned[0]["content"]


def test_marker_no_aparece_sin_poda():
    from core.ollama import OllamaClient
    w = _make_window()
    history = [
        {"role": "system", "content": "Eres un asistente."},
        {"role": "user", "content": "hola"},
        {"role": "assistant", "content": "hola"},
    ]
    pruned = OllamaClient._fit_round_history(
        w, history, tool_definitions=[],
    )
    assert "CONTEXTO RECORTADO" not in pruned[0]["content"]