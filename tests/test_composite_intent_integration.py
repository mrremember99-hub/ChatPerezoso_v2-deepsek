"""Tests de integración: el composite + gate deben dejar pasar las
herramientas locales.

Este test existe por un bug real: MCPToolBridge.intent_rules() no
devolvía las reglas del núcleo, así que CompositeToolProvider construía
un gate sin las reglas de `listar_carpeta`, `crear_archivo`, etc.
Resultado: el modelo recibía 0 tools aunque el composite tuviera 12.
Los tests unitarios no lo pillaron porque probaban cada pieza aislada.
"""
from __future__ import annotations

import pytest

from core.composite_tools import CompositeToolProvider
from core.tools import ToolRegistry
from core.workspace import Workspace
from plugins.mcp import MCPToolBridge


@pytest.fixture
def composite(tmp_path):
    ws = Workspace(tmp_path)
    tools = ToolRegistry(ws)
    mcp = MCPToolBridge(tools)
    return CompositeToolProvider([mcp])


def test_core_tools_are_offered_when_user_asks_to_list(composite):
    """«Lista los archivos del workspace» debe activar listar_carpeta."""
    defs = composite.definitions()
    gate = composite.build_intent_gate()
    filtered = gate.tools_for_request(defs, "Lista los archivos del workspace")

    assert filtered, "El gate no debe bloquear todas las tools locales"
    names = {d["function"]["name"] for d in filtered}
    assert "listar_carpeta" in names


def test_core_tools_are_offered_when_user_asks_to_create(composite):
    """«crea una carpeta llamada X» debe activar crear_carpeta."""
    defs = composite.definitions()
    gate = composite.build_intent_gate()
    filtered = gate.tools_for_request(defs, "crea una carpeta que se llame dos")

    assert filtered, "El gate no debe bloquear todas las tools locales"
    names = {d["function"]["name"] for d in filtered}
    assert "crear_carpeta" in names


def test_gate_blocks_tools_for_generic_questions(composite):
    """Una pregunta general no debe activar ninguna tool."""
    defs = composite.definitions()
    gate = composite.build_intent_gate()
    filtered = gate.tools_for_request(defs, "Explícame qué es una variable")

    assert not filtered, "Una pregunta general no debe ofrecer herramientas"
