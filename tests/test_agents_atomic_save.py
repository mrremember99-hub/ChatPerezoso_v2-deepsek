"""Regresión AG-1: AgentStore.save usa escritura atómica.

Antes, un crash a mitad del write dejaba agents.json corrupto. Al
reiniciar, el load devolvía defaults y el usuario perdía todos sus
agentes personalizados. Ahora se escribe a .tmp y se renombra.
"""
from __future__ import annotations

import json

from core.agents import Agent, AgentStore


def test_save_leaves_no_tmp_file(tmp_path):
    """Tras un save exitoso, no queda ningún .tmp."""
    path = tmp_path / "agents.json"
    store = AgentStore(path)
    store.save([Agent(name="A"), Agent(name="B")])

    tmps = list(tmp_path.glob("*.tmp"))
    assert tmps == [], f"quedaron temporales: {tmps}"
    assert path.exists()


def test_save_is_atomic_visible(tmp_path):
    """El archivo final es el contenido esperado."""
    path = tmp_path / "agents.json"
    store = AgentStore(path)
    store.save([Agent(name="X", temperature=0.5)])

    data = json.loads(path.read_text(encoding="utf-8"))
    assert len(data["agents"]) == 1
    assert data["agents"][0]["name"] == "X"
    assert data["agents"][0]["temperature"] == 0.5


def test_save_does_not_leave_partial_file_on_write_error(
    tmp_path, monkeypatch,
):
    """Si write_text falla, agents.json no se toca."""
    path = tmp_path / "agents.json"
    store = AgentStore(path)

    # Guardar versión inicial.
    store.save([Agent(name="Original")])
    original_content = path.read_text(encoding="utf-8")

    # Simular fallo en write_text del .tmp.
    def failing_write(*args, **kwargs):
        raise OSError("disco lleno")

    from pathlib import Path as _P
    monkeypatch.setattr(_P, "write_text", failing_write)

    # El save debe fallar silenciosamente (except OSError: pass).
    store.save([Agent(name="Nuevo")])

    # El archivo original sigue intacto.
    assert path.read_text(encoding="utf-8") == original_content
    