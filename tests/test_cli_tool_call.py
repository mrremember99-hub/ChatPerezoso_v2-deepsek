"""Tests del parser CLI textual (fallback mistral-small)."""
from __future__ import annotations

from core.ollama import OllamaClient


TOOLS = {"ejecutar_comando", "escribir_archivo", "leer_archivo"}


def test_forma_basica():
    c = 'ejecutar_comando --command "python3 alpha.py"'
    call = OllamaClient._parse_cli_tool_call(c, TOOLS)
    assert call is not None
    name, args = call
    assert name == "ejecutar_comando"
    assert args == {"command": "python3 alpha.py"}


def test_multiples_args():
    c = 'escribir_archivo --nombre x.py --content "hola"'
    call = OllamaClient._parse_cli_tool_call(c, TOOLS)
    assert call == ("escribir_archivo", {
        "nombre": "x.py", "content": "hola"
    })


def test_kebab_a_snake():
    c = 'leer_archivo --path a.py --line-start 2 --line-end 5'
    call = OllamaClient._parse_cli_tool_call(c, TOOLS)
    assert call == ("leer_archivo", {
        "path": "a.py", "line_start": "2", "line_end": "5"
    })


def test_rechaza_si_nombre_desconocido():
    c = 'rm --recursive /'
    assert OllamaClient._parse_cli_tool_call(c, TOOLS) is None


def test_rechaza_si_no_hay_doble_guion():
    assert OllamaClient._parse_cli_tool_call(
        "ejecutar_comando python3 alpha.py", TOOLS
    ) is None


def test_rechaza_si_hay_code_fence():
    c = 'ejecutar_comando --command "x"\n```\n...\n```'
    assert OllamaClient._parse_cli_tool_call(c, TOOLS) is None


def test_rechaza_si_muchas_lineas():
    c = "linea 1\nlinea 2\nlinea 3\nlinea 4\nlinea 5"
    assert OllamaClient._parse_cli_tool_call(c, TOOLS) is None


def test_rechaza_sin_args():
    assert OllamaClient._parse_cli_tool_call(
        "ejecutar_comando", TOOLS
    ) is None


def test_rechaza_vacio():
    assert OllamaClient._parse_cli_tool_call("", TOOLS) is None
    assert OllamaClient._parse_cli_tool_call("   ", TOOLS) is None


def test_rechaza_ultimo_arg_sin_valor():
    c = 'ejecutar_comando --command'
    # Solo --command sin valor: rechaza.
    assert OllamaClient._parse_cli_tool_call(c, TOOLS) is None
