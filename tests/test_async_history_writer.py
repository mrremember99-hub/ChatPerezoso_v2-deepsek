"""Regresión A3: AsyncHistoryWriter encola y drena sin bloquear.

Antes, HistoryStore.save() corría en el hilo UI. Con historiales de
varios MB eso son decenas/cientos de ms de freeze en el hilo Qt.

Ahora se encola en un ThreadPoolExecutor(max_workers=1):
  · submit() no bloquea.
  · flush() espera a que la cola termine.
  · shutdown() cierra limpio.
  · El snapshot es inmutable: mutar el original no afecta al disco.
"""
from __future__ import annotations

import json
import threading
import time

from core.history import AsyncHistoryWriter, HistoryStore, _MAX_MESSAGES


def _make_writer(tmp_path):
    store = HistoryStore(tmp_path / "h.json")
    return AsyncHistoryWriter(store), store, tmp_path / "h.json"


def test_submit_does_not_block_ui_thread(tmp_path):
    """submit() de un historial grande debe volver rápido."""
    writer, _, _ = _make_writer(tmp_path)

    # 500 mensajes grandes: ~5 MB. json.dumps + write tarda decenas
    # de ms. submit() debe devolver en menos de 20 ms.
    big = [
        {"role": "user", "content": "x" * 10_000}
        for _ in range(500)
    ]

    t0 = time.monotonic()
    writer.submit(big)
    elapsed_ms = (time.monotonic() - t0) * 1000

    writer.shutdown()

    assert elapsed_ms < 20, (
        f"submit bloqueó {elapsed_ms:.1f} ms (esperado <20)"
    )


def test_flush_waits_for_write(tmp_path):
    """Tras flush(), el archivo está en disco."""
    writer, store, _ = _make_writer(tmp_path)
    writer.submit([{"role": "user", "content": "hola"}])
    writer.flush()

    loaded = store.load()
    assert loaded is not None
    assert loaded.messages == [{"role": "user", "content": "hola"}]

    writer.shutdown()


def test_snapshot_is_immutable(tmp_path):
    """Mutar la lista original NO afecta a lo escrito."""
    writer, store, _ = _make_writer(tmp_path)
    messages = [{"role": "user", "content": "original"}]
    writer.submit(messages)
    writer.flush()

    # Mutar el original después del submit.
    messages[0]["content"] = "mutado"

    loaded = store.load()
    assert loaded is not None
    assert loaded.messages[0]["content"] == "original"

    writer.shutdown()


def test_cap_applies_at_writer_level(tmp_path):
    """El writer capa a _MAX_MESSAGES, igual que HistoryStore.save."""
    writer, store, _ = _make_writer(tmp_path)
    messages = [
        {"role": "user", "content": f"m{i}"}
        for i in range(_MAX_MESSAGES + 30)
    ]
    writer.submit(messages)
    writer.flush()

    loaded = store.load()
    assert loaded is not None
    assert len(loaded.messages) == _MAX_MESSAGES
    # Deben ser los últimos, no los primeros.
    assert loaded.messages[0]["content"] == f"m{30}"

    writer.shutdown()


def test_writes_serialize_in_order(tmp_path):
    """Con un solo worker, las escrituras no se pisan."""
    writer, store, _ = _make_writer(tmp_path)
    for i in range(5):
        writer.submit([{"role": "user", "content": f"turno-{i}"}])
    writer.flush()

    loaded = store.load()
    assert loaded is not None
    # El último write es el que sobrevive.
    assert loaded.messages[-1]["content"] == "turno-4"

    writer.shutdown()


def test_shutdown_drains_pending(tmp_path):
    """shutdown() no debe dejar escrituras a medias."""
    writer, store, _ = _make_writer(tmp_path)
    for i in range(10):
        writer.submit([{"role": "user", "content": f"n{i}"}])
    writer.shutdown()

    loaded = store.load()
    assert loaded is not None
    assert loaded.messages[-1]["content"] == "n9"
    