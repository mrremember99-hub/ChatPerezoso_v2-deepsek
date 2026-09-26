"""Tests del truncado selectivo de tool results (Hueco 3)."""
from __future__ import annotations

from core.ollama import (
    _TOOL_RESULT_KEEP_HEAD,
    _TOOL_RESULT_KEEP_TAIL,
    _TOOL_RESULT_MAX_LINES,
    _shrink_tool_result_content,
    _shrink_tool_results,
)


# -- _shrink_tool_result_content ----------------------------------------


def test_content_corto_no_se_toca():
    text = "linea 1\nlinea 2\nlinea 3\n"
    assert _shrink_tool_result_content(text) == text


def test_content_vacio():
    assert _shrink_tool_result_content("") == ""


def test_content_largo_se_trunca():
    lines = [f"linea {i}\n" for i in range(500)]
    text = "".join(lines)
    result = _shrink_tool_result_content(text)
    assert len(result.splitlines()) == (
        _TOOL_RESULT_KEEP_HEAD + _TOOL_RESULT_KEEP_TAIL + 1
    )
    # La primera y ultima linea originales siguen.
    assert result.startswith("linea 0\n")
    assert result.rstrip().endswith("linea 499")
    # El marcador menciona cuantas se omitieron.
    assert "omitidas" in result


def test_content_justo_en_el_limite():
    lines = [f"x{i}\n" for i in range(_TOOL_RESULT_MAX_LINES)]
    text = "".join(lines)
    assert _shrink_tool_result_content(text) == text


def test_content_una_linea_de_mas():
    lines = [f"x{i}\n" for i in range(_TOOL_RESULT_MAX_LINES + 1)]
    text = "".join(lines)
    result = _shrink_tool_result_content(text)
    assert "omitidas" in result


# -- _shrink_tool_results -----------------------------------------------


def test_role_tool_se_trunca():
    lines = "\n".join(f"linea {i}" for i in range(500))
    messages = [
        {"role": "tool", "content": lines, "tool_name": "leer_archivo"},
    ]
    result = _shrink_tool_results(messages)
    assert "omitidas" in result[0]["content"]
    assert result[0]["tool_name"] == "leer_archivo"  # preserva metadata


def test_user_con_prefijo_tool_result_se_trunca():
    lines = "\n".join(f"linea {i}" for i in range(500))
    messages = [
        {
            "role": "user",
            "content": f"[TOOL_RESULT:leer_archivo]\n{lines}",
        },
    ]
    result = _shrink_tool_results(messages)
    assert "omitidas" in result[0]["content"]


def test_user_real_no_se_toca():
    lines = "\n".join(f"linea {i}" for i in range(500))
    messages = [{"role": "user", "content": lines}]
    result = _shrink_tool_results(messages)
    assert result[0]["content"] == lines


def test_assistant_no_se_toca():
    lines = "\n".join(f"linea {i}" for i in range(500))
    messages = [{"role": "assistant", "content": lines}]
    result = _shrink_tool_results(messages)
    assert result[0]["content"] == lines


def test_lista_vacia():
    assert _shrink_tool_results([]) == []


def test_no_muta_original():
    lines = "\n".join(f"linea {i}" for i in range(500))
    original = {"role": "tool", "content": lines}
    messages = [original]
    _shrink_tool_results(messages)
    # El dict original NO debe haberse modificado.
    assert original["content"] == lines


def test_content_no_str_se_preserva():
    messages = [{"role": "tool", "content": None}]
    result = _shrink_tool_results(messages)
    assert result[0]["content"] is None
