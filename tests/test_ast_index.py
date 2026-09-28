"""Tests de core/ast_index.py — sin red, sin Qt, sin Ollama."""
from __future__ import annotations

import sqlite3
from pathlib import Path

import pytest

from core.ast_index import (
    KIND_CLASS,
    KIND_CONSTANT,
    KIND_FUNCTION,
    KIND_METHOD,
    AstIndex,
    Symbol,
    cache_path_for,
    extract_symbols,
    extract_symbols_from_source,
)


# ── cache_path_for ─────────────────────────────────────────────────


def test_cache_path_estable(tmp_path):
    a = cache_path_for(tmp_path)
    b = cache_path_for(tmp_path)
    assert a == b
    assert a.suffix == ".sqlite"
    assert "ast_index_" in a.name


def test_cache_path_distinto_por_root(tmp_path):
    d1 = tmp_path / "uno"
    d2 = tmp_path / "dos"
    d1.mkdir()
    d2.mkdir()
    assert cache_path_for(d1) != cache_path_for(d2)


# ── extract_symbols_from_source ────────────────────────────────────


def test_extrae_funcion_simple():
    src = "def foo(a, b):\n    '''Hace algo.'''\n    return a + b\n"
    syms = extract_symbols_from_source(src, "x.py")
    assert len(syms) == 1
    s = syms[0]
    assert s.name == "foo"
    assert s.kind == KIND_FUNCTION
    assert s.file == "x.py"
    assert s.line == 1
    assert s.signature == "def foo(a, b)"
    assert s.docstring == "Hace algo."


def test_extrae_clase_con_metodos():
    src = (
        "class User:\n"
        "    '''Modelo de usuario.'''\n"
        "    def __init__(self, name):\n"
        "        self.name = name\n"
        "    def greet(self):\n"
        "        return 'hi'\n"
        "    def _privado(self):\n"
        "        return 'no'\n"
    )
    syms = extract_symbols_from_source(src, "u.py")
    names = [(s.name, s.kind) for s in syms]
    assert ("User", KIND_CLASS) in names
    assert ("User.__init__", KIND_METHOD) in names
    assert ("User.greet", KIND_METHOD) in names
    # Los _privados (no __dunder__) no aparecen.
    assert not any(s.name.endswith("_privado") for s in syms)


def test_extrae_constantes_top_level():
    src = "MAX = 10\nMIN = 1\nlower = 2\n"
    syms = extract_symbols_from_source(src, "c.py")
    names = {s.name for s in syms}
    assert names == {"MAX", "MIN"}


def test_async_function_es_function():
    src = "async def fetch(url):\n    return url\n"
    syms = extract_symbols_from_source(src, "a.py")
    assert len(syms) == 1
    assert syms[0].kind == KIND_FUNCTION
    assert syms[0].name == "fetch"


def test_syntax_error_devuelve_vacio():
    src = "def foo(:\n"
    assert extract_symbols_from_source(src, "x.py") == []


def test_docstring_multilinea_solo_primera():
    src = (
        "def f():\n"
        "    '''Linea uno.\n"
        "    Linea dos.\n"
        "    '''\n"
    )
    syms = extract_symbols_from_source(src, "x.py")
    assert syms[0].docstring == "Linea uno."


def test_varargs_y_kwargs():
    src = "def f(a, *args, b=1, **kwargs):\n    pass\n"
    syms = extract_symbols_from_source(src, "x.py")
    assert "a" in syms[0].signature
    assert "*args" in syms[0].signature
    assert "b" in syms[0].signature
    assert "**kwargs" in syms[0].signature


# ── extract_symbols (archivo) ──────────────────────────────────────


def test_extract_symbols_de_archivo(tmp_path):
    f = tmp_path / "mod.py"
    f.write_text("def hola():\n    return 1\n", encoding="utf-8")
    syms = extract_symbols(f, tmp_path)
    assert len(syms) == 1
    assert syms[0].file == "mod.py"


def test_extract_symbols_archivo_inexistente(tmp_path):
    assert extract_symbols(tmp_path / "no.py", tmp_path) == []


# ── AstIndex: refresh + search ─────────────────────────────────────


def _mk_ws(tmp_path: Path) -> Path:
    (tmp_path / "core").mkdir()
    (tmp_path / "core" / "models.py").write_text(
        "class User:\n"
        "    def save(self): pass\n"
        "class Admin:\n"
        "    pass\n"
        "def get_user(uid):\n"
        "    return uid\n",
        encoding="utf-8",
    )
    (tmp_path / "util.py").write_text(
        "MAX_USERS = 10\n"
        "def helper():\n"
        "    pass\n",
        encoding="utf-8",
    )
    return tmp_path


def test_index_refresh_inicial(tmp_path):
    ws = _mk_ws(tmp_path)
    db = tmp_path / "idx.sqlite"
    with AstIndex(ws, db_path=db) as idx:
        n = idx.refresh()
        assert n == 2
        st = idx.stats()
        assert st["files"] == 2
        assert st["symbols"] >= 5


def test_index_search_substring(tmp_path):
    ws = _mk_ws(tmp_path)
    db = tmp_path / "idx.sqlite"
    with AstIndex(ws, db_path=db) as idx:
        idx.refresh()
        res = idx.search("user")
        names = {s.name for s in res}
        assert "User" in names
        assert "get_user" in names
        assert "MAX_USERS" in names
        assert "helper" not in names


def test_index_search_kind_filter(tmp_path):
    ws = _mk_ws(tmp_path)
    db = tmp_path / "idx.sqlite"
    with AstIndex(ws, db_path=db) as idx:
        idx.refresh()
        solo_class = idx.search("user", kind=KIND_CLASS)
        assert {s.name for s in solo_class} == {"User"}
        solo_func = idx.search("user", kind=KIND_FUNCTION)
        assert {s.name for s in solo_func} == {"get_user"}


def test_index_search_query_vacia(tmp_path):
    ws = _mk_ws(tmp_path)
    db = tmp_path / "idx.sqlite"
    with AstIndex(ws, db_path=db) as idx:
        idx.refresh()
        assert idx.search("") == []
        assert idx.search("   ") == []


def test_index_incremental_al_cambiar_archivo(tmp_path):
    ws = _mk_ws(tmp_path)
    db = tmp_path / "idx.sqlite"
    with AstIndex(ws, db_path=db) as idx:
        idx.refresh()
        # añade una función nueva
        f = ws / "util.py"
        f.write_text(
            "MAX_USERS = 10\n"
            "def helper():\n    pass\n"
            "def nuevo():\n    pass\n",
            encoding="utf-8",
        )
        import os, time
        os.utime(f, (time.time() + 5, time.time() + 5))  # mtime futuro
        n = idx.refresh()
        assert n == 1  # solo util.py
        assert any(s.name == "nuevo" for s in idx.search("nuevo"))


def test_index_borra_archivos_desaparecidos(tmp_path):
    ws = _mk_ws(tmp_path)
    db = tmp_path / "idx.sqlite"
    with AstIndex(ws, db_path=db) as idx:
        idx.refresh()
        assert idx.stats()["files"] == 2
        (ws / "util.py").unlink()
        n = idx.refresh()
        assert n == 1
        assert idx.stats()["files"] == 1
        assert idx.search("MAX_USERS") == []


def test_mark_dirty_fuerza_reparseo(tmp_path):
    ws = _mk_ws(tmp_path)
    db = tmp_path / "idx.sqlite"
    with AstIndex(ws, db_path=db) as idx:
        idx.refresh()
        # Sin cambios en disco, refresh no hace nada.
        assert idx.refresh() == 0
        idx.mark_dirty("util.py")
        assert idx.refresh() == 1


def test_refresh_acepta_paths_explicitos(tmp_path):
    ws = _mk_ws(tmp_path)
    db = tmp_path / "idx.sqlite"
    with AstIndex(ws, db_path=db) as idx:
        n = idx.refresh(paths=[ws / "util.py"])
        assert n == 1
        assert idx.stats()["files"] == 1


def test_index_persiste_entre_instancias(tmp_path):
    ws = _mk_ws(tmp_path)
    db = tmp_path / "idx.sqlite"
    with AstIndex(ws, db_path=db) as idx:
        idx.refresh()
    # Nueva instancia: no re-parsea (mtime no cambia).
    with AstIndex(ws, db_path=db) as idx:
        assert idx.refresh() == 0
        assert any(s.name == "User" for s in idx.search("user"))


def test_index_ignora_archivos_directorios_skip(tmp_path):
    ws = tmp_path
    (ws / ".venv").mkdir()
    (ws / ".venv" / "lib.py").write_text(
        "def oculto(): pass\n", encoding="utf-8"
    )
    (ws / "visible.py").write_text(
        "def visible(): pass\n", encoding="utf-8"
    )
    db = tmp_path / "idx.sqlite"
    with AstIndex(ws, db_path=db) as idx:
        idx.refresh()
        assert idx.search("oculto") == []
        assert len(idx.search("visible")) == 1


# ── bases en signature ─────────────────────────────────────────────


def test_class_signature_incluye_bases():
    src = "class Foo(Bar, Baz):\n    pass\n"
    syms = extract_symbols_from_source(src, "x.py")
    assert syms[0].signature == "class Foo(Bar, Baz)"


def test_class_signature_sin_bases():
    src = "class Foo:\n    pass\n"
    syms = extract_symbols_from_source(src, "x.py")
    assert syms[0].signature == "class Foo"


def test_class_signature_base_cualificada():
    src = "class Foo(pkg.Bar):\n    pass\n"
    syms = extract_symbols_from_source(src, "x.py")
    assert syms[0].signature == "class Foo(pkg.Bar)"


# ── interface_lines ────────────────────────────────────────────────


def test_interface_lines_formato_basico(tmp_path):
    ws = _mk_ws(tmp_path)
    db = tmp_path / "idx.sqlite"
    with AstIndex(ws, db_path=db) as idx:
        idx.refresh()
        lines = idx.interface_lines("core/models.py")
    text = "\n".join(lines)
    assert "  class User" in text
    # _format_args elimina self/cls de la firma (igual que el legacy).
    assert "    def save()" in text
    assert "  class Admin" in text
    assert "  def get_user(uid)" in text


def test_interface_lines_excluye_privados(tmp_path):
    ws = tmp_path
    (ws / "x.py").write_text(
        "class A:\n"
        "    def _hidden(self): ...\n"
        "    def public(self): ...\n",
        encoding="utf-8",
    )
    db = tmp_path / "idx.sqlite"
    with AstIndex(ws, db_path=db) as idx:
        idx.refresh()
        text = "\n".join(idx.interface_lines("x.py"))
        assert "def public" in text
        assert "_hidden" not in text


def test_interface_lines_archivo_no_indexado(tmp_path):
    ws = _mk_ws(tmp_path)
    db = tmp_path / "idx.sqlite"
    with AstIndex(ws, db_path=db) as idx:
        idx.refresh()
        assert idx.interface_lines("no_existe.py") == []


def test_interface_lines_trunca(tmp_path):
    ws = tmp_path
    src = "".join(f"def f{i}(): pass\n" for i in range(50))
    (ws / "many.py").write_text(src, encoding="utf-8")
    db = tmp_path / "idx.sqlite"
    with AstIndex(ws, db_path=db) as idx:
        idx.refresh()
        lines = idx.interface_lines("many.py")
        assert lines[-1] == "  ..."
        assert len(lines) == 31  # 30 + marcador


def test_interface_lines_vacio_si_no_hay_simbolos(tmp_path):
    ws = tmp_path
    (ws / "comment.py").write_text("# solo comentario\n", encoding="utf-8")
    db = tmp_path / "idx.sqlite"
    with AstIndex(ws, db_path=db) as idx:
        idx.refresh()
        assert idx.interface_lines("comment.py") == []


# ── cache global get_index / close_all (B2c) ───────────────────────


def test_get_index_devuelve_la_misma_instancia(tmp_path):
    from core.ast_index import get_index, close_all

    close_all()
    (tmp_path / "x.py").write_text("def f(): pass\n", encoding="utf-8")
    a = get_index(tmp_path)
    b = get_index(tmp_path)
    assert a is b
    close_all()


def test_get_index_distintos_roots_distintas_instancias(tmp_path):
    from core.ast_index import get_index, close_all

    close_all()
    d1 = tmp_path / "uno"; d1.mkdir()
    d2 = tmp_path / "dos"; d2.mkdir()
    a = get_index(d1)
    b = get_index(d2)
    assert a is not b
    close_all()


def test_get_index_resuelve_symlinks_y_paths_relativos(tmp_path, monkeypatch):
    from core.ast_index import get_index, close_all

    close_all()
    d = tmp_path / "ws"; d.mkdir()
    a = get_index(d)
    # Mismo path pero con "." o ".." resueltos.
    b = get_index(d / "." / "sub" / "..")
    assert a is b
    close_all()


def test_close_all_vacia_el_cache(tmp_path):
    from core.ast_index import get_index, close_all

    close_all()
    d = tmp_path / "ws"; d.mkdir()
    a = get_index(d)
    close_all()
    b = get_index(d)
    assert a is not b
    close_all()


def test_close_all_idempotente(tmp_path):
    from core.ast_index import close_all
    close_all()
    close_all()  # no debe fallar
