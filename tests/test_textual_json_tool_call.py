"""Tests §5: recuperar tool_call emitido como JSON en prosa.

Cubre el bug de Ollama #15539 (Gemma4 y similares).
"""
from __future__ import annotations

import pytest

from core.ollama import OllamaClient


TOOLS = {"crear_archivo", "leer_archivo", "ejecutar_comando"}


def test_forma_basica_con_arguments():
    # Content limpio: solo el JSON (con whitespace). El
    # endurecimiento rechaza JSON embebido en prosa.
    content = (
        '{"name": "crear_archivo", "arguments": '
        '{"path": "x.py", "content": "print(1)"}}'
    )
    call = OllamaClient._parse_textual_json_tool_call(content, TOOLS)
    assert call is not None
    name, args = call
    assert name == "crear_archivo"
    assert args == {"path": "x.py", "content": "print(1)"}


def test_forma_con_parameters_alias():
    content = '{"name": "leer_archivo", "parameters": {"path": "a.py"}}'
    call = OllamaClient._parse_textual_json_tool_call(content, TOOLS)
    assert call == ("leer_archivo", {"path": "a.py"})


def test_forma_anidada_en_function():
    content = (
        '{"function": {"name": "crear_archivo", '
        '"arguments": {"path": "y.py"}}}'
    )
    call = OllamaClient._parse_textual_json_tool_call(content, TOOLS)
    assert call == ("crear_archivo", {"path": "y.py"})


def test_forma_tool_calls_array():
    content = (
        '{"tool_calls": [{"function": {"name": "ejecutar_comando", '
        '"arguments": {"command": "ls"}}}]}'
    )
    call = OllamaClient._parse_textual_json_tool_call(content, TOOLS)
    assert call == ("ejecutar_comando", {"command": "ls"})


def test_nombre_no_en_tool_names_no_detecta():
    content = '{"name": "eliminar_todo", "arguments": {}}'
    assert OllamaClient._parse_textual_json_tool_call(content, TOOLS) is None


def test_json_de_ejemplo_no_se_confunde():
    # JSON de ejemplo sin name de tool conocido.
    content = 'Ejemplo: {"foo": "bar", "count": 3}'
    assert OllamaClient._parse_textual_json_tool_call(content, TOOLS) is None


def test_texto_sin_json_devuelve_none():
    assert OllamaClient._parse_textual_json_tool_call("hola", TOOLS) is None
    assert OllamaClient._parse_textual_json_tool_call("", TOOLS) is None


def test_json_desbalanceado_se_ignora():
    content = '{"name": "crear_archivo", "arguments": {"path"'
    assert OllamaClient._parse_textual_json_tool_call(content, TOOLS) is None


def test_bloque_sin_name_se_ignora_y_siguiente_gana():
    # Dos bloques concatenados (solo whitespace entre ellos).
    # El primero no tiene name de tool: se ignora. El segundo sí.
    content = (
        '{"foo": 1} '
        '{"name": "leer_archivo", "arguments": {"path": "z.py"}}'
    )
    call = OllamaClient._parse_textual_json_tool_call(content, TOOLS)
    assert call == ("leer_archivo", {"path": "z.py"})
