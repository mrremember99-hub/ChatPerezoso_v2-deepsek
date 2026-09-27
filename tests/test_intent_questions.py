"""H16 (2026-09-27): preguntas informativas no exponen tools."""
from __future__ import annotations

import pytest

from core.intent import ToolIntentGate
from core.tools import _RULES


@pytest.fixture
def gate():
    return ToolIntentGate(_RULES)


@pytest.fixture
def tools():
    return [{"type": "function", "function": {"name": "leer_archivo"}}]


@pytest.mark.parametrize("text", [
    "¿cómo leo un archivo en Python?",
    "¿qué es un archivo en Python?",
    "explícame cómo leer un archivo en Python",
    "cuándo debo leer un archivo",
])
def test_pregunta_informativa_no_expone(gate, tools, text):
    assert gate.tools_for_request(tools, text) is None


@pytest.mark.parametrize("text", [
    "¿puedes leer main.py?",
    "¿puedes leer el archivo gui.py?",
    "¿puedes leer config.json?",
])
def test_pregunta_con_filename_expone(gate, tools, text):
    assert gate.tools_for_request(tools, text) is not None


@pytest.mark.parametrize("text", [
    "lee main.py",
    "crea un archivo nota.txt",
])
def test_declarativa_expone(gate, tools, text):
    assert gate.tools_for_request(tools, text) is not None


def test_h1_continuacion_gana(gate, tools):
    last = "¿Quieres que lea el archivo main.py?"
    assert gate.tools_for_request(
        tools, "sigue", last_assistant=last,
    ) is not None


def test_execution_no_bloquea_por_pregunta(gate):
    """tool_is_requested sigue funcionando por verbos (H16 solo
    toca exposure, no execution)."""
    assert gate.tool_is_requested("leer_archivo", "lee main.py")
    # H16 no toca execution: una pregunta con verbo fuerte sigue
    # autorizando la tool en el gate de ejecucion. El modelo es
    # quien decide si llamarla o no.
    assert gate.tool_is_requested("leer_archivo", "¿puedes leer main.py?")


def test_search_provider_pregunta_informativa(tmp_path):
    """Integracion con SearchProvider."""
    from core.workspace import Workspace
    from plugins.search.provider import SearchProvider

    gate = ToolIntentGate(
        SearchProvider(Workspace(tmp_path)).intent_rules()
    )
    tools = [{"type": "function", "function": {"name": "buscar_en_workspace"}}]
    assert gate.tools_for_request(
        tools, "¿dónde está la capital de Asturias?"
    ) is None
    assert gate.tools_for_request(
        tools, "¿dónde está el archivo main.py?"
    ) is not None
