"""P2#4: _result_to_text debe truncar tambien si content no es lista.

Antes habia un early-return `if not isinstance(content, list): return
prefix + str(content)` que se saltaba _MAX_RESULT_CHARS. Ahora todo
pasa por el limite.
"""
from __future__ import annotations

import inspect
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.mcp import client as mcp_client  # noqa: E402


def _result_to_text(result):
    """Localiza la clase que define _result_to_text (staticmethod)."""
    for _name, obj in vars(mcp_client).items():
        if inspect.isclass(obj) and hasattr(obj, "_result_to_text"):
            return obj._result_to_text(result)
    raise RuntimeError("no se encontro _result_to_text en plugins.mcp.client")


def test_string_content_truncado(monkeypatch):
    monkeypatch.setattr(mcp_client, "_MAX_RESULT_CHARS", 100)

    class R:
        content = "x" * 500
        is_error = False

    out = _result_to_text(R())
    assert len(out) <= 100 + 200
    assert "truncado" in out


def test_string_content_normal_no_trunca(monkeypatch):
    monkeypatch.setattr(mcp_client, "_MAX_RESULT_CHARS", 100)

    class R:
        content = "hola mundo"
        is_error = False

    out = _result_to_text(R())
    assert out == "hola mundo"
    assert "truncado" not in out


def test_dict_content_truncado(monkeypatch):
    monkeypatch.setattr(mcp_client, "_MAX_RESULT_CHARS", 100)

    class R:
        content = {"clave": "v" * 500}
        is_error = False

    out = _result_to_text(R())
    assert len(out) <= 100 + 200
    assert "truncado" in out


def test_error_string_content_truncado(monkeypatch):
    monkeypatch.setattr(mcp_client, "_MAX_RESULT_CHARS", 100)

    class R:
        content = "x" * 500
        is_error = True

    out = _result_to_text(R())
    assert out.startswith("ERROR MCP: ")
    assert "truncado" in out


def test_list_content_sigue_truncando(monkeypatch):
    """Regresion: el camino list no debe romperse."""
    monkeypatch.setattr(mcp_client, "_MAX_RESULT_CHARS", 100)

    class R:
        content = [{"text": "y" * 500}]
        is_error = False

    out = _result_to_text(R())
    assert len(out) <= 100 + 200
    assert "truncado" in out
