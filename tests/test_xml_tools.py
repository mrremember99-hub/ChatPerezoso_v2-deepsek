"""Tests del parser XML de tool calls."""
from __future__ import annotations

from core.xml_tools import (
    build_tools_prompt,
    parse_tool_calls,
    strip_tool_call_blocks,
)


# ── parse_tool_calls ────────────────────────────────────────────────

def test_parse_standard_tool_call():
    text = '<tool_call>{"name": "listar_carpeta", "arguments": {"path": "."}}</tool_call>'
    assert parse_tool_calls(text, known_tools={"listar_carpeta"}) == [
        ("listar_carpeta", {"path": "."})
    ]


def test_parse_anthropic_tool_use():
    text = '<tool_use>{"name": "leer_archivo", "input": {"path": "a.txt"}}</tool_use>'
    assert parse_tool_calls(text, known_tools={"leer_archivo"}) == [
        ("leer_archivo", {"path": "a.txt"})
    ]


def test_parse_markdown_fenced_block():
    text = '```tool_call\n{"name": "listar_carpeta", "arguments": {}}\n```'
    assert parse_tool_calls(text, known_tools={"listar_carpeta"}) == [
        ("listar_carpeta", {})
    ]


def test_parse_multiple_calls_in_one_message():
    text = (
        '<tool_call>{"name": "leer_archivo", "arguments": {"path": "a"}}</tool_call>\n'
        '<tool_call>{"name": "listar_carpeta", "arguments": {}}</tool_call>'
    )
    calls = parse_tool_calls(text, known_tools={"leer_archivo", "listar_carpeta"})
    assert calls == [
        ("leer_archivo", {"path": "a"}),
        ("listar_carpeta", {}),
    ]


def test_parse_with_parameters_field():
    """Algunos modelos usan 'parameters' en lugar de 'arguments'."""
    text = '<tool_call>{"name": "x", "parameters": {"a": 1}}</tool_call>'
    assert parse_tool_calls(text, known_tools={"x"}) == [("x", {"a": 1})]


def test_parse_unknown_tool_is_discarded():
    text = '<tool_call>{"name": "herramienta_inventada", "arguments": {}}</tool_call>'
    assert parse_tool_calls(text, known_tools={"listar_carpeta"}) == []


def test_parse_invalid_json_returns_empty():
    text = '<tool_call>{not valid json}</tool_call>'
    assert parse_tool_calls(text, known_tools={"x"}) == []


def test_parse_no_block_returns_empty():
    assert parse_tool_calls("Hola, ¿qué tal?", known_tools={"x"}) == []


def test_parse_false_positive_json_in_markdown():
    """Un JSON de ejemplo en un bloque de código no es una tool call."""
    text = 'Ejemplo de config:\n```json\n{"name": "archivo.txt"}\n```'
    assert parse_tool_calls(text, known_tools={"archivo"}) == []


def test_parse_without_known_tools_accepts_any_name():
    text = '<tool_call>{"name": "cualquiera", "arguments": {}}</tool_call>'
    assert parse_tool_calls(text, known_tools=None) == [("cualquiera", {})]


def test_parse_arguments_missing_defaults_to_empty_dict():
    text = '<tool_call>{"name": "listar_carpeta"}</tool_call>'
    assert parse_tool_calls(text, known_tools={"listar_carpeta"}) == [
        ("listar_carpeta", {})
    ]


# ── strip_tool_call_blocks ──────────────────────────────────────────

def test_strip_removes_block_and_keeps_prose():
    text = 'Voy a listar.\n<tool_call>{"name": "x", "arguments": {}}</tool_call>'
    assert strip_tool_call_blocks(text) == "Voy a listar."


def test_strip_removes_multiple_blocks():
    text = (
        '<tool_call>{"name": "a", "arguments": {}}</tool_call>\n'
        '<tool_call>{"name": "b", "arguments": {}}</tool_call>'
    )
    assert strip_tool_call_blocks(text) == ""


def test_strip_no_block_returns_same_text():
    text = "Solo texto normal."
    assert strip_tool_call_blocks(text) == text


# ── build_tools_prompt ──────────────────────────────────────────────

def test_build_prompt_empty_tools_returns_empty():
    assert build_tools_prompt([]) == ""


def test_build_prompt_contains_tool_name_and_signature():
    tools = [{
        "function": {
            "name": "listar_carpeta",
            "description": "Lista el contenido.",
            "parameters": {
                "type": "object",
                "properties": {"path": {"type": "string"}},
                "required": [],
            },
        },
    }]
    prompt = build_tools_prompt(tools)
    assert "listar_carpeta" in prompt
    assert "path" in prompt
    assert "tool_call" in prompt


def test_build_prompt_marks_optional_params_with_question_mark():
    tools = [{
        "function": {
            "name": "x",
            "description": "",
            "parameters": {
                "type": "object",
                "properties": {
                    "req": {"type": "string"},
                    "opt": {"type": "string"},
                },
                "required": ["req"],
            },
        },
    }]
    prompt = build_tools_prompt(tools)
    assert "req: string" in prompt
    assert "opt?: string" in prompt


def test_build_prompt_truncates_long_descriptions():
    tools = [{
        "function": {
            "name": "x",
            "description": "a" * 300,
            "parameters": {"type": "object", "properties": {}, "required": []},
        },
    }]
    prompt = build_tools_prompt(tools)
    assert "..." in prompt

# -- parche AC: dialecto XML <function=> integrado en tool_calls ------------

def test_function_xml_stripped_from_content():
    """strip_tool_call_blocks debe eliminar el bloque <function=> completo."""
    from core.xml_tools import strip_tool_call_blocks
    text = (
        "Voy a listar la carpeta.\n"
        "<function=mcp__fs__list_directory>\n"
        "<parameter=path>\n.\n</parameter>\n"
        "</function>\n</tool_call>"
    )
    cleaned = strip_tool_call_blocks(text)
    assert "<function=" not in cleaned
    assert "<parameter=" not in cleaned
    assert "Voy a listar" in cleaned

# ── X-1: anidamiento en arguments (verificado correcto) ─────────────

def test_parse_nested_object_arguments():
    r"""`arguments` con objeto anidado: el regex no-greedy lo captura bien.

    El auditor 7 reportó que `\{.*?\}` cortaría en el primer `}`
    interno. Verificado empíricamente: no ocurre. El ancla
    `</tool_call>` que sigue al `\}` fuerza la expansión correcta.
    """
    text = (
        '<tool_call>{"name": "x", "arguments": '
        '{"path": "a", "opts": {"force": true}}}</tool_call>'
    )
    assert parse_tool_calls(text, known_tools={"x"}) == [
        ("x", {"path": "a", "opts": {"force": True}})
    ]


def test_parse_array_of_objects_arguments():
    """`arguments` con array de objetos: también se captura completo."""
    text = (
        '<tool_call>{"name": "y", "arguments": '
        '{"items": [{"a": 1}, {"b": 2}]}}</tool_call>'
    )
    assert parse_tool_calls(text, known_tools={"y"}) == [
        ("y", {"items": [{"a": 1}, {"b": 2}]})
    ]


def test_parse_deeply_nested_arguments():
    """Anidamiento a 3 niveles: sigue funcionando."""
    text = (
        '<tool_call>{"name": "z", "arguments": '
        '{"a": {"b": {"c": {"d": 1}}}}}</tool_call>'
    )
    assert parse_tool_calls(text, known_tools={"z"}) == [
        ("z", {"a": {"b": {"c": {"d": 1}}}})
    ]
