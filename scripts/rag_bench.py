#!/usr/bin/env python3
"""Benchmark de retrieval: flat (1/1) vs weighted (3/2).

Corre sobre el propio workspace de ChatPerezoso. Requiere Ollama
con nomic-embed-text instalado.

Uso:
    python3 scripts/rag_bench.py

Salida:
    Tabla comparativa con top-1 y top-5 accuracy en 10 queries.

Opcional:
    --queries N     limita a las primeras N queries
    --skip-weight   corre solo el modo flat
"""
from __future__ import annotations

import argparse
import contextlib
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

import core.rag_index as ri  # noqa: E402
from core.ast_index import AstIndex, get_index  # noqa: E402
from core.ollama import OllamaClient  # noqa: E402
from core.rag_index import RagIndex  # noqa: E402

# Queries + simbolo esperado. Ampliar cuando se añadan simbolos.
QUERIES: list[tuple[str, str]] = [
    ("guardar el historial en disco",
     "core/history.py::HistoryStore"),
    ("leer un archivo del workspace",
     "core/workspace.py::Workspace"),
    ("crear un cliente para Ollama",
     "core/ollama.py::OllamaClient"),
    ("indexar el AST de un workspace",
     "core/ast_index.py::AstIndex"),
    ("buscar simbolos por nombre",
     "core/ast_index.py::AstIndex.search"),
    ("compactar el contexto de la conversacion",
     "core/context_window.py::ContextWindow"),
    ("ejecutar un comando de shell en el workspace",
     "plugins/shell/client.py::ShellClient"),
    ("verificar la sintaxis de un archivo python",
     "plugins/verificador/client.py::check_python_syntax"),
    ("parsear tool calls de una respuesta del modelo",
     "core/tools.py"),
    ("decidir si una tool requiere confirmacion",
     "core/tools.py"),
]


def _patch_repeats(name: int, docstring: int) -> None:
    """Sustituye _chunk_text por version parametrizada."""
    original = ri._chunk_text

    def patched(symbol, body, **_kw):
        return original(
            symbol, body,
            name_repeats=name, docstring_repeats=docstring,
        )

    ri._chunk_text = patched  # type: ignore[assignment]


def _run_mode(
    mode: str, *, name_repeats: int, docstring_repeats: int,
) -> tuple[int, int, int, int]:
    """Indexa y mide. Devuelve (n_indexados, top1, top5, n_queries)."""
    # DB dedicada por modo, para no mezclar embeddings.
    db = Path(f"/tmp/rag_bench_{mode}.sqlite")
    if db.exists():
        db.unlink()

    _patch_repeats(name_repeats, docstring_repeats)

    idx = AstIndex(ROOT, db_path=db)
    idx.refresh()
    ollama = OllamaClient()
    rag = RagIndex(ROOT, ollama=ollama, ast_index=idx)
    n = rag.index_pending()

    top1 = 0
    top5 = 0
    nq = 0
    for query, expected in QUERIES:
        hits = rag.query(query, k=5)
        nq += 1
        names = [f"{h.symbol.file}::{h.symbol.name}" for h in hits]
        if names and names[0] == expected:
            top1 += 1
        if expected in names:
            top5 += 1

    # Cerrar conexiones
    with contextlib.suppress(Exception):
        idx.close()
    db.unlink(missing_ok=True)

    return n, top1, top5, nq


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--queries", type=int, default=0)
    parser.add_argument("--skip-weight", action="store_true")
    args = parser.parse_args()

    if args.queries:
        global QUERIES  # noqa: PLW0603
        QUERIES = QUERIES[:args.queries]

    print(f"Workspace: {ROOT}")
    print(f"Queries: {len(QUERIES)}")
    print()

    print("Modo flat (1/1)...")
    n_flat, t1_flat, t5_flat, nq = _run_mode(
        "flat", name_repeats=1, docstring_repeats=1,
    )
    print(f"  Indexados: {n_flat}, top-1: {t1_flat}/{nq}, "
          f"top-5: {t5_flat}/{nq}")
    print()

    if args.skip_weight:
        return 0

    print("Modo weighted (3/2)...")
    n_w, t1_w, t5_w, _ = _run_mode(
        "weighted", name_repeats=3, docstring_repeats=2,
    )
    print(f"  Indexados: {n_w}, top-1: {t1_w}/{nq}, "
          f"top-5: {t5_w}/{nq}")
    print()

    print("--- Comparativa ---")
    print(f"  top-1: {t1_flat}/{nq}  ->  {t1_w}/{nq}  "
          f"(delta {t1_w - t1_flat:+d})")
    print(f"  top-5: {t5_flat}/{nq}  ->  {t5_w}/{nq}  "
          f"(delta {t5_w - t5_flat:+d})")
    if t1_w < t1_flat or t5_w < t5_flat:
        print()
        print("AVISO: el modo weighted empeora el retrieval. "
              "Revisar _NAME_REPEATS / _DOCSTRING_REPEATS.")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
