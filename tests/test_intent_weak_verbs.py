"""Regresión: weak_verbs para "dónde" sin falsos positivos.

- Verbos fuertes ("busca", "encuentra") autorizan siempre.
- Verbos débiles ("dónde") autorizan solo si hay target.
- Preguntas generales con "dónde" no autorizan.
"""
from __future__ import annotations

import pytest

from core.intent import ToolIntentGate
from plugins.search import SearchProvider
from core.workspace import Workspace


@pytest.fixture
def gate(tmp_path):
    return ToolIntentGate(
        SearchProvider(Workspace(tmp_path)).intent_rules()
    )


def test_donde_pregunta_general_no_autoriza(gate):
    """¿dónde está Asturias? NO debe autorizar la búsqueda."""
    assert not gate.tool_is_requested(
        "buscar_en_workspace",
        "¿dónde está la capital de Asturias?",
    )


def test_donde_archivo_autoriza(gate):
    """¿dónde está el archivo X? SÍ debe autorizar."""
    assert gate.tool_is_requested(
        "buscar_en_workspace",
        "¿dónde está el archivo main.py?",
    )


def test_donde_funcion_autoriza(gate):
    """¿dónde está la función X? SÍ debe autorizar."""
    assert gate.tool_is_requested(
        "buscar_en_workspace",
        "¿dónde está la función saludar?",
    )


def test_donde_filename_solo_autoriza(gate):
    """¿dónde está main.py? El filename basta como target."""
    assert gate.tool_is_requested(
        "buscar_en_workspace",
        "¿dónde está main.py?",
    )


def test_busca_archivo_autoriza_sin_target(gate):
    """'busca' es verbo fuerte: autoriza incluso sin target_word."""
    assert gate.tool_is_requested(
        "buscar_en_workspace",
        "busca 'def test_'",
    )


def test_encuentra_sin_target_autoriza(gate):
    """'encuentra' es verbo fuerte: autoriza sin target."""
    assert gate.tool_is_requested(
        "buscar_en_workspace",
        "encuentra los TODO",
    )


def test_tools_for_request_no_autoriza_con_donde_general(gate):
    """Integración: tools_for_request debe devolver None si solo hay
    'dónde' sin target."""
    tools = [{"type": "function", "function": {"name": "buscar_en_workspace"}}]
    assert gate.tools_for_request(tools, "¿dónde está Asturias?") is None


def test_tools_for_request_autoriza_con_donde_y_archivo(gate):
    tools = [{"type": "function", "function": {"name": "buscar_en_workspace"}}]
    result = gate.tools_for_request(
        tools, "¿dónde está el archivo main.py?",
    )
    assert result is not None
    