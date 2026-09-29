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


# ── Fixture: aislar ~/.cache/chatperezoso durante los tests ────────


@pytest.fixture(autouse=True)
def _isolate_ast_cache(tmp_path, monkeypatch):
    """Redirige CACHE_DIR de ast_index a tmp_path.

    Sin esto, get_index(root) y AstIndex(root) sin db_path escriben
    en ~/.cache/chatperezoso/ y dejan basura tras cada corrida.
    """
    from core import ast_index as ai
    monkeypatch.setattr(ai, "CACHE_DIR", tmp_path / "ast_cache")


# ── C0: end_line ──────────────────────────────────────────────────


def test_end_line_funcion():
    src = "def f():\n    x = 1\n    return x\n"
    syms = extract_symbols_from_source(src, "x.py")
    assert syms[0].line == 1
    assert syms[0].end_line == 3


def test_end_line_clase_y_metodo():
    src = (
        "class Foo:\n"
        "    def bar(self, a):\n"
        "        return a\n"
    )
    syms = extract_symbols_from_source(src, "x.py")
    by_name = {s.name: s for s in syms}
    assert by_name["Foo"].line == 1
    assert by_name["Foo"].end_line == 3
    assert by_name["Foo.bar"].line == 2
    assert by_name["Foo.bar"].end_line == 3


def test_end_line_constante():
    src = "MAX = 10\n"
    syms = extract_symbols_from_source(src, "x.py")
    assert syms[0].line == 1
    assert syms[0].end_line == 1


def test_end_line_symbol_manual_default_cero():
    # Symbol construido a mano sin end_line: default 0 (compat).
    s = Symbol(name="x", kind="function", file="a.py", line=1)
    assert s.end_line == 0


def test_end_line_persiste_en_db(tmp_path):
    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "x.py").write_text(
        "def f():\n    x = 1\n    return x\n", encoding="utf-8"
    )
    db = tmp_path / "idx.sqlite"
    with AstIndex(ws, db_path=db) as idx:
        idx.refresh()
        rows = list(idx._con.execute(
            "SELECT name, end_line FROM symbols ORDER BY line"
        ))
        assert rows[0]["name"] == "f"
        assert rows[0]["end_line"] == 3


def test_migracion_schema_v1_a_vN(tmp_path):
    """DB creada con schema 1 (sin end_line) -> al abrir con AstIndex
    v2, se migra: columna end_line anadida + datos vaciados."""
    import sqlite3

    ws = tmp_path / "ws"
    ws.mkdir()
    (ws / "x.py").write_text(
        "def f():\n    return 1\n", encoding="utf-8"
    )
    db = tmp_path / "idx.sqlite"

    # Creamos DB manualmente con schema 1 (sin end_line).
    con = sqlite3.connect(str(db))
    con.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE TABLE symbols ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  file TEXT NOT NULL, line INTEGER NOT NULL,"
        "  name TEXT NOT NULL, kind TEXT NOT NULL,"
        "  signature TEXT NOT NULL DEFAULT '',"
        "  docstring TEXT NOT NULL DEFAULT ''"
        ");"
        "CREATE TABLE files ("
        "  path TEXT PRIMARY KEY, mtime REAL NOT NULL,"
        "  indexed_at REAL NOT NULL"
        ");"
        "INSERT INTO meta(key, value) VALUES('schema', '1');"
        "INSERT INTO symbols(file, line, name, kind) "
        "VALUES('x.py', 1, 'viejo', 'function');"
        "INSERT INTO files(path, mtime, indexed_at) "
        "VALUES('x.py', 0.0, 0.0);"
    )
    con.commit()
    con.close()

    # Abrimos con AstIndex v2: debe migrar y vaciar.
    with AstIndex(ws, db_path=db) as idx:
        # El simbolo viejo desaparecio.
        assert idx.search("viejo") == []
        # La columna end_line existe.
        cols = [r[1] for r in idx._con.execute(
            "PRAGMA table_info(symbols)"
        )]
        assert "end_line" in cols
        # El schema quedo al dia.
        row = idx._con.execute(
            "SELECT value FROM meta WHERE key='schema'"
        ).fetchone()
        # La migracion v1 -> vN lleva el schema al valor actual.
        from core.ast_index import _SCHEMA_VERSION
        assert int(row[0]) == _SCHEMA_VERSION
        # Y el refresh reparsea el archivo.
        idx.refresh()
        assert any(s.name == "f" for s in idx.search("f"))


# ── C2a: tabla embeddings ─────────────────────────────────────────


def test_schema_v3_tiene_tabla_embeddings(tmp_path):
    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "x.py").write_text("def f(): pass\n", encoding="utf-8")
    with AstIndex(ws, db_path=tmp_path / "idx.sqlite") as idx:
        cols = [r[1] for r in idx.con.execute(
            "PRAGMA table_info(embeddings)"
        )]
        assert cols == [
            "symbol_id", "model", "dim", "vector", "indexed_at",
        ]
        schema = idx.con.execute(
            "SELECT value FROM meta WHERE key='schema'"
        ).fetchone()[0]
        assert int(schema) == 3


def test_schema_v2_a_v3_no_vacia_symbols(tmp_path):
    """La migracion 2->3 solo anade la tabla embeddings, no toca
    symbols. Un simbolo existente debe sobrevivir."""
    import sqlite3

    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "x.py").write_text(
        "def f():\n    return 1\n", encoding="utf-8"
    )
    db = tmp_path / "idx.sqlite"

    # Simulamos una DB en schema 2 (con end_line, sin embeddings).
    con = sqlite3.connect(str(db))
    con.executescript(
        "CREATE TABLE meta (key TEXT PRIMARY KEY, value TEXT NOT NULL);"
        "CREATE TABLE symbols ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  file TEXT NOT NULL, line INTEGER NOT NULL,"
        "  name TEXT NOT NULL, kind TEXT NOT NULL,"
        "  signature TEXT NOT NULL DEFAULT '',"
        "  docstring TEXT NOT NULL DEFAULT '',"
        "  end_line INTEGER NOT NULL DEFAULT 0"
        ");"
        "CREATE TABLE files ("
        "  path TEXT PRIMARY KEY, mtime REAL NOT NULL,"
        "  indexed_at REAL NOT NULL"
        ");"
        "INSERT INTO meta(key, value) VALUES('schema', '2');"
        "INSERT INTO symbols(file, line, name, kind, end_line) "
        "VALUES('x.py', 1, 'preservado', 'function', 2);"
        "INSERT INTO files(path, mtime, indexed_at) "
        "VALUES('x.py', 0.0, 0.0);"
    )
    con.commit()
    con.close()

    # Abrir con AstIndex v3: migra, no vacia.
    with AstIndex(ws, db_path=db) as idx:
        # El simbolo viejo sigue ahi (no se vacio).
        assert any(s.name == "preservado" for s in idx.search("preservado"))
        # Y existe embeddings.
        cols = [r[1] for r in idx.con.execute(
            "PRAGMA table_info(embeddings)"
        )]
        assert "symbol_id" in cols
        schema = idx.con.execute(
            "SELECT value FROM meta WHERE key='schema'"
        ).fetchone()[0]
        assert int(schema) == 3


def test_delete_file_limpia_embeddings_huerfanos(tmp_path):
    import struct

    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "x.py").write_text(
        "def f():\n    return 1\ndef g():\n    return 2\n",
        encoding="utf-8",
    )
    (ws / "y.py").write_text("def h(): pass\n", encoding="utf-8")

    with AstIndex(ws, db_path=tmp_path / "idx.sqlite") as idx:
        idx.refresh()
        # Insertamos embeddings para todos.
        sids = [r[0] for r in idx.con.execute("SELECT id FROM symbols")]
        blob = struct.pack("3f", 0.1, 0.2, 0.3)
        for sid in sids:
            idx.con.execute(
                "INSERT INTO embeddings"
                "(symbol_id, model, dim, vector, indexed_at) "
                "VALUES (?, 'test', 3, ?, 0.0)",
                (sid, blob),
            )
        idx.con.commit()
        assert idx.con.execute(
            "SELECT COUNT(*) FROM embeddings"
        ).fetchone()[0] == 3

        # Borrar x.py: solo debe quedar el embedding de h.
        idx._delete_file("x.py")
        idx.con.commit()
        assert idx.con.execute(
            "SELECT COUNT(*) FROM embeddings"
        ).fetchone()[0] == 1


def test_delete_file_no_toca_embeddings_de_otros(tmp_path):
    import struct

    ws = tmp_path / "ws"; ws.mkdir()
    (ws / "a.py").write_text("def f(): pass\n", encoding="utf-8")
    (ws / "b.py").write_text("def g(): pass\n", encoding="utf-8")

    with AstIndex(ws, db_path=tmp_path / "idx.sqlite") as idx:
        idx.refresh()
        sids = {r["file"]: r["id"] for r in idx.con.execute(
            "SELECT id, file FROM symbols"
        )}
        blob = struct.pack("1f", 1.0)
        for sid in sids.values():
            idx.con.execute(
                "INSERT INTO embeddings"
                "(symbol_id, model, dim, vector, indexed_at) "
                "VALUES (?, 'test', 1, ?, 0.0)",
                (sid, blob),
            )
        idx.con.commit()

        idx._delete_file("a.py")
        idx.con.commit()
        rows = list(idx.con.execute(
            "SELECT symbol_id FROM embeddings"
        ))
        assert len(rows) == 1
        assert rows[0][0] == sids["b.py"]
