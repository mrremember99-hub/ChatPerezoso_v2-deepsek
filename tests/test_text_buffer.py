"""Tests del TextDeltaBuffer del streaming."""
from __future__ import annotations

import threading

from ui.workers import TextDeltaBuffer


def test_buffer_returns_true_on_first_push():
    buf = TextDeltaBuffer()
    assert buf.push("a") is True


def test_buffer_returns_false_when_already_non_empty():
    buf = TextDeltaBuffer()
    buf.push("a")
    assert buf.push("b") is False
    assert buf.push("c") is False


def test_buffer_drain_returns_all_and_resets():
    buf = TextDeltaBuffer()
    buf.push("hola")
    buf.push(" ")
    buf.push("mundo")
    assert buf.drain() == "hola mundo"
    assert buf.drain() == ""


def test_buffer_returns_true_after_drain():
    buf = TextDeltaBuffer()
    buf.push("a")
    buf.drain()
    assert buf.push("b") is True


def test_buffer_empty_push_is_noop():
    buf = TextDeltaBuffer()
    assert buf.push("") is False
    assert buf.drain() == ""


def test_buffer_pending_chars():
    buf = TextDeltaBuffer()
    buf.push("abc")
    buf.push("de")
    assert buf.pending_chars == 5
    buf.drain()
    assert buf.pending_chars == 0


def test_buffer_pending_chars_reflects_accumulated():
    buf = TextDeltaBuffer(max_chars=1000)
    buf.push("abc")
    buf.push("de")
    assert buf.pending_chars == 5
    buf.drain()
    assert buf.pending_chars == 0


def test_buffer_push_does_not_block_when_under_limit():
    """Con espacio libre, push() retorna inmediatamente."""
    import time
    buf = TextDeltaBuffer(max_chars=1000)
    t0 = time.monotonic()
    for _ in range(100):
        buf.push("x")
    elapsed = time.monotonic() - t0
    assert elapsed < 0.1
    assert buf.pending_chars == 100


def test_buffer_push_blocks_until_space_or_cancel():
    """Con buffer lleno, push() bloquea hasta cancelacion."""
    import threading
    buf = TextDeltaBuffer(max_chars=10)
    buf.push("x" * 20)  # desborda
    assert buf.pending_chars == 20

    cancel = threading.Event()

    def pusher():
        buf.push("y", cancel_event=cancel)

    t = threading.Thread(target=pusher, daemon=True)
    t.start()
    t.join(timeout=0.3)
    assert t.is_alive(), "push deberia haber bloqueado"

    cancel.set()
    t.join(timeout=1.0)
    assert not t.is_alive(), "push no se desbloqueo al cancelar"


def test_buffer_push_unblocks_after_drain():
    """Si otro hilo drena, push() se desbloquea."""
    import threading
    import time

    buf = TextDeltaBuffer(max_chars=10)
    buf.push("x" * 20)

    def delayed_drain():
        time.sleep(0.1)
        buf.drain()

    threading.Thread(target=delayed_drain, daemon=True).start()

    t0 = time.monotonic()
    buf.push("y")
    elapsed = time.monotonic() - t0
    # Se desbloquea por el drain, no por timeout.
    assert elapsed < 1.0, f"push() espero demasiado ({elapsed:.2f}s)"


def test_buffer_is_thread_safe():
    """Varios hilos haciendo push concurrente no pierden datos."""
    buf = TextDeltaBuffer()
    barrier = threading.Barrier(10)
    total_per_thread = 100

    def worker(prefix: int) -> None:
        barrier.wait()
        for i in range(total_per_thread):
            buf.push(f"{prefix}-{i};")

    threads = [
        threading.Thread(target=worker, args=(n,)) for n in range(10)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    text = buf.drain()
    # Cada push produce un fragmento terminado en ";".
    assert text.count(";") == 10 * total_per_thread
    # Todos los prefijos presentes.
    for n in range(10):
        assert f"{n}-0;" in text
        assert f"{n}-{total_per_thread - 1};" in text
