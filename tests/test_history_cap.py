"""Regresión HS-1: save respeta _MAX_MESSAGES.

Antes, HistoryStore.save escribía TODOS los mensajes en disco. Al
reiniciar, load solo cargaba los últimos 200. Los anteriores se
perdían silenciosamente y se reescribían cada sesión.
"""
from __future__ import annotations

from core.history import HistoryStore, _MAX_MESSAGES


def test_save_caps_at_max_messages(tmp_path):
    """Guardar más de _MAX_MESSAGES solo persiste los últimos."""
    store = HistoryStore(tmp_path / "h.json")
    messages = [
        {"role": "user" if i % 2 == 0 else "assistant",
         "content": f"msg-{i}"}
        for i in range(_MAX_MESSAGES + 50)
    ]
    store.save(messages)

    loaded = store.load()
    assert loaded is not None
    assert len(loaded.messages) == _MAX_MESSAGES
    # Deben ser los ÚLTIMOS, no los primeros.
    assert loaded.messages[0]["content"] == f"msg-{50}"
    assert loaded.messages[-1]["content"] == f"msg-{_MAX_MESSAGES + 49}"


def test_save_under_cap_keeps_all(tmp_path):
    """Con menos de _MAX_MESSAGES no se pierde nada."""
    store = HistoryStore(tmp_path / "h.json")
    messages = [
        {"role": "user", "content": f"m{i}"}
        for i in range(10)
    ]
    store.save(messages)
    loaded = store.load()
    assert loaded is not None
    assert len(loaded.messages) == 10
    assert loaded.messages[0]["content"] == "m0"


def test_save_cap_applies_after_filtering(tmp_path):
    """El cap se aplica tras filtrar roles no conversacionales."""
    store = HistoryStore(tmp_path / "h.json")
    # Intercalar roles que el save filtra.
    messages = []
    for i in range(_MAX_MESSAGES + 10):
        messages.append({"role": "user", "content": f"u{i}"})
        messages.append({"role": "tool", "content": "no guardar"})
    store.save(messages)

    loaded = store.load()
    assert loaded is not None
    assert len(loaded.messages) == _MAX_MESSAGES
    # Todos deben ser "user" (los "tool" se filtraron).
    assert all(m["role"] == "user" for m in loaded.messages)