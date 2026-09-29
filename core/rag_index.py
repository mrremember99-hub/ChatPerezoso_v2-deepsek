"""Indice RAG sobre el AST index.

Estrategia: chunks = simbolos del AST (clase, funcion, metodo,
constante). Cada chunk embebe metadata + firma + docstring + primeras
lineas del cuerpo. Los vectores viven en la tabla ``embeddings`` de
la misma DB que ``ast_index`` — asi el borrado en cascada ya limpia
huerfanos sin coordinacion extra.

Busqueda: cosine brute-force con numpy. Para un workspace con unos
miles de simbolos (2453 en este proyecto, ~7.5 MB de vectores), el
calculo completo tarda < 10 ms. No hace falta ANN.
"""
from __future__ import annotations

import logging
import re
import sqlite3
import threading
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import numpy as np

from .ast_index import AstIndex, Symbol, get_index
from .ollama import OllamaClient, OllamaError


logger = logging.getLogger(__name__)

DEFAULT_MODEL = "nomic-embed-text"
DEFAULT_BATCH = 32
MAX_BODY_CHARS = 1200

# C5a: nomic-embed-text esta entrenado con prefijos especificos
# ("search_document:" para documentos, "search_query:" para queries).
# Sin ellos pierde ~15-20 puntos de retrieval. Solo aplicamos para
# modelos que empiezan por "nomic".
NOMIC_DOC_PREFIX = "search_document: "
NOMIC_QUERY_PREFIX = "search_query: "

# C5a: no indexar tests por defecto. Sus nombres ("test_foo_...")
# son descriptivos y dominan las busquedas por significado,
# desplazando al codigo de produccion.
_SKIP_INDEX_PREFIXES = ("tests/", "test_")

_RAG_CACHE: dict[Path, "RagIndex"] = {}
_RAG_CACHE_LOCK = threading.Lock()


@dataclass(frozen=True)
class RagHit:
    symbol: Symbol
    score: float


def _prefix_for(text: str, model: str, *, is_query: bool) -> str:
    """Anade el prefijo correcto segun el modelo de embeddings.

    nomic-embed-text usa "search_document:" / "search_query:".
    Otros modelos no llevan prefijo (o el prefijo seria ruido).
    """
    if not model.startswith("nomic"):
        return text
    prefix = NOMIC_QUERY_PREFIX if is_query else NOMIC_DOC_PREFIX
    return prefix + text


def _humanize(text: str) -> str:
    """Separa CamelCase, snake_case y UPPER en palabras naturales.

    "Workspace.read_file" -> "workspace read file"
    "MAX_READ_BYTES" -> "max read bytes"
    "getUserId" -> "get user id"
    """
    # CamelCase -> Camel Case (antes de tocar snake_case)
    s = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", text)
    # snake_case / UPPER_CASE
    s = s.replace("_", " ")
    # separadores de path
    s = s.replace("/", " ").replace(".", " ").replace("-", " ")
    return " ".join(s.lower().split())


def _humanize_path(path: str) -> str:
    """'core/workspace.py' -> 'core workspace'."""
    if "." in path.rsplit("/", 1)[-1]:
        path = path.rsplit(".", 1)[0]
    return _humanize(path)


def _chunk_text(symbol: Symbol, body: str) -> str:
    """Construye el texto que se embebe para un simbolo.

    C6a (2026-09-29): el nombre y la ruta se humanizan a palabras
    naturales ("read_file" -> "read file"). Sin esto, nomic no
    conecta "leer archivo" con read_file: el identificador crudo
    no es semantico para el modelo.

    Formato:
        <ruta humanizada> | <nombre humanizado> <kind>
        <ruta original>:<nombre original>
        <firma>
        <docstring>
        <body truncado>
    """
    header = (
        f"{_humanize_path(symbol.file)} | "
        f"{_humanize(symbol.name)} {symbol.kind}"
    )
    parts = [header, f"{symbol.file}:{symbol.name}"]
    if symbol.signature:
        parts.append(symbol.signature)
    if symbol.docstring:
        parts.append(symbol.docstring)
    if body:
        parts.append("")
        parts.append(body[:MAX_BODY_CHARS])
    return "\n".join(parts)


class RagIndex:
    """Indice de embeddings sobre los simbolos de un workspace.

    Uso tipico:

        rag = RagIndex(root, ollama=client)
        rag.index_pending()                    # embebe lo que falte
        for hit in rag.query("como se guarda un usuario", k=5):
            print(hit.score, hit.symbol.file, hit.symbol.name)

    La conexion SQLite es la de ``ast_index`` (misma DB, mismo
    schema). RagIndex no la abre ni la cierra.
    """

    def __init__(
        self,
        root: Path,
        *,
        ollama: OllamaClient,
        ast_index: AstIndex | None = None,
        model: str = DEFAULT_MODEL,
    ) -> None:
        self.root = Path(root).resolve()
        self.ollama = ollama
        self.ast_index = ast_index or get_index(self.root)
        self.model = model

    @property
    def con(self) -> sqlite3.Connection:
        return self.ast_index.con

    # -- indexado ------------------------------------------------------------

    def index_pending(
        self,
        *,
        batch_size: int = DEFAULT_BATCH,
        limit: int | None = None,
    ) -> int:
        """Embebe simbolos sin embedding (o con otro modelo).

        Devuelve cuantos se indexaron. Si Ollama falla a mitad,
        para y devuelve lo hecho hasta el momento (idempotente:
        volver a llamar continua donde se quedo).
        """
        cur = self.con.cursor()
        sql = (
            "SELECT s.id, s.file, s.line, s.end_line, s.name, s.kind, "
            "s.signature, s.docstring "
            "FROM symbols s "
            "LEFT JOIN embeddings e ON e.symbol_id = s.id "
            "WHERE (e.symbol_id IS NULL OR e.model != ?) "
            "  AND s.file NOT LIKE 'tests/%' "
            "  AND s.file NOT LIKE '%test_%' "
            "  AND s.kind != 'constant' "
            "ORDER BY s.id"
        )
        params: list[Any] = [self.model]
        if limit is not None:
            sql += " LIMIT ?"
            params.append(int(limit))
        rows = list(cur.execute(sql, params))
        if not rows:
            return 0

        file_cache: dict[str, list[str]] = {}
        indexed = 0
        for i in range(0, len(rows), batch_size):
            batch = rows[i : i + batch_size]
            symbols = [self._row_to_symbol(r) for r in batch]
            chunks = [
                _prefix_for(
                    _chunk_text(s, self._body_for(s, file_cache)),
                    self.model,
                    is_query=False,
                )
                for s in symbols
            ]
            try:
                vecs = self.ollama.embed(chunks, model=self.model)
            except OllamaError as exc:
                logger.warning(
                    "Embed fallo en batch %d (%d simbolos pendientes): %s",
                    i, len(rows) - indexed, exc,
                )
                break
            if len(vecs) != len(symbols):
                logger.warning(
                    "Embed devolvio %d vectores para %d textos; batch saltado",
                    len(vecs), len(symbols),
                )
                break

            now = time.time()
            cur.executemany(
                "INSERT OR REPLACE INTO embeddings"
                "(symbol_id, model, dim, vector, indexed_at) "
                "VALUES (?, ?, ?, ?, ?)",
                [
                    (
                        row["id"],
                        self.model,
                        len(vec),
                        np.asarray(vec, dtype=np.float32).tobytes(),
                        now,
                    )
                    for row, vec in zip(batch, vecs)
                ],
            )
            self.con.commit()
            indexed += len(batch)
        return indexed

    # -- busqueda ------------------------------------------------------------

    def query(
        self,
        text: str,
        *,
        k: int = 5,
        kind: str | None = None,
    ) -> list[RagHit]:
        """Devuelve los k simbolos mas similares a ``text``.

        Filtra por ``kind`` (class/function/method/constant) en SQL
        antes del cosine. Solo considera embeddings del modelo
        configurado: si se cambio de modelo, hay que reindexar.
        """
        q = (text or "").strip()
        if not q:
            return []
        try:
            qvec_list = self.ollama.embed(
                [_prefix_for(q, self.model, is_query=True)],
                model=self.model,
            )
        except OllamaError as exc:
            logger.warning("Embed de query fallo: %s", exc)
            return []
        if not qvec_list:
            return []
        qvec = np.asarray(qvec_list[0], dtype=np.float32)

        cur = self.con.cursor()
        sql = (
            "SELECT s.id, s.file, s.line, s.end_line, s.name, s.kind, "
            "s.signature, s.docstring, e.vector, e.dim "
            "FROM embeddings e JOIN symbols s ON s.id = e.symbol_id "
            "WHERE e.model = ?"
        )
        params: list[Any] = [self.model]
        if kind:
            sql += " AND s.kind = ?"
            params.append(kind)
        rows = list(cur.execute(sql, params))
        if not rows:
            return []

        dim = int(rows[0]["dim"])
        if qvec.shape[0] != dim:
            logger.warning(
                "Query dim %d != indice dim %d (modelo cambiado?)",
                qvec.shape[0], dim,
            )
            return []

        vecs_list: list[np.ndarray] = []
        valid_rows: list[sqlite3.Row] = []
        for r in rows:
            if int(r["dim"]) != dim:
                continue
            try:
                v = np.frombuffer(r["vector"], dtype=np.float32)
            except (ValueError, TypeError) as exc:
                # np.frombuffer lanza ValueError si el buffer no es
                # multiplo del tamano del dtype (fila corrupta). No
                # deberia pasar (lo escribimos nosotros), pero si
                # pasa queremos verlo.
                logger.warning(
                    "Vector corrupto en symbol_id=%s: %s",
                    r["id"], exc,
                )
                continue
            if v.shape[0] != dim:
                continue
            vecs_list.append(v)
            valid_rows.append(r)
        if not vecs_list:
            return []

        A = np.stack(vecs_list)
        qn = float(np.linalg.norm(qvec)) or 1.0
        An = np.linalg.norm(A, axis=1)
        An[An == 0] = 1.0
        sims = (A @ qvec) / (An * qn)

        if k >= len(sims):
            top = np.argsort(-sims)
        else:
            top = np.argpartition(-sims, k)[:k]
            top = top[np.argsort(-sims[top])]

        return [
            RagHit(
                symbol=self._row_to_symbol(valid_rows[i]),
                score=float(sims[i]),
            )
            for i in top[:k]
        ]

    # -- mantenimiento -------------------------------------------------------

    def invalidate_file(self, rel_path: str) -> int:
        """Borra los embeddings de los simbolos de un archivo.

        Devuelve cuantos embeddings se borraron. Normalmente no hace
        falta llamarlo: ast_index._delete_file ya limpia al
        reindexar. Util si quieres forzar reindexado puntual sin
        tocar el AST.
        """
        cur = self.con.cursor()
        cur.execute(
            "DELETE FROM embeddings WHERE symbol_id IN "
            "(SELECT id FROM symbols WHERE file = ?)",
            (rel_path,),
        )
        n = cur.rowcount
        self.con.commit()
        return int(n)

    def cleanup_orphans(self) -> int:
        """Borra embeddings cuyo symbol ya no existe.

        Red de seguridad: _delete_file deberia evitarlos, pero si
        algo se desincroniza (crash a mitad de borrado, migracion
        interrumpida), esto limpia.
        """
        cur = self.con.cursor()
        cur.execute(
            "DELETE FROM embeddings WHERE symbol_id NOT IN "
            "(SELECT id FROM symbols)"
        )
        n = cur.rowcount
        self.con.commit()
        return int(n)

    def stats(self) -> dict:
        """Contadores.

        ``symbols`` refleja solo los elegibles para RAG: sin tests,
        sin constantes. ``pending`` es cuantos de esos faltan por
        indexar. Si quieres el total bruto, mira
        ``ast_index.stats()``.
        """
        cur = self.con.cursor()
        total_sym = int(
            cur.execute(
                "SELECT COUNT(*) FROM symbols "
                "WHERE file NOT LIKE 'tests/%' "
                "  AND file NOT LIKE '%test_%' "
                "  AND kind != 'constant'"
            ).fetchone()[0]
        )
        total_emb = int(
            cur.execute(
                "SELECT COUNT(*) FROM embeddings WHERE model = ?",
                (self.model,),
            ).fetchone()[0]
        )
        return {
            "root": str(self.root),
            "model": self.model,
            "symbols": total_sym,
            "embedded": total_emb,
            "pending": max(0, total_sym - total_emb),
        }

    # -- internos ------------------------------------------------------------

    def _row_to_symbol(self, row: sqlite3.Row) -> Symbol:
        end = row["end_line"] or row["line"]
        return Symbol(
            name=row["name"],
            kind=row["kind"],
            file=row["file"],
            line=row["line"],
            signature=row["signature"],
            docstring=row["docstring"],
            end_line=end,
        )

    def _body_for(
        self,
        symbol: Symbol,
        cache: dict[str, list[str]],
    ) -> str:
        """Cuerpo del simbolo (lineas line..end_line, 1-based)."""
        lines = cache.get(symbol.file)
        if lines is None:
            path = self.root / symbol.file
            try:
                lines = path.read_text(encoding="utf-8").splitlines()
            except (OSError, UnicodeDecodeError):
                lines = []
            cache[symbol.file] = lines
        if not lines:
            return ""
        start = max(0, symbol.line - 1)
        end = symbol.end_line or symbol.line
        return "\n".join(lines[start:end])


# -- cache module-level ------------------------------------------------------


def get_rag_index(
    root: Path,
    *,
    ollama: OllamaClient,
    model: str = DEFAULT_MODEL,
) -> RagIndex:
    """Devuelve el RagIndex compartido para ``root`` (uno por proceso).

    Si el modelo cambia respecto al cacheado, se recrea. Mismo
    patron que ast_index.get_index.
    """
    resolved = Path(root).resolve()
    with _RAG_CACHE_LOCK:
        idx = _RAG_CACHE.get(resolved)
        if idx is None or idx.model != model:
            idx = RagIndex(resolved, ollama=ollama, model=model)
            _RAG_CACHE[resolved] = idx
        return idx


def close_all() -> None:
    """Limpia la cache module-level. Para shutdown y tests.

    No cierra conexiones: RagIndex no tiene ninguna propia, usa la
    de ast_index. Para cerrar SQLite hay que llamar a
    ast_index.close_all().
    """
    with _RAG_CACHE_LOCK:
        _RAG_CACHE.clear()
