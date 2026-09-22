"""Mide el rendimiento del pipeline de streaming de ChatPerezoso.

Uso:
    python scripts/benchmark_streaming.py --model llama3.1:latest
    python scripts/benchmark_streaming.py --model X --repeats 5 --skip-renderer

Requiere Ollama corriendo. Usa QApplication offscreen para el renderer.
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


def _measure_stream(host: str, model: str, prompt: str) -> dict:
    from core.ollama import OllamaClient

    client = OllamaClient(host)
    timestamps: list[float] = []
    chars: list[int] = []
    t0 = time.monotonic()

    def on_text(text: str) -> None:
        timestamps.append(time.monotonic() - t0)
        chars.append(len(text))

    try:
        result = client.chat(
            model,
            [{"role": "user", "content": prompt}],
            tools=None,
            on_text=on_text,
            on_tool=lambda *_: "",
            options={"temperature": 0.0},
        )
    finally:
        client.shutdown()

    total_time = time.monotonic() - t0
    total_chars = sum(chars)
    return {
        "ttft_s": timestamps[0] if timestamps else None,
        "total_s": total_time,
        "chunks": len(timestamps),
        "chars": total_chars,
        "chunks_per_s": len(timestamps) / total_time if total_time > 0 else 0.0,
        "chars_per_chunk": total_chars / len(chars) if chars else 0.0,
        "approx_tok_s": (total_chars / 4) / total_time if total_time > 0 else 0.0,
        "result_len": len(result),
    }


def _measure_renderer(deltas: int = 500, chunk_size: int = 8) -> dict:
    """Alimenta PlainTextRenderer con deltas sintéticos.

    El renderer actual (tras los parches AV + AT) no tiene buffer
    propio: escribe directamente al QTextDocument. Aquí medimos:
      · coste de aplicar `deltas` llamadas a on_text()
      · coste de una pasada final de final_text() (Markdown + Pygments)
    """
    from PySide6.QtWidgets import QTextEdit
    from ui.rendering.plain_text import PlainTextRenderer

    widget = QTextEdit()
    renderer = PlainTextRenderer(widget)
    renderer.reset()

    base = "palabra " * 200
    parts = [base[i:i + chunk_size] for i in range(0, len(base), chunk_size)]
    parts = (parts * ((deltas // len(parts)) + 1))[:deltas]

    t0 = time.monotonic()
    for p in parts:
        renderer.on_text(p)
    accumulate_s = time.monotonic() - t0

    # Forzar el render final (Markdown + Pygments si hay bloques).
    t1 = time.monotonic()
    renderer.final_text("")
    final_s = time.monotonic() - t1

    return {
        "deltas": len(parts),
        "chars": sum(len(p) for p in parts),
        "accumulate_s": accumulate_s,
        "flush_s": final_s,      # mantenemos el nombre por compatibilidad del print
        "max_pending": 0,        # ya no hay buffer interno en el renderer
        "deltas_per_s": len(parts) / accumulate_s if accumulate_s > 0 else 0.0,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--host", default="http://localhost:11434")
    p.add_argument("--model", required=True)
    p.add_argument("--prompt",
                   default="Explica en 3 párrafos qué es un closure en programación.")
    p.add_argument("--repeats", type=int, default=3)
    p.add_argument("--skip-renderer", action="store_true")
    args = p.parse_args()

    _setup_qt()

    print(f"Modelo:       {args.model}")
    print(f"Host:         {args.host}")
    print(f"Prompt:       {args.prompt[:70]}{'…' if len(args.prompt) > 70 else ''}")
    print(f"Repeticiones: {args.repeats}")
    print()

    results = []
    for i in range(args.repeats):
        print(f"[{i + 1}/{args.repeats}] midiendo stream…")
        try:
            r = _measure_stream(args.host, args.model, args.prompt)
        except Exception as exc:
            print(f"  ERROR: {exc}")
            return 1
        results.append(r)
        print(f"  TTFT={r['ttft_s']:.3f}s  total={r['total_s']:.2f}s  "
              f"chunks={r['chunks']} ({r['chunks_per_s']:.1f}/s)  "
              f"chars/chunk={r['chars_per_chunk']:.1f}  "
              f"~tok/s={r['approx_tok_s']:.1f}")

    print()
    print(f"Streaming — mediana de {len(results)}:")
    for key in ("ttft_s", "total_s", "chunks", "chunks_per_s",
                "chars_per_chunk", "approx_tok_s"):
        vals = [r[key] for r in results if r[key] is not None]
        if vals:
            print(f"  {key:20s} {statistics.median(vals):>10.3f}")

    if not args.skip_renderer:
        print()
        print("Renderer (offscreen, sin Ollama):")
        try:
            rm = _measure_renderer()
            print(f"  deltas={rm['deltas']}  chars={rm['chars']}  "
                  f"accumulate={rm['accumulate_s'] * 1000:.1f}ms  "
                  f"flush={rm['flush_s'] * 1000:.1f}ms  "
                  f"max_pending={rm['max_pending']}")
            print(f"  deltas/s acumulando: {rm['deltas_per_s']:.0f}")
        except Exception as exc:
            print(f"  ERROR: {exc}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
