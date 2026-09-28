"""Tests de la tool buscar_simbolo (B3)."""
from __future__ import annotations

from pathlib import Path

import pytest

from core.ast_index import AstIndex
from core.intent import READ_ONLY_TOOLS, is_read_only
from core.tools import ToolRegistry
from core.workspace import Workspace


def _ws_y_indice(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "core").mkdir()
    (root / "core" / "models.py").write_text(
        "class User:\n"
        "    '''Modelo de usuario.'''\n"
        "    def save(self): ...\n"
        "class Admin:\n"
        "    pass\n"
        "def get_user(uid):\n"
        "    '''Devuelve un User.'''\n"
        "    return uid\n"
        "MAX_USERS = 10\n",
        encoding="utf-8",
    )
    (root / "util.py").write_text(
        "def helper():\n    pass\n",
        encoding="utf-8",
    )
    ws = Workspace(root)
    idx = AstIndex(root, db_path=tmp_path / "idx.sqlite")
    return ws, idx


# ── definitions / intent_rules / read-only ─────────────────────────


def test_buscar_simbolo_aparece_en_definitions(tmp_path):
    ws, idx = _ws_y_indice(tmp_path)
    reg = ToolRegistry(ws, ast_index=idx)
    names = [d["function"]["name"] for d in reg.definitions()]
    assert "buscar_simbolo" in names


def test_buscar_simbolo_tiene_intent_rule(tmp_path):
    ws, idx = _ws_y_indice(tmp_path)
    reg = ToolRegistry(ws, ast_index=idx)
    rules = reg.intent_rules()
    assert "buscar_simbolo" in rules
    rule = rules["buscar_simbolo"]
    assert "busca" in rule.verbs
    assert "función" in rule.target_words


def test_buscar_simbolo_es_read_only():
    assert "buscar_simbolo" in READ_ONLY_TOOLS
    assert is_read_only("buscar_simbolo") is True


def test_buscar_simbolo_no_requiere_confirmacion(tmp_path):
    ws, idx = _ws_y_indice(tmp_path)
    reg = ToolRegistry(ws, ast_index=idx)
    assert reg.requires_confirmation("buscar_simbolo") is False


# ── call(): camino feliz ───────────────────────────────────────────


def test_buscar_query_generica(tmp_path):
    ws, idx = _ws_y_indice(tmp_path)
    reg = ToolRegistry(ws, ast_index=idx)
    out = reg.call("buscar_simbolo", {"query": "user"})
    assert "class User" in out
    assert "get_user" in out
    assert "MAX_USERS" in out
    assert "helper" not in out
    # Formato: file:line [kind] signature
    assert "core/models.py:" in out
    assert "[class]" in out
    assert "[function]" in out


def test_buscar_por_kind_class(tmp_path):
    ws, idx = _ws_y_indice(tmp_path)
    reg = ToolRegistry(ws, ast_index=idx)
    out = reg.call("buscar_simbolo", {"query": "user", "kind": "class"})
    assert "class User" in out
    assert "get_user" not in out


def test_buscar_por_kind_function(tmp_path):
    ws, idx = _ws_y_indice(tmp_path)
    reg = ToolRegistry(ws, ast_index=idx)
    out = reg.call("buscar_simbolo", {"query": "user", "kind": "function"})
    assert "get_user" in out
    assert "class User" not in out


def test_buscar_por_kind_method(tmp_path):
    ws, idx = _ws_y_indice(tmp_path)
    reg = ToolRegistry(ws, ast_index=idx)
    out = reg.call("buscar_simbolo", {"query": "save", "kind": "method"})
    assert "User.save" in out
    assert "[method]" in out


def test_buscar_por_kind_constant(tmp_path):
    ws, idx = _ws_y_indice(tmp_path)
    reg = ToolRegistry(ws, ast_index=idx)
    out = reg.call("buscar_simbolo", {"query": "MAX", "kind": "constant"})
    assert "MAX_USERS" in out
    assert "[constant]" in out


def test_buscar_limit(tmp_path):
    ws, idx = _ws_y_indice(tmp_path)
    reg = ToolRegistry(ws, ast_index=idx)
    out = reg.call("buscar_simbolo", {"query": "user", "limit": 1})
    # Solo la primera coincidencia (por archivo+linea).
    assert out.count("core/models.py:") == 1


def test_buscar_muestra_docstring(tmp_path):
    ws, idx = _ws_y_indice(tmp_path)
    reg = ToolRegistry(ws, ast_index=idx)
    out = reg.call("buscar_simbolo", {"query": "User"})
    assert "Modelo de usuario." in out


# ── call(): casos de error ─────────────────────────────────────────


def test_buscar_sin_resultados(tmp_path):
    ws, idx = _ws_y_indice(tmp_path)
    reg = ToolRegistry(ws, ast_index=idx)
    out = reg.call("buscar_simbolo", {"query": "zzzz_no_existe"})
    assert "Sin resultados" in out


def test_buscar_kind_invalido(tmp_path):
    ws, idx = _ws_y_indice(tmp_path)
    reg = ToolRegistry(ws, ast_index=idx)
    out = reg.call("buscar_simbolo", {"query": "x", "kind": "banana"})
    assert out.startswith("ERROR")
    assert "kind" in out


def test_buscar_sin_query(tmp_path):
    ws, idx = _ws_y_indice(tmp_path)
    reg = ToolRegistry(ws, ast_index=idx)
    out = reg.call("buscar_simbolo", {})
    assert out.startswith("ERROR")
    assert "query" in out


def test_buscar_limit_cero_se_normaliza(tmp_path):
    ws, idx = _ws_y_indice(tmp_path)
    reg = ToolRegistry(ws, ast_index=idx)
    # limit=0 no debe reventar: se normaliza a default 30.
    out = reg.call("buscar_simbolo", {"query": "user", "limit": 0})
    assert "class User" in out


# ── cache del indice ───────────────────────────────────────────────


def test_indice_se_cachea_entre_llamadas(tmp_path):
    ws, idx = _ws_y_indice(tmp_path)
    reg = ToolRegistry(ws, ast_index=idx)
    reg.call("buscar_simbolo", {"query": "user"})
    primero = reg._ast_index
    reg.call("buscar_simbolo", {"query": "helper"})
    assert reg._ast_index is primero


def test_indice_lazy_sin_inyeccion(tmp_path):
    ws, _ = _ws_y_indice(tmp_path)
    reg = ToolRegistry(ws)
    assert reg._ast_index is None
    reg.call("buscar_simbolo", {"query": "user"})
    assert reg._ast_index is not None
    # Es un AstIndex real.
    from core.ast_index import AstIndex as RealAstIndex
    assert isinstance(reg._ast_index, RealAstIndex)


def test_buscar_detecta_cambio_en_disco(tmp_path):
    ws, idx = _ws_y_indice(tmp_path)
    reg = ToolRegistry(ws, ast_index=idx)
    # Primera pasada.
    out = reg.call("buscar_simbolo", {"query": "nuevo"})
    assert "Sin resultados" in out
    # Añadimos una funcion nueva y forzamos mtime futuro para que
    # refresh() la detecte aunque el sistema de archivos no cambie
    # la resolucion de mtime.
    f = ws.root / "util.py"
    f.write_text(
        "def helper():\n    pass\n"
        "def nuevo():\n    pass\n",
        encoding="utf-8",
    )
    import os, time
    os.utime(f, (time.time() + 5, time.time() + 5))
    out = reg.call("buscar_simbolo", {"query": "nuevo"})
    assert "nuevo" in out
    assert "util.py" in out
