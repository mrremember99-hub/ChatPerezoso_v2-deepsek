"""Tests de core.shutdown.close_auxiliary_caches.

Sin Qt, sin red. Monkeypatch sobre core.ast_index.close_all.
"""
from __future__ import annotations

import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from core import ast_index, shutdown  # noqa: E402


def test_close_auxiliary_caches_llama_close_all(monkeypatch):
    calls = {"n": 0}

    def fake_close_all():
        calls["n"] += 1

    monkeypatch.setattr(ast_index, "close_all", fake_close_all)
    shutdown.close_auxiliary_caches()
    assert calls["n"] == 1


def test_close_auxiliary_caches_traga_excepciones(monkeypatch):
    def boom():
        raise RuntimeError("simulado")

    monkeypatch.setattr(ast_index, "close_all", boom)
    # No debe propagar: el shutdown sigue aunque el cache falle.
    shutdown.close_auxiliary_caches()


def test_close_auxiliary_caches_idempotente(monkeypatch):
    calls = {"n": 0}

    def fake_close_all():
        calls["n"] += 1

    monkeypatch.setattr(ast_index, "close_all", fake_close_all)
    shutdown.close_auxiliary_caches()
    shutdown.close_auxiliary_caches()
    assert calls["n"] == 2
