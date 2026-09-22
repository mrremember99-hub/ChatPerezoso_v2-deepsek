"""Regresión CC-5: detección centralizada de fallo de tool calling textual.

Antes, la detección era una cadena de substrings hardcodeados en
`chat_controller._on_done`. De los tres strings, solo uno coincidía
con los mensajes reales de `ollama.py`; los otros dos eran código
muerto. Ahora hay un helper compartido en `core.ollama`.
"""
from __future__ import annotations

from core.ollama import is_textual_tool_failure


def test_detects_give_up_message():
    """El mensaje real de 'retry agotado' se detecta."""
    text = (
        "No se pudo completar la operación: el modelo no logró "
        "invocar la herramienta mediante la llamada nativa tras "
        "reintentarlo. Reformula la petición."
    )
    assert is_textual_tool_failure(text)


def test_detects_retry_message():
    """El mensaje de retry también se detecta."""
    text = (
        "Has escrito el JSON de la herramienta «listar_carpeta» "
        "como texto normal. No ejecutes herramientas así."
    )
    assert is_textual_tool_failure(text)


def test_empty_text_is_not_failure():
    assert not is_textual_tool_failure("")
    assert not is_textual_tool_failure(None)


def test_normal_text_is_not_failure():
    assert not is_textual_tool_failure("Hola, ¿qué tal?")
    assert not is_textual_tool_failure("Aquí tienes el resultado.")
    assert not is_textual_tool_failure("El archivo contiene 42 líneas.")


def test_case_insensitive():
    assert is_textual_tool_failure("NO LOGRÓ INVOCAR LA HERRAMIENTA")
    assert is_textual_tool_failure("Has Escrito El Json De La Herramienta")