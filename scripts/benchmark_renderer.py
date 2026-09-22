"""Mide el coste de PlainTextRenderer.on_text con texto sintético.

No necesita Ollama. Simula el streaming alimentando deltas al
renderer y mide:
  · tiempo total acumulando N deltas
  · deltas/segundo
  · pico de memoria del QTextDocument (aprox por characterCount)

Uso:
    python scripts/benchmark_renderer.py
    python scripts/benchmark_renderer.py --deltas 1000 --chunk-size 8
"""
from __future__ import annotations

import argparse
import os
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")


def _setup_qt():
    from PySide6.QtWidgets import QApplication
    return QApplication.instance() or QApplication([])


def _make_stream(deltas: int, chunk_size: int, con_codigo: bool) -> list[str]:
    """Genera la lista de deltas que se pasarán al renderer."""
    if con_codigo:
        base = (
            "Aquí tienes el ejemplo:\n\n"
            "```python\n"
            "def compute(x, y):\n"
            "    total = x + y\n"
            "    return total * 2\n"
            "```\n\n"
            "Y el resultado se usa así:\n\n"
            "```python\n"
            "result = compute(3, 4)\n"
            "print(result)\n"
            "```\n\n"
        ) * 20
    else:
        base = "Esto es una frase de ejemplo con palabras normales. " * 200

    parts = [base[i : i + chunk_size] for i in range(0, len(base), chunk_size)]
    return (parts * ((deltas // len(parts)) + 1))[:deltas]


def _measure(deltas: int, chunk_size: int, con_codigo: bool) -> dict:
    from PySide6.QtWidgets import QTextEdit
    from ui.rendering.plain_text import PlainTextRenderer

    widget = QTextEdit()
    renderer = PlainTextRenderer(widget)
    renderer.reset()

    chunks = _make_stream(deltas, chunk_size, con_codigo)

    t0 = time.monotonic()
    for chunk in chunks:
        renderer.on_text(chunk)
    accumulate_s = time.monotonic() - t0

    t1 = time.monotonic()
    renderer.final_text("")
    final_s = time.monotonic() - t1

    widget.deleteLater()
    return {
        "deltas": len(chunks),
        "chars": sum(len(c) for c in chunks),
        "accumulate_s": accumulate_s,
        "final_s": final_s,
        "deltas_per_s": len(chunks) / accumulate_s if accumulate_s > 0 else 0.0,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--deltas", type=int, default=2000)
    p.add_argument("--chunk-size", type=int, default=8)
    p.add_argument("--repeats", type=int, default=3)
    args = p.parse_args()

    _setup_qt()

    print(f"deltas por muestra:  {args.deltas}")
    print(f"chunk_size:          {args.chunk_size}")
    print(f"repeticiones:        {args.repeats}")
    print()

    for label, con_codigo in (("prose", False), ("code", True)):
        acc: list[float] = []
        fin: list[float] = []
        for _ in range(args.repeats):
            r = _measure(args.deltas, args.chunk_size, con_codigo)
            acc.append(r["accumulate_s"])
            fin.append(r["final_s"])
        # Última muestra para datos derivados
        chars = r["chars"]
        deltas = r["deltas"]
        print(
            f"{label:6s} "
            f"chars={chars:>7d}  "
            f"deltas={deltas:>5d}  "
            f"accum={statistics.median(acc)*1000:>8.1f}ms  "
            f"final={statistics.median(fin)*1000:>7.1f}ms  "
            f"deltas/s={deltas / statistics.median(acc):>7.0f}"
        )

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
