"""Tests de core/rag_index.py — sin red, embedder fake."""
from __future__ import annotations

import struct
from pathlib import Path

import numpy as np
import pytest

from core.ast_index import AstIndex, close_all as ast_close_all
from core.rag_index import (
    DEFAULT_MODEL,
    RagHit,
    RagIndex,
    _chunk_text,
    close_all,
    get_rag_index,
)


# ── fake embedder ─────────────────────────────────────────────────


class FakeOllama:
    """Devuelve vectores deterministas a partir del hash del texto.

    Texto identico -> vector identico. Texto distinto -> vector
    distinto (casi siempre). Sirve para verificar que query()
    ordena por similitud sin necesitar Ollama real.
    """

    def __init__(self, dim: int = 8):
        self.dim = dim
        self.calls: list[tuple[list[str], str]] = []

    def embed(self, texts, *, model=DEFAULT_MODEL, timeout=30.0,
              cancel_event=None):
        self.calls.append((list(texts), model))
        out = []
        for t in texts:
            h = abs(hash(t))
            rng = np.random.default_rng(h)
            v = rng.standard_normal(self.dim).astype(np.float32)
            n = float(np.linalg.norm(v)) or 1.0
            out.append((v / n).tolist())
        return out


def _ws(tmp_path):
    root = tmp_path / "ws"
    root.mkdir()
    (root / "auth.py").write_text(
        '"""Modulo de autenticacion."""\n'
        "class User:\n"
        "    '''Usuario del sistema.'''\n"
        "    def save(self):\n"
        "        '''Persiste el usuario en la base de datos.'''\n"
        "        return True\n"
        "def login(username, password):\n"
        "    '''Valida credenciales y devuelve un token.'''\n"
        "    return 'token'\n"
        "def logout(token):\n"
        "    '''Invalida el token.'''\n"
        "    return True\n",
        encoding="utf-8",
    )
    (root / "math.py").write_text(
        "def suma(a, b):\n"
        "    '''Suma dos numeros.'''\n"
        "    return a + b\n"
        "MAX = 100\n",
        encoding="utf-8",
    )
    return root


def _make_rag(tmp_path, model=DEFAULT_MODEL):
    root = _ws(tmp_path)
    idx = AstIndex(root, db_path=tmp_path / "idx.sqlite")
    idx.refresh()
    ollama = FakeOllama()
    return RagIndex(root, ollama=ollama, ast_index=idx), idx, ollama


# ── _chunk_text ───────────────────────────────────────────────────


def test_chunk_text_incluye_metadata():
    from core.ast_index import Symbol

    s = Symbol(
        name="User", kind="class", file="auth.py", line=1,
        signature="class User", docstring="Usuario.", end_line=5,
    )
    out = _chunk_text(s, "class User:\n    pass\n")
    # Header humanizado (C6a)
    assert "auth | user class" in out
    # Original
    assert "auth.py:User" in out
    # Firma, docstring, body
    assert "class User" in out
    assert "Usuario." in out
    assert "pass" in out


def test_chunk_text_sin_cuerpo_ni_docstring():
    from core.ast_index import Symbol

    s = Symbol(name="x", kind="function", file="a.py", line=1)
    out = _chunk_text(s, "")
    assert "a.py:x" in out
    assert "x function" in out  # header humanizado (C6a)


def test_chunk_text_trunca_body():
    from core.ast_index import Symbol

    # Usamos un caracter que no aparezca en la metadata para no
    # contar de mas (el nombre del simbolo contiene "x" si lo usamos).
    s = Symbol(name="fn", kind="function", file="a.py", line=1,
               end_line=1)
    from core.rag_index import MAX_BODY_CHARS
    body = "Z" * 5000
    out = _chunk_text(s, body)
    # Exactamente MAX_BODY_CHARS del cuerpo, ni uno mas.
    assert out.count("Z") == MAX_BODY_CHARS


# ── index_pending ─────────────────────────────────────────────────


def test_index_pending_primera_vez(tmp_path):
    rag, idx, ollama = _make_rag(tmp_path)
    try:
        n = rag.index_pending()
        # 5 simbolos: User, User.save, login, logout, suma
        # (MAX es constante -> se salta desde C6a)
        assert n == 5
        st = rag.stats()
        assert st["embedded"] == 5
        assert st["pending"] == 0
        # 1 llamada a embed (batch unico)
        assert len(ollama.calls) == 1
        assert ollama.calls[0][1] == DEFAULT_MODEL
    finally:
        idx.close()


def test_index_pending_idempotente(tmp_path):
    rag, idx, ollama = _make_rag(tmp_path)
    try:
        assert rag.index_pending() == 5
        # segunda vez: 0, no re-embebe
        assert rag.index_pending() == 0
        assert len(ollama.calls) == 1
    finally:
        idx.close()


def test_index_pending_limit(tmp_path):
    rag, idx, _ = _make_rag(tmp_path)
    try:
        assert rag.index_pending(limit=3) == 3
        assert rag.stats()["embedded"] == 3
        assert rag.index_pending() == 2
        assert rag.stats()["embedded"] == 5
    finally:
        idx.close()


def test_index_pending_batch_size(tmp_path):
    rag, idx, ollama = _make_rag(tmp_path)
    try:
        rag.index_pending(batch_size=2)
        # 6 simbolos / 2 por batch = 3 llamadas
        assert len(ollama.calls) == 3
    finally:
        idx.close()


def test_index_pending_error_parcial(tmp_path):
    """Si embed falla a mitad, se conserva lo indexado."""
    from core.ollama import OllamaError

    root = _ws(tmp_path)
    idx = AstIndex(root, db_path=tmp_path / "idx.sqlite")
    idx.refresh()

    call_count = {"n": 0}

    class HalfBrokenOllama:
        def __init__(self):
            self.dim = 8

        def embed(self, texts, *, model=DEFAULT_MODEL, **kw):
            call_count["n"] += 1
            if call_count["n"] > 1:
                raise OllamaError("boom")
            out = []
            for t in texts:
                rng = np.random.default_rng(abs(hash(t)))
                v = rng.standard_normal(self.dim).astype(np.float32)
                v /= np.linalg.norm(v) or 1.0
                out.append(v.tolist())
            return out

    rag = RagIndex(root, ollama=HalfBrokenOllama(), ast_index=idx)
    try:
        n = rag.index_pending(batch_size=2)
        # solo el primer batch (2 simbolos) paso
        assert n == 2
    finally:
        idx.close()


# ── query ─────────────────────────────────────────────────────────


def test_query_devuelve_hits(tmp_path):
    rag, idx, _ = _make_rag(tmp_path)
    try:
        rag.index_pending()
        hits = rag.query("login", k=3)
        assert len(hits) == 3
        assert all(isinstance(h, RagHit) for h in hits)
        # ordenados por score descendente
        scores = [h.score for h in hits]
        assert scores == sorted(scores, reverse=True)
    finally:
        idx.close()


def test_query_texto_vacio(tmp_path):
    rag, idx, _ = _make_rag(tmp_path)
    try:
        rag.index_pending()
        assert rag.query("") == []
        assert rag.query("   ") == []
    finally:
        idx.close()


def test_query_kind_filter(tmp_path):
    rag, idx, _ = _make_rag(tmp_path)
    try:
        rag.index_pending()
        hits = rag.query("x", k=10, kind="class")
        assert all(h.symbol.kind == "class" for h in hits)
    finally:
        idx.close()


def test_query_sin_indice(tmp_path):
    root = _ws(tmp_path)
    idx = AstIndex(root, db_path=tmp_path / "idx.sqlite")
    idx.refresh()
    rag = RagIndex(root, ollama=FakeOllama(), ast_index=idx)
    try:
        # no indexamos -> query no encuentra nada
        assert rag.query("login") == []
    finally:
        idx.close()


def test_query_top_k_mayor_que_total(tmp_path):
    rag, idx, _ = _make_rag(tmp_path)
    try:
        rag.index_pending()
        hits = rag.query("x", k=100)
        assert len(hits) == 5  # todos los elegibles
    finally:
        idx.close()


def test_query_embed_falla_devuelve_vacio(tmp_path):
    from core.ollama import OllamaError

    root = _ws(tmp_path)
    idx = AstIndex(root, db_path=tmp_path / "idx.sqlite")
    idx.refresh()

    class BrokenOllama:
        def embed(self, texts, **kw):
            raise OllamaError("down")

    rag = RagIndex(root, ollama=BrokenOllama(), ast_index=idx)
    try:
        assert rag.query("login") == []
    finally:
        idx.close()


# ── invalidate / cleanup ──────────────────────────────────────────


def test_invalidate_file(tmp_path):
    rag, idx, _ = _make_rag(tmp_path)
    try:
        rag.index_pending()
        n = rag.invalidate_file("math.py")
        # Solo suma (MAX es constante -> no se indexo).
        assert n == 1
        assert rag.stats()["embedded"] == 4
    finally:
        idx.close()


def test_cleanup_orphans(tmp_path):
    rag, idx, _ = _make_rag(tmp_path)
    try:
        rag.index_pending()
        # Inserto un embedding huerfano a mano.
        blob = struct.pack("8f", *([0.0] * 8))
        idx.con.execute(
            "INSERT INTO embeddings"
            "(symbol_id, model, dim, vector, indexed_at) "
            "VALUES (99999, ?, 8, ?, 0.0)",
            (DEFAULT_MODEL, blob),
        )
        idx.con.commit()
        n = rag.cleanup_orphans()
        assert n == 1
        assert rag.stats()["embedded"] == 5
    finally:
        idx.close()


# ── stats / cache ─────────────────────────────────────────────────


def test_stats(tmp_path):
    rag, idx, _ = _make_rag(tmp_path)
    try:
        st = rag.stats()
        assert st["model"] == DEFAULT_MODEL
        # 5 elegibles (MAX constante excluida)
        assert st["symbols"] == 5
        assert st["embedded"] == 0
        assert st["pending"] == 5
        rag.index_pending(limit=2)
        st = rag.stats()
        assert st["embedded"] == 2
        assert st["pending"] == 3
    finally:
        idx.close()


def test_get_rag_index_reutiliza(tmp_path):
    root = _ws(tmp_path)
    close_all()
    a = get_rag_index(root, ollama=FakeOllama())
    b = get_rag_index(root, ollama=FakeOllama())
    assert a is b
    close_all()


def test_get_rag_index_modelo_distinto_recrea(tmp_path):
    root = _ws(tmp_path)
    close_all()
    a = get_rag_index(root, ollama=FakeOllama(), model="model-a")
    b = get_rag_index(root, ollama=FakeOllama(), model="model-b")
    assert a is not b
    assert b.model == "model-b"
    close_all()


def test_close_all_limpia_cache(tmp_path):
    root = _ws(tmp_path)
    close_all()
    a = get_rag_index(root, ollama=FakeOllama())
    close_all()
    b = get_rag_index(root, ollama=FakeOllama())
    assert a is not b
    close_all()


# ── aislamiento ───────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _isolate_ast_cache(tmp_path, monkeypatch):
    from core import ast_index as ai
    monkeypatch.setattr(ai, "CACHE_DIR", tmp_path / "ast_cache")
    yield
    ast_close_all()
    close_all()


# ── C5a: prefijos nomic + skip tests ─────────────────────────────


def test_prefix_para_nomic_documento():
    from core.rag_index import _prefix_for, NOMIC_DOC_PREFIX
    assert _prefix_for("hola", "nomic-embed-text", is_query=False) == \
        NOMIC_DOC_PREFIX + "hola"


def test_prefix_para_nomic_query():
    from core.rag_index import _prefix_for, NOMIC_QUERY_PREFIX
    assert _prefix_for("hola", "nomic-embed-text", is_query=True) == \
        NOMIC_QUERY_PREFIX + "hola"


def test_prefix_para_otro_modelo_no_aplica():
    from core.rag_index import _prefix_for
    assert _prefix_for("hola", "text-embedding-3-small", is_query=False) == "hola"
    assert _prefix_for("hola", "text-embedding-3-small", is_query=True) == "hola"


def test_index_pending_salta_archivos_de_tests(tmp_path):
    root = tmp_path / "ws"; root.mkdir()
    (root / "tests").mkdir()
    (root / "tests" / "test_foo.py").write_text(
        "def test_algo(): pass\n", encoding="utf-8"
    )
    (root / "core.py").write_text(
        "def produccion(): pass\n", encoding="utf-8"
    )
    from core.ast_index import AstIndex
    idx = AstIndex(root, db_path=tmp_path / "idx.sqlite")
    idx.refresh()
    ollama = FakeOllama()
    rag = RagIndex(root, ollama=ollama, ast_index=idx)
    try:
        n = rag.index_pending()
        # Solo produccion (no test_algo).
        assert n == 1
        st = rag.stats()
        # stats tambien filtra tests desde C6a.
        assert st["symbols"] == 1
        assert st["embedded"] == 1
    finally:
        idx.close()


def test_index_pending_chunks_llevan_prefijo_nomic(tmp_path):
    """El embed que se llama desde index_pending debe recibir textos
    con 'search_document: ' al principio."""
    root = tmp_path / "ws"; root.mkdir()
    (root / "x.py").write_text(
        "def f():\n    '''doc'''\n    return 1\n", encoding="utf-8"
    )
    from core.ast_index import AstIndex
    idx = AstIndex(root, db_path=tmp_path / "idx.sqlite")
    idx.refresh()

    captured = []

    class CapturingOllama(FakeOllama):
        def embed(self, texts, **kw):
            captured.append(list(texts))
            return super().embed(texts, **kw)

    rag = RagIndex(root, ollama=CapturingOllama(), ast_index=idx)
    try:
        rag.index_pending()
        assert captured, "embed no se llamo"
        for t in captured[0]:
            assert t.startswith("search_document: "), t[:60]
    finally:
        idx.close()


def test_query_lleva_prefijo_nomic(tmp_path):
    """query() debe embeber 'search_query: <texto>'."""
    root = tmp_path / "ws"; root.mkdir()
    (root / "x.py").write_text(
        "def f():\n    return 1\n", encoding="utf-8"
    )
    from core.ast_index import AstIndex
    idx = AstIndex(root, db_path=tmp_path / "idx.sqlite")
    idx.refresh()

    seen_queries = []

    class CapturingOllama(FakeOllama):
        def embed(self, texts, **kw):
            # Solo capturamos las que empiezan por search_query
            for t in texts:
                if t.startswith("search_query: "):
                    seen_queries.append(t)
            return super().embed(texts, **kw)

    rag = RagIndex(root, ollama=CapturingOllama(), ast_index=idx)
    try:
        rag.index_pending()
        rag.query("como se guarda")
        assert seen_queries, "query no se embebio"
        assert seen_queries[0] == "search_query: como se guarda"
    finally:
        idx.close()


# ── C6a: humanizacion + skip constantes ──────────────────────────


def test_humanize_snake_case():
    from core.rag_index import _humanize
    assert _humanize("read_file") == "read file"
    assert _humanize("MAX_READ_BYTES") == "max read bytes"


def test_humanize_camel_case():
    from core.rag_index import _humanize
    assert _humanize("getUserId") == "get user id"
    assert _humanize("OllamaClient") == "ollama client"


def test_humanize_path():
    from core.rag_index import _humanize_path
    assert _humanize_path("core/workspace.py") == "core workspace"
    assert _humanize_path("auth.py") == "auth"


def test_humanize_symbol_dotted():
    from core.rag_index import _humanize
    assert _humanize("Workspace.read_file") == "workspace read file"


def test_chunk_text_lleva_header_humanizado():
    from core.ast_index import Symbol

    s = Symbol(
        name="Workspace.read_file", kind="method",
        file="core/workspace.py", line=1,
        signature="def read_file(path)",
    )
    out = _chunk_text(s, "return text")
    # Header: "core workspace | workspace read file method"
    assert "core workspace" in out
    assert "workspace read file method" in out
    # Original preservado
    assert "core/workspace.py:Workspace.read_file" in out


def test_index_pending_salta_constantes(tmp_path):
    root = tmp_path / "ws"; root.mkdir()
    (root / "x.py").write_text(
        "MAX = 10\n"
        "def f():\n    return 1\n",
        encoding="utf-8",
    )
    from core.ast_index import AstIndex
    idx = AstIndex(root, db_path=tmp_path / "idx.sqlite")
    idx.refresh()
    ollama = FakeOllama()
    rag = RagIndex(root, ollama=ollama, ast_index=idx)
    try:
        n = rag.index_pending()
        # Solo f(), MAX se salta.
        assert n == 1
        st = rag.stats()
        assert st["symbols"] == 1
    finally:
        idx.close()


# ── X2.1: get_rag_index actualiza ollama al reciclar ─────────────
# Auditoria externa 2026-09-29, P1#5.

def test_get_rag_index_actualiza_ollama_al_reciclar(tmp_path):
    """El cache es por root+model. Si el llamante pasa un OllamaClient
    nuevo (mismo root+model), se actualiza la referencia en lugar de
    quedarse con el viejo (potencialmente cerrado)."""
    root = _ws(tmp_path)
    close_all()

    ollama_a = FakeOllama()
    ollama_b = FakeOllama()

    a = get_rag_index(root, ollama=ollama_a)
    assert a.ollama is ollama_a

    # Mismo root+model, cliente distinto: debe reciclar y actualizar.
    b = get_rag_index(root, ollama=ollama_b)
    assert b is a, "mismo root+model -> misma instancia"
    assert b.ollama is ollama_b, "referencia al cliente debe actualizarse"

    close_all()
