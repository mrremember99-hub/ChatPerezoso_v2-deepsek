"""Indice AST de un workspace, persistido en SQLite.

Objetivo: busqueda rapida de simbolos (clases, funciones, metodos,
constantes) sin re-parsear todo el workspace en cada snapshot.

No reemplaza a ``workspace_snapshot.py``: es una capa encima. El
snapshot sigue siendo la vista breve para el modelo; el indice es
lo que permite responder "donde esta definido X" en O(1).

Diseno:
- DB en ``~/.cache/chatperezoso/ast_index_<hash>.sqlite``, una por
  workspace root. No ensucia el workspace del usuario.
- Schema minimo: tabla ``symbols`` (nombre, tipo, archivo, linea,
  firma, docstring) + tabla ``files`` (mtime por archivo).
- Refresh incremental: compara mtime en disco vs DB; solo re-parsea
  lo que cambio. ``mark_dirty(path)`` fuerza re-parseo del archivo
  en el proximo ``refresh()`` aunque el mtime no cambie (util tras
  un ``write_file`` que preserva el mtime).
- Solo Python (.py). Otros lenguajes, cuando haga falta, en otra
  capa encima.
"""
from __future__ import annotations

import ast
import hashlib
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable, Iterator

from .workspace import _SKIP_DIRS


CACHE_DIR = Path.home() / ".cache" / "chatperezoso"
_MAX_DOCSTRING = 200
_SCHEMA_VERSION = 2

KIND_CLASS = "class"
KIND_FUNCTION = "function"
KIND_METHOD = "method"
KIND_CONSTANT = "constant"


@dataclass(frozen=True)
class Symbol:
    name: str
    kind: str
    file: str          # relativo al root del workspace
    line: int
    signature: str = ""
    docstring: str = ""
    # C0: linea final (inclusive). 0 = desconocido (compat con
    # tests que construyen Symbol a mano sin end_line).
    end_line: int = 0


# ── Paths ────────────────────────────────────────────────────────────


# Cache module-level: un AstIndex por root de workspace, compartido
# por ToolRegistry, snapshot y (en el futuro) el RAG. Evita abrir la
# misma conexion SQLite dos veces y garantiza que el indice que
# consulta buscar_simbolo es el mismo que refresca el snapshot.
_INDEX_CACHE: dict[Path, "AstIndex"] = {}
_INDEX_CACHE_LOCK = threading.Lock()


def get_index(root: Path) -> "AstIndex":
    """Devuelve el AstIndex compartido para ``root`` (uno por proceso).

    Cache module-level thread-safe. La primera llamada abre la
    conexion SQLite; las siguientes devuelven la misma instancia.
    """
    resolved = Path(root).resolve()
    with _INDEX_CACHE_LOCK:
        idx = _INDEX_CACHE.get(resolved)
        if idx is None:
            idx = AstIndex(resolved)
            _INDEX_CACHE[resolved] = idx
        return idx


def close_all() -> None:
    """Cierra todas las conexiones cacheadas y vacia el cache.

    Util en shutdown de la app y en tests que quieran partir de cero.
    Es idempotente: llamarla dos veces no falla.
    """
    with _INDEX_CACHE_LOCK:
        for idx in _INDEX_CACHE.values():
            try:
                idx.close()
            except Exception:
                pass
        _INDEX_CACHE.clear()


def cache_path_for(root: Path) -> Path:
    """Ruta estable a la DB del indice para un workspace root."""
    resolved = Path(root).resolve()
    key = hashlib.sha1(str(resolved).encode("utf-8")).hexdigest()[:12]
    # El nombre del workspace ayuda a identificar el archivo a ojo.
    safe_name = "".join(c if c.isalnum() or c in "-_" else "_"
                        for c in resolved.name) or "root"
    return CACHE_DIR / f"ast_index_{safe_name}_{key}.sqlite"


# ── Extraccion de simbolos ──────────────────────────────────────────


def _format_args(args: ast.arguments) -> str:
    """Solo nombres de argumentos, sin defaults ni anotaciones."""
    parts: list[str] = []
    for a in args.args:
        if a.arg in ("self", "cls"):
            continue
        parts.append(a.arg)
    if args.vararg:
        parts.append(f"*{args.vararg.arg}")
    for a in args.kwonlyargs:
        parts.append(a.arg)
    if args.kwarg:
        parts.append(f"**{args.kwarg.arg}")
    return ", ".join(parts)


def _name_of(node: ast.AST) -> str:
    """Nombre legible de un nodo (Name/Attribute)."""
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return f"{_name_of(node.value)}.{node.attr}"
    return "?"


def _first_docstring(node: ast.AST) -> str:
    """Primera linea no vacia del docstring, truncada."""
    body = getattr(node, "body", None)
    if not body:
        return ""
    first = body[0]
    if not isinstance(first, ast.Expr):
        return ""
    val = first.value
    if not isinstance(val, ast.Constant) or not isinstance(val.value, str):
        return ""
    doc = val.value.strip()
    if not doc:
        return ""
    line = doc.splitlines()[0].strip()
    if len(line) > _MAX_DOCSTRING:
        line = line[: _MAX_DOCSTRING - 1] + "…"
    return line


def _extract_from_body(
    body: list[ast.stmt],
    *,
    rel_path: str,
    prefix: str = "",
    is_class_body: bool = False,
) -> Iterator[Symbol]:
    for node in body:
        if isinstance(node, ast.ClassDef):
            doc = _first_docstring(node)
            name = f"{prefix}{node.name}" if prefix else node.name
            bases = ", ".join(_name_of(b) for b in node.bases)
            sig = (
                f"class {node.name}({bases})" if bases
                else f"class {node.name}"
            )
            yield Symbol(
                name=name,
                kind=KIND_CLASS,
                file=rel_path,
                line=node.lineno,
                signature=sig,
                docstring=doc,
                end_line=node.end_lineno or node.lineno,
            )
            yield from _extract_from_body(
                node.body,
                rel_path=rel_path,
                prefix=f"{name}.",
                is_class_body=True,
            )
        elif isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            if node.name.startswith("_") and not node.name.startswith("__"):
                continue
            args = _format_args(node.args)
            kind = KIND_METHOD if is_class_body else KIND_FUNCTION
            name = f"{prefix}{node.name}" if prefix else node.name
            yield Symbol(
                name=name,
                kind=kind,
                file=rel_path,
                line=node.lineno,
                signature=f"def {node.name}({args})",
                docstring=_first_docstring(node),
                end_line=node.end_lineno or node.lineno,
            )
        elif isinstance(node, ast.Assign) and not prefix:
            # Constantes top-level: NAME = ... con NAME en mayusculas.
            for t in node.targets:
                if isinstance(t, ast.Name) and t.id.isupper():
                    yield Symbol(
                        name=t.id,
                        kind=KIND_CONSTANT,
                        file=rel_path,
                        line=node.lineno,
                        signature=f"{t.id} = ...",
                        end_line=node.end_lineno or node.lineno,
                    )
                    break


def extract_symbols_from_source(
    source: str,
    rel_path: str,
) -> list[Symbol]:
    """Parsea ``source`` y devuelve sus simbolos. Vacio si SyntaxError."""
    try:
        tree = ast.parse(source)
    except SyntaxError:
        return []
    return list(_extract_from_body(tree.body, rel_path=rel_path))


def extract_symbols(path: Path, root: Path) -> list[Symbol]:
    """Lee ``path`` (.py) y devuelve sus simbolos. [] si falla."""
    try:
        source = path.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return []
    try:
        rel = str(path.relative_to(root))
    except ValueError:
        rel = str(path)
    return extract_symbols_from_source(source, rel)


# ── Indice ──────────────────────────────────────────────────────────


class AstIndex:
    """Indice SQLite de simbolos Python de un workspace.

    Uso:

        idx = AstIndex(workspace.root)
        idx.refresh()                       # escanea y actualiza
        for s in idx.search("user"):        # substring, case-insensitive
            print(s.file, s.line, s.kind, s.name)
        idx.mark_dirty("core/foo.py")       # forzar re-parseo
        idx.close()
    """

    def __init__(self, root: Path, db_path: Path | None = None) -> None:
        self.root = Path(root).resolve()
        self.db_path = Path(db_path) if db_path else cache_path_for(self.root)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._con = sqlite3.connect(str(self.db_path))
        self._con.row_factory = sqlite3.Row
        self._ensure_schema()
        self._dirty: set[str] = set()

    # ── Schema ────────────────────────────────────────────────

    def _ensure_schema(self) -> None:
        cur = self._con.cursor()
        cur.executescript(
            """
            CREATE TABLE IF NOT EXISTS meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE IF NOT EXISTS symbols (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                file TEXT NOT NULL,
                line INTEGER NOT NULL,
                name TEXT NOT NULL,
                kind TEXT NOT NULL,
                signature TEXT NOT NULL DEFAULT '',
                docstring TEXT NOT NULL DEFAULT '',
                end_line INTEGER NOT NULL DEFAULT 0
            );
            CREATE INDEX IF NOT EXISTS idx_symbols_name ON symbols(name);
            CREATE INDEX IF NOT EXISTS idx_symbols_file ON symbols(file);
            CREATE UNIQUE INDEX IF NOT EXISTS idx_symbols_unique
                ON symbols(file, line, name);
            CREATE TABLE IF NOT EXISTS files (
                path TEXT PRIMARY KEY,
                mtime REAL NOT NULL,
                indexed_at REAL NOT NULL
            );
            """
        )
        # C0: deteccion de schema viejo. Si el meta.schema no cuadra
        # con _SCHEMA_VERSION, migramos: ALTER TABLE si falta la
        # columna nueva + vaciado completo para forzar reparseo (los
        # simbolos viejos tienen end_line=0 y queremos que se llenen).
        row = cur.execute(
            "SELECT value FROM meta WHERE key='schema'"
        ).fetchone()
        current = int(row["value"]) if row else 0

        if current == 0:
            # DB nueva: la creamos con el schema actual.
            cur.execute(
                "INSERT INTO meta(key, value) VALUES('schema', ?)",
                (str(_SCHEMA_VERSION),),
            )
        elif current < _SCHEMA_VERSION:
            # Migracion. Solo hay una version previa (1 -> 2), asi que
            # no hace falta una maquina general de migraciones. Si en
            # el futuro hay mas, se convierte en una lista de pasos.
            if current == 1:
                try:
                    cur.execute(
                        "ALTER TABLE symbols ADD COLUMN "
                        "end_line INTEGER NOT NULL DEFAULT 0"
                    )
                except sqlite3.OperationalError:
                    # Columna ya presente (reintento): ignorar.
                    pass
            # Vaciado: la proxima refresh() reparsea todo.
            cur.execute("DELETE FROM symbols")
            cur.execute("DELETE FROM files")
            cur.execute(
                "UPDATE meta SET value=? WHERE key='schema'",
                (str(_SCHEMA_VERSION),),
            )
        self._con.commit()

    # ── API publica ───────────────────────────────────────────

    def mark_dirty(self, path: Path | str) -> None:
        """Fuerza el re-parseo de ``path`` en el proximo ``refresh()``.

        ``path`` puede ser absoluto o relativo al root. No falla si
        no existe: ``refresh`` lo ignorara al no encontrarlo en disco.
        """
        p = Path(path)
        if p.is_absolute():
            try:
                p = p.relative_to(self.root)
            except ValueError:
                pass
        self._dirty.add(str(p))

    def iter_py_files(self) -> Iterator[Path]:
        """Itera los .py del workspace, saltando dirs ignorados."""
        for p in self.root.rglob("*.py"):
            if not p.is_file():
                continue
            if any(part in _SKIP_DIRS for part in p.parts):
                continue
            if p.name.startswith("."):
                continue
            yield p

    def refresh(self, paths: Iterable[Path] | None = None) -> int:
        """Reindexa lo que haga falta. Devuelve nº de archivos tocados.

        - ``paths=None``: escanea el workspace entero. Solo re-parsea
          los .py cuyo mtime cambio desde el ultimo refresh, los que
          estan en ``_dirty``, y borra los que ya no existen.
        - ``paths=[...]``: procesa solo esos (absolutos o relativos).
        """
        if paths is None:
            disk_paths = {self._rel(p): p for p in self.iter_py_files()}
        else:
            disk_paths: dict[str, Path] = {}
            for raw in paths:
                p = Path(raw)
                if not p.is_absolute():
                    p = self.root / p
                if p.is_file() and p.suffix.lower() == ".py":
                    disk_paths[self._rel(p)] = p

        # Archivos indexados actualmente.
        cur = self._con.cursor()
        known = {
            row["path"]: row["mtime"]
            for row in cur.execute("SELECT path, mtime FROM files")
        }

        touched = 0

        # Borrar los que ya no estan en disco (solo si refresh global).
        if paths is None:
            gone = set(known) - set(disk_paths)
            for rel in gone:
                self._delete_file(rel)
                touched += 1

        # Re-parsear los que cambiaron o estan dirty.
        for rel, abs_path in disk_paths.items():
            try:
                mtime = abs_path.stat().st_mtime
            except OSError:
                continue
            if rel in self._dirty or known.get(rel) != mtime:
                self._index_file(abs_path, rel, mtime)
                touched += 1

        self._dirty.clear()
        self._con.commit()
        return touched

    def search(
        self,
        query: str,
        *,
        kind: str | None = None,
        limit: int = 30,
    ) -> list[Symbol]:
        """Busca simbolos por substring del nombre (case-insensitive).

        ``kind`` opcional filtra por tipo. Orden: por archivo y linea.
        """
        q = (query or "").strip()
        if not q:
            return []
        like = f"%{q.lower()}%"
        sql = (
            "SELECT name, kind, file, line, signature, docstring "
            "FROM symbols WHERE LOWER(name) LIKE ?"
        )
        params: list = [like]
        if kind:
            sql += " AND kind = ?"
            params.append(kind)
        sql += " ORDER BY file, line LIMIT ?"
        params.append(int(limit))

        cur = self._con.cursor()
        return [
            Symbol(
                name=row["name"],
                kind=row["kind"],
                file=row["file"],
                line=row["line"],
                signature=row["signature"],
                docstring=row["docstring"],
            )
            for row in cur.execute(sql, params)
        ]

    def interface_lines(
        self,
        rel_path: str,
        *,
        max_symbols: int = 30,
    ) -> list[str]:
        """Lineas de interfaz publica de un archivo .py, al estilo del
        snapshot legacy.

        Formato:
        - 2 espacios para clase/funcion/constante top-level
        - 4 espacios para metodos directos de clase top-level
        - No desciende a clases anidadas (compat con legacy)
        - Trunca a max_symbols con "  ..." al final

        Devuelve [] si el archivo no esta indexado o no tiene simbolos
        publicos. En ese caso el llamante cae al preview de texto.
        """
        cur = self._con.cursor()
        rows = list(
            cur.execute(
                "SELECT name, kind, line, signature FROM symbols "
                "WHERE file = ? ORDER BY line",
                (rel_path,),
            )
        )
        out: list[str] = []
        for row in rows:
            if len(out) >= max_symbols:
                out.append("  ...")
                break
            name = row["name"]
            kind = row["kind"]
            sig = row["signature"]
            dots = name.count(".")
            if kind == KIND_METHOD:
                if dots != 1:
                    # Metodo de clase anidada: el legacy no baja ahi.
                    continue
                method = name.split(".", 1)[1]
                if method.startswith("_") and not method.startswith("__"):
                    continue
                out.append(f"    {sig}")
            elif kind == KIND_CLASS:
                if dots != 0:
                    continue
                out.append(f"  {sig}")
            elif kind == KIND_FUNCTION:
                if dots != 0:
                    continue
                out.append(f"  {sig}")
            elif kind == KIND_CONSTANT:
                out.append(f"  {name} = ...")
        return out

    def stats(self) -> dict:
        """Contadores para diagnostico."""
        cur = self._con.cursor()
        n_sym = cur.execute("SELECT COUNT(*) FROM symbols").fetchone()[0]
        n_files = cur.execute("SELECT COUNT(*) FROM files").fetchone()[0]
        return {
            "db_path": str(self.db_path),
            "root": str(self.root),
            "files": int(n_files),
            "symbols": int(n_sym),
            "dirty": len(self._dirty),
        }

    def close(self) -> None:
        try:
            self._con.close()
        except sqlite3.Error:
            pass

    def __enter__(self) -> "AstIndex":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # ── Internos ──────────────────────────────────────────────

    def _rel(self, path: Path) -> str:
        try:
            return str(path.relative_to(self.root))
        except ValueError:
            return str(path)

    def _delete_file(self, rel: str) -> None:
        cur = self._con.cursor()
        cur.execute("DELETE FROM symbols WHERE file = ?", (rel,))
        cur.execute("DELETE FROM files WHERE path = ?", (rel,))

    def _index_file(self, abs_path: Path, rel: str, mtime: float) -> None:
        self._delete_file(rel)
        syms = extract_symbols(abs_path, self.root)
        cur = self._con.cursor()
        if syms:
            cur.executemany(
                "INSERT OR REPLACE INTO symbols"
                "(file, line, name, kind, signature, docstring, end_line) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                [
                    (s.file, s.line, s.name, s.kind, s.signature,
                     s.docstring, s.end_line)
                    for s in syms
                ],
            )
        cur.execute(
            "INSERT OR REPLACE INTO files(path, mtime, indexed_at) "
            "VALUES (?, ?, ?)",
            (rel, mtime, time.time()),
        )
