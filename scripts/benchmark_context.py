"""Mide el coste de la compactación de contexto y compara estimaciones.

No necesita Ollama. Genera historiales sintéticos (prosa, código, mixto)
y reporta:
  · estimación con la heurística actual (chars/4)
  · estimación con ratio ponderado por densidad de código
  · tiempo de compactar con la lógica actual

Uso:
    python scripts/benchmark_context.py
    python scripts/benchmark_context.py --turns 200 --limit 32768
"""
from __future__ import annotations

import argparse
import statistics
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))


PROSE = (
    "En un sistema de gestión de contexto, la ventana de tokens del modelo "
    "es un recurso limitado. Cuando el historial crece, hay que decidir qué "
    "se descarta. Las heurísticas de estimación son suficientes mientras "
    "haya margen suficiente para absorber el error. "
)

CODE = (
    "def process(messages: list[dict]) -> list[dict]:\n"
    "    total = sum(len(m.get('content', '')) for m in messages)\n"
    "    if total > LIMIT:\n"
    "        return messages[-10:]\n"
    "    return messages\n"
)


def _build_history(kind: str, turns: int) -> list[dict]:
    messages: list[dict] = []
    for i in range(turns):
        if kind == "prose":
            u, a = PROSE * 3, PROSE * 5
        elif kind == "code":
            u, a = CODE * 8, CODE * 12
        else:  # mixed
            u = PROSE * 3 + CODE * 4
            a = PROSE * 4 + CODE * 6
        messages.append({"role": "user", "content": f"[turno {i}] {u}"})
        messages.append({"role": "assistant", "content": a})
    return messages


def _estimate_chars4(messages: list[dict]) -> int:
    return sum(
        len(m["content"]) for m in messages
        if isinstance(m.get("content"), str)
    ) // 4


def _estimate_weighted(messages: list[dict]) -> int:
    """Ratio ajustado: bloques densos en código tokenizan peor."""
    total = 0.0
    for m in messages:
        content = m.get("content", "")
        if not isinstance(content, str):
            continue
        code_hint = content.count("\n    ") + content.count("{") + content.count(";")
        ratio = 2.8 if code_hint > 5 else 4.2
        total += len(content) / ratio
    return int(total)


def _time_compact(messages: list[dict], limit: int, repeats: int) -> float:
    """Mide el ContextWindow real con fit()."""
    from core.context_window import ContextWindow

    window = ContextWindow(
        limit_tokens=limit,
        output_reserve=min(1024, limit // 4),
        min_turns=8,
    )

    times: list[float] = []
    for _ in range(repeats):
        t0 = time.monotonic()
        window.fit(
            system_prompt="",
            tool_definitions=[],
            messages=messages,
        )
        times.append(time.monotonic() - t0)
    return statistics.median(times)


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--turns", type=int, default=100)
    p.add_argument("--limit", type=int, default=8192)
    p.add_argument("--repeats", type=int, default=50)
    args = p.parse_args()

    print(f"Turnos por historial: {args.turns}")
    print(f"Límite de contexto:   {args.limit} tokens")
    print()
    print(f"{'Tipo':8s} {'chars':>10s} {'chars/4':>10s} {'weighted':>10s} "
          f"{'diff%':>8s} {'compact_ms':>12s}")
    print("-" * 62)

    for kind in ("prose", "code", "mixed"):
        h = _build_history(kind, args.turns)
        chars = sum(len(m["content"]) for m in h)
        e4 = _estimate_chars4(h)
        ew = _estimate_weighted(h)
        diff = (ew - e4) / e4 * 100 if e4 else 0.0
        t_ms = _time_compact(h, args.limit, args.repeats) * 1000
        print(f"{kind:8s} {chars:>10d} {e4:>10d} {ew:>10d} "
              f"{diff:>7.1f}% {t_ms:>11.3f}ms")

    print()
    print("Notas:")
    print("  · chars/4 es la heurística actual del código.")
    print("  · weighted usa 2.8 chars/tok para código y 4.2 para prosa.")
    print("  · diff% > 20% en 'code' o 'mixed' justifica un tokenizer real.")
    print("  · compact_ms > 5 ms sería preocupante; hoy debe ser < 1 ms.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
