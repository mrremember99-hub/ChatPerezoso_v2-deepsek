"""Overpaper run #3: F821 debe mencionar orden de definicion."""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from plugins.verificador.client import _RUFF_SUGGESTIONS  # noqa: E402


def test_f821_menciona_orden_de_definicion():
    sug = _RUFF_SUGGESTIONS.get("F821", "").lower()
    assert "orden" in sug
    assert "arriba del uso" in sug


def test_f821_mantiene_causas_previas():
    sug = _RUFF_SUGGESTIONS.get("F821", "")
    # Causa (1) except as X, causa (2) import olvidado, (3) typo.
    assert "except" in sug
    assert "import olvidado" in sug
    assert "typo" in sug
