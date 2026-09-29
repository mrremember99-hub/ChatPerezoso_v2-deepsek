#!/usr/bin/env python3
"""A/B del prompt de tools: pipeline real (con gate + registry).

Adaptado de scripts/eval_tools.py (2026-09-28). El codigo original
sigue en scripts/eval_tools.py para no duplicar 394 lineas. Este
modulo añade la capa de comparacion contra baseline.

Uso:
    python -m scripts.eval.ab_tools --variant actual --model qwen3:1.7b
    python -m scripts.eval.ab_tools --variant both
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent.parent


def main() -> int:
    """Delegar en scripts.eval_tools.main() (wrapper externo)."""
    sys.path.insert(0, str(ROOT / "scripts"))
    import eval_tools  # noqa: E402
    return eval_tools.main()


if __name__ == "__main__":
    raise SystemExit(main())
