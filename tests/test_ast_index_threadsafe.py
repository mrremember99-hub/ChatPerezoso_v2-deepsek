"""Overpaper run #1: AstIndex debe ser usable desde otro hilo."""
from __future__ import annotations

import sys
import threading
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core.ast_index import AstIndex  # noqa: E402


def test_ast_index_cross_thread(tmp_path):
    """Crear en main thread, usar en worker: no debe lanzar."""
    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.py").write_text(
        "def f():\n    return 1\n", encoding="utf-8",
    )
    idx = AstIndex(root, db_path=tmp_path / "idx.sqlite")

    errs: list[Exception] = []
    results: list = []

    def worker():
        try:
            idx.refresh()
            results.append(idx.search("f"))
            results.append(idx.stats())
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=5.0)

    assert errs == [], f"excepcion en worker: {errs!r}"
    assert len(results) == 2
    assert any(s.name == "f" for s in results[0])


def test_ast_index_multi_workers(tmp_path):
    """3 workers concurrentes: sin excepciones, resultados coherentes."""
    root = tmp_path / "ws"
    root.mkdir()
    for i in range(3):
        (root / f"m{i}.py").write_text(
            f"def fn_{i}():\n    return {i}\n", encoding="utf-8",
        )
    idx = AstIndex(root, db_path=tmp_path / "idx.sqlite")
    idx.refresh()

    errs: list[Exception] = []
    seen: list[list] = []
    lock = threading.Lock()

    def worker():
        try:
            res = idx.search("fn_")
            with lock:
                seen.append(res)
        except Exception as e:  # noqa: BLE001
            with lock:
                errs.append(e)

    threads = [threading.Thread(target=worker) for _ in range(3)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert errs == [], f"excepciones en workers: {errs!r}"
    assert len(seen) == 3
    for r in seen:
        assert len(r) == 3


# ── rag_index tambien debe ser thread-safe ──────────────────────

class _FakeOllama:
    def embed(self, *a, **kw):
        return []


def test_rag_stats_cross_thread(tmp_path):
    """RagIndex.stats desde worker: no debe lanzar."""
    from core.rag_index import RagIndex

    root = tmp_path / "ws"
    root.mkdir()
    (root / "a.py").write_text("def f(): pass\n", encoding="utf-8")
    idx = AstIndex(root, db_path=tmp_path / "idx.sqlite")
    idx.refresh()
    rag = RagIndex(root, ollama=_FakeOllama(), ast_index=idx, model="m")

    errs: list[Exception] = []
    out: list[dict] = []

    def worker():
        try:
            out.append(rag.stats())
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    t = threading.Thread(target=worker)
    t.start()
    t.join(timeout=5.0)

    assert errs == [], f"excepcion en worker: {errs!r}"
    assert len(out) == 1
    assert "symbols" in out[0]


def test_rag_search_y_ast_concurrentes(tmp_path):
    """search() y rag.stats() en paralelo: sin excepciones."""
    from core.rag_index import RagIndex

    root = tmp_path / "ws"
    root.mkdir()
    for i in range(3):
        (root / f"m{i}.py").write_text(
            f"def fn_{i}(): pass\n", encoding="utf-8",
        )
    idx = AstIndex(root, db_path=tmp_path / "idx.sqlite")
    idx.refresh()
    rag = RagIndex(root, ollama=_FakeOllama(), ast_index=idx, model="m")

    errs: list[Exception] = []

    def w_ast():
        try:
            for _ in range(20):
                idx.search("fn_")
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    def w_rag():
        try:
            for _ in range(20):
                rag.stats()
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    threads = [threading.Thread(target=w_ast) for _ in range(2)]
    threads += [threading.Thread(target=w_rag) for _ in range(2)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert errs == [], f"excepciones: {errs!r}"
