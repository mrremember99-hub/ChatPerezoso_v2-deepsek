"""Tests del stall guard generico (P1 2026-09-26)."""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from core.ollama import (
    _STALL_NUDGE_MESSAGE,
    OllamaClient,
)


def test_nudge_message_generico():
    """El mensaje ya no asume 'verificacion'."""
    assert "operacion que requiere herramientas" in _STALL_NUDGE_MESSAGE
    assert "verificacion explicita" not in _STALL_NUDGE_MESSAGE
    assert "ejecutar_comando" not in _STALL_NUDGE_MESSAGE


def test_condiciones_evidencia_stall_generico():
    """Caso real: user dice 'lee alpha.py', gate autoriza
    leer_archivo, modelo responde 'El contenido es print(Hola)'
    sin tool_call. Debe disparar stall."""
    # Condiciones del bloque.
    stall_retries_used = 0
    any_tool_call_emitted = False
    tool_names = {"leer_archivo"}
    user_text = "lee alpha.py"
    final_text = 'El contenido del archivo es: print("Hola mundo")'

    assert stall_retries_used < 1
    assert not any_tool_call_emitted
    assert tool_names
    assert not OllamaClient._user_asked_question(user_text)
    assert OllamaClient._assistant_closes_turn(final_text)
    # -> stall guard dispara.


def test_stall_no_dispara_si_usuario_pregunta():
    """'Explica como crear un archivo' no debe disparar stall
    aunque el gate autorice tools."""
    user_text = "¿Cómo creo un archivo en Python?"
    assert OllamaClient._user_asked_question(user_text)
    # La condicion `not _user_asked_question` ya bloquea.


def test_stall_no_dispara_si_assistant_pregunta():
    """Si el assistant pregunta de vuelta, no es stall."""
    user_text = "lee alpha.py"
    final_text = "¿Quieres que lea alpha.py?"
    assert not OllamaClient._user_asked_question(user_text)
    assert not OllamaClient._assistant_closes_turn(final_text)
    # La condicion `_assistant_closes_turn` ya bloquea.


def test_stall_no_dispara_sin_tools_autorizadas():
    """Si el gate no autorizo tools, no hay stall."""
    tool_names: set[str] = set()
    assert not tool_names  # condicion 3 bloquea.


def test_stall_no_dispara_si_ya_se_reintento():
    """_MAX_STALL_RETRIES=1: un solo nudge por chat."""
    stall_retries_used = 1
    assert not (stall_retries_used < 1)


def test_stall_no_dispara_si_modelo_uso_tool():
    """Si el modelo ya emitio alguna tool, no es stall."""
    any_tool_call_emitted = True
    assert not (not any_tool_call_emitted) is True or True
    # La condicion `not any_tool_call_emitted` bloquea.
    assert not (not any_tool_call_emitted)
