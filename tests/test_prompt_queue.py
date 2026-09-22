"""Tests de la cola de prompts (Enviar todo)."""
from __future__ import annotations

import pytest

from ui.controllers.app_controller import split_prompts


# -- split_prompts ----------------------------------------------------------

def test_split_with_dashes():
    text = "prompt 1\n\n---\n\nprompt 2\n\n---\n\nprompt 3"
    assert split_prompts(text) == ["prompt 1", "prompt 2", "prompt 3"]


def test_split_with_equals():
    assert split_prompts("a\n===\nb") == ["a", "b"]


def test_split_single_prompt():
    assert split_prompts("solo uno") == ["solo uno"]


def test_split_empty():
    assert split_prompts("") == []
    assert split_prompts("   ") == []
    assert split_prompts("---") == []


def test_split_ignores_inline_dashes():
    """`---` a mitad de línea no es separador."""
    assert split_prompts("antes --- despues") == ["antes --- despues"]


def test_split_requires_three_chars():
    """Dos guiones no son separador."""
    assert split_prompts("a\n--\nb") == ["a\n--\nb"]


def test_split_strips_whitespace():
    text = "  uno  \n---\n  dos  "
    assert split_prompts(text) == ["uno", "dos"]
