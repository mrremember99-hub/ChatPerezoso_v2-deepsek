#!/usr/bin/env python3
"""Wrapper: delega en scripts.eval.runner (2026-09-28).

Mantenido por compatibilidad con muscle memory / scripts externos.
"""
from __future__ import annotations

import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))
from scripts.eval.runner import main  # noqa: E402

if __name__ == "__main__":
    raise SystemExit(main())
