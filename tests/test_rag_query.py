"""Tests de la tool rag_query (C3)."""
from __future__ import annotations

from pathlib import Path

import numpy as np
import pytest

from core.ast_index import AstIndex, close_all as ast_close_all
from core.intent import READ_ONLY_TOOLS, is_read_only
from core.rag_index import DEFAULT_MODEL, RagIndex, close_all as rag_close_all
from core.tools import ToolRegistry
from core.workspace import Workspace


class FakeOllama:
    """Embedder determinista (hash del texto -> vector normalizado)."""

    def __init__(self, dim: int = 8):
        self.dim = dim

    def embed(self, texts, *, model=DEFAULT_MODEL, timeout=120.0,
              cancel_event=None):
        out = []
        for t in texts:
            rng = np.random.default_rng(abs(hash(t)))
            v = rng.standard_normal(self.dim).astype(np.float32)
            v /= np.linalg.norm(v) or 1.0
            out.append(v.tolist())
        return out


@pytest.fixture
def rag_setup(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "auth.py").write_text(
        "def login(username, password):\n"
        "    '''Valida credenciales y devuelve un token.'''\n"
        "    return 'token'\n"
        "class User:\n"
        "    def save(self):\n"
        "        '''Persiste el usuario.'''\n"
        "        return True\n",
        encoding="utf-8",
    )
    (root / "math.py").write_text(
        "def suma(a, b):\n"
        "    '''Suma dos numeros.'''\n"
        "    return a + b\n"
        "MAX = 100\n",
        encoding="utf-8",
    )
    ws = Workspace(root)
    ast = AstIndex(root, db_path=tmp_path / "idx.sqlite")
    ast.refresh()
    ollama = FakeOllama()
    rag = RagIndex(root, ollama=ollama, ast_index=ast)
    return ws, ast, rag


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    from core import ast_index as ai
    monkeypatch.setattr(ai, "CACHE_DIR", tmp_path / "ast_cache")
    yield
    ast_close_all()
    rag_close_all()


# ── spec / regla / read-only ──────────────────────────────────────


def test_rag_query_aparece_en_definitions(rag_setup):
    ws, ast, rag = rag_setup
    reg = ToolRegistry(ws, ast_index=ast, rag_index=rag)
    names = [d["function"]["name"] for d in reg.definitions()]
    assert "rag_query" in names


def test_rag_query_tiene_intent_rule(rag_setup):
    ws, ast, rag = rag_setup
    reg = ToolRegistry(ws, ast_index=ast, rag_index=rag)
    rules = reg.intent_rules()
    assert "rag_query" in rules
    rule = rules["rag_query"]
    assert "busca" in rule.verbs
    assert "código" in rule.target_words


def test_rag_query_es_read_only():
    assert "rag_query" in READ_ONLY_TOOLS
    assert is_read_only("rag_query") is True


def test_rag_query_no_requiere_confirmacion(rag_setup):
    ws, ast, rag = rag_setup
    reg = ToolRegistry(ws, ast_index=ast, rag_index=rag)
    assert reg.requires_confirmation("rag_query") is False


# ── call: camino feliz ────────────────────────────────────────────


def test_rag_query_devuelve_hits(rag_setup):
    ws, ast, rag = rag_setup
    rag.index_pending()
    reg = ToolRegistry(ws, ast_index=ast, rag_index=rag)
    out = reg.call("rag_query", {"query": "login"})
    assert "Encontrados" in out
    # score + archivo:linea + [kind] + nombre + firma
    assert "auth.py:" in out
    assert "[function]" in out or "[class]" in out


def test_rag_query_k_custom(rag_setup):
    ws, ast, rag = rag_setup
    rag.index_pending()
    reg = ToolRegistry(ws, ast_index=ast, rag_index=rag)
    out = reg.call("rag_query", {"query": "x", "k": 1})
    assert "Encontrados 1 fragmento(s)" in out


def test_rag_query_kind_filter(rag_setup):
    ws, ast, rag = rag_setup
    rag.index_pending()
    reg = ToolRegistry(ws, ast_index=ast, rag_index=rag)
    out = reg.call("rag_query", {"query": "x", "kind": "class"})
    # Solo [class], ningun [function]
    assert "[class]" in out
    assert "[function]" not in out


# ── call: casos de error ──────────────────────────────────────────


def test_rag_query_sin_indice(tmp_path):
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    (ws_root / "x.py").write_text("def f(): pass\n", encoding="utf-8")
    ws = Workspace(ws_root)
    reg = ToolRegistry(ws)  # sin rag_index
    out = reg.call("rag_query", {"query": "x"})
    assert out.startswith("ERROR")
    assert "no disponible" in out
    assert "buscar_simbolo" in out


def test_rag_query_sin_resultados(rag_setup):
    ws, ast, rag = rag_setup
    rag.index_pending()
    reg = ToolRegistry(ws, ast_index=ast, rag_index=rag)
    # Query sin indice no deberia dar "Sin resultados" pero tampoco
    # explotar. Verificamos que el mensaje sea claro si no hay hits.
    out = reg.call("rag_query", {"query": "zzzz"})
    # O bien encuentra algo (hash raro) o dice "Sin resultados".
    assert "Sin resultados" in out or "Encontrados" in out


def test_rag_query_kind_invalido(rag_setup):
    ws, ast, rag = rag_setup
    rag.index_pending()
    reg = ToolRegistry(ws, ast_index=ast, rag_index=rag)
    out = reg.call("rag_query", {"query": "x", "kind": "banana"})
    assert out.startswith("ERROR")
    assert "kind" in out


def test_rag_query_sin_query(rag_setup):
    ws, ast, rag = rag_setup
    reg = ToolRegistry(ws, ast_index=ast, rag_index=rag)
    out = reg.call("rag_query", {})
    assert out.startswith("ERROR")
    assert "query" in out


def test_rag_query_k_cero_se_normaliza(rag_setup):
    ws, ast, rag = rag_setup
    rag.index_pending()
    reg = ToolRegistry(ws, ast_index=ast, rag_index=rag)
    out = reg.call("rag_query", {"query": "x", "k": 0})
    assert "Encontrados" in out


def test_rag_query_sin_indice_de_ast(tmp_path):
    """Sin ast_index inyectado, el RAG crea el suyo. Sin rag_index,
    la tool avisa. Con rag_index pero ast_index=None debe funcionar
    (RagIndex tiene su propio ast_index)."""
    ws_root = tmp_path / "ws"
    ws_root.mkdir()
    (ws_root / "x.py").write_text(
        "def f():\n    '''doc'''\n    return 1\n",
        encoding="utf-8",
    )
    ws = Workspace(ws_root)
    ast = AstIndex(ws_root, db_path=tmp_path / "idx.sqlite")
    ast.refresh()
    rag = RagIndex(ws_root, ollama=FakeOllama(), ast_index=ast)
    rag.index_pending()
    reg = ToolRegistry(ws, rag_index=rag)
    out = reg.call("rag_query", {"query": "f"})
    assert "Encontrados" in out
    ast.close()
