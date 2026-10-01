"""Tests de concurrencia del AsyncRunner.

Cubren, en particular, la race entre submit() y close(): submit()
leía self._loop fuera del lock, así que close() podía ponerlo a None
entre _ensure_loop() y la lectura, disparando AssertionError (o
AttributeError con python -O).
"""
from __future__ import annotations

import asyncio
import threading
import time

import pytest

from core.async_runner import AsyncRunner, _CancelledByEvent


def test_submit_returns_result():
    runner = AsyncRunner()
    try:
        async def co():
            return 42
        assert runner.submit(co()) == 42
    finally:
        runner.close()


def test_submit_propagates_exception():
    runner = AsyncRunner()
    try:
        async def co():
            raise ValueError("boom")
        with pytest.raises(ValueError, match="boom"):
            runner.submit(co())
    finally:
        runner.close()


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_submit_after_close_raises():
    runner = AsyncRunner()
    runner.close()

    async def co():
        return 1

    with pytest.raises(RuntimeError, match="cerrado"):
        runner.submit(co())


def test_ensure_loop_returns_loop():
    """El contrato nuevo: _ensure_loop devuelve el loop vivo."""
    runner = AsyncRunner()
    try:
        loop = runner._ensure_loop()
        assert loop is not None
        assert loop is runner._loop
    finally:
        runner.close()


def test_submit_does_not_read_self_loop_outside_lock(monkeypatch):
    """Regresión directa: submit() debe usar el loop devuelto por
    _ensure_loop(), no releer self._loop."""
    runner = AsyncRunner()
    real_loop = None
    try:
        async def ping():
            return 1
        assert runner.submit(ping()) == 1

        real_loop = runner._loop
        original_ensure = runner._ensure_loop

        def ensure_that_simulates_close_race():
            loop = original_ensure()
            runner._loop = None  # simular close() concurrente
            return loop

        monkeypatch.setattr(
            runner, "_ensure_loop", ensure_that_simulates_close_race
        )

        async def co():
            return 42

        assert runner.submit(co()) == 42
    finally:
        # Si real_loop nunca se asigno (fallo antes del submit),
        # dejamos None; close() lo maneja sin problemas.
        if real_loop is not None:
            runner._loop = real_loop
        runner.close()


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_submit_and_close_do_not_raise_assertion_error():
    """Estrés: submit concurrente con close. Nunca AssertionError."""
    async def co():
        await asyncio.sleep(0)
        return 42

    unexpected: list[BaseException] = []
    iterations = 50

    for _ in range(iterations):
        runner = AsyncRunner()
        barrier = threading.Barrier(2)

        def submitter():
            barrier.wait()
            try:
                runner.submit(co())
            except (RuntimeError, _CancelledByEvent, TimeoutError):
                pass
            except BaseException as exc:  # noqa: BLE001
                unexpected.append(exc)

        t = threading.Thread(target=submitter)
        t.start()
        barrier.wait()
        runner.close()
        t.join(timeout=2)

    assert not unexpected, (
        f"Excepciones inesperadas en submit/close: {unexpected}"
    )


def test_cancel_event_interrupts_long_coroutine():
    runner = AsyncRunner()
    try:
        async def co():
            await asyncio.sleep(30)
            return "no deberia llegar"

        cancel = threading.Event()

        def canceller():
            time.sleep(0.1)
            cancel.set()

        threading.Thread(target=canceller, daemon=True).start()

        with pytest.raises(_CancelledByEvent):
            runner.submit(co(), cancel_event=cancel)
    finally:
        runner.close()


def test_cancel_event_already_set():
    runner = AsyncRunner()
    try:
        async def co():
            await asyncio.sleep(30)
            return "nunca"

        cancel = threading.Event()
        cancel.set()

        with pytest.raises(_CancelledByEvent):
            runner.submit(co(), cancel_event=cancel)
    finally:
        runner.close()


def test_timeout_interrupts_long_coroutine():
    runner = AsyncRunner()
    try:
        async def co():
            await asyncio.sleep(30)
            return "nunca"

        with pytest.raises(TimeoutError):
            runner.submit(co(), timeout=0.1)
    finally:
        runner.close()


# ── X2.2: close fallido conserva handles ─────────────────────────
# Auditoria externa 2026-09-29, P1#3.
#
# Antes, si thread.join() expiraba, se hacía _loop=None y _thread=None
# igualmente. El llamante perdia la posibilidad de inspeccionar el
# estado. Ahora solo se limpian si el cierre fue limpio.

def test_close_failed_join_conserva_handles(monkeypatch):
    runner = AsyncRunner()
    try:
        async def co():
            return 1
        runner.submit(co())
        thread = runner._thread
        assert thread is not None

        # Simulamos un hilo que no muere: is_alive siempre True,
        # join no-op.
        monkeypatch.setattr(thread, "is_alive", lambda: True)
        monkeypatch.setattr(thread, "join", lambda timeout=None: None)

        ok = runner.close(timeout=0.1)

        assert ok is False, "close debe reportar fallo"
        assert runner._loop is not None, "handles conservados tras fallo"
        assert runner._thread is not None
        # Segundo close: _closed ya es True, devuelve True sin
        # tocar los handles (que siguen ahi).
        assert runner.close() is True
        assert runner._loop is not None
    finally:
        # Cleanup real: restaurar metodos y esperar al hilo.
        monkeypatch.undo()
        if thread is not None:
            thread.join(timeout=2)


def test_close_limpio_libera_handles():
    """Control: si el cierre es limpio, los handles se liberan."""
    runner = AsyncRunner()

    async def co():
        return 1
    runner.submit(co())
    assert runner._thread is not None

    ok = runner.close(timeout=3.0)
    assert ok is True
    assert runner._loop is None
    assert runner._thread is None


def test_cancel_durante_coroutine_ya_en_ejecucion():
    """2026-10-01: el watcher cancela el task asyncio subyacente.

    Regresion: antes, si la coroutine ya estaba corriendo (p.ej.
    Ollama en prefill, 30-60s sin emitir bytes), future.cancel()
    devolvia False y el watcher salia sin hacer nada. El task
    seguia vivo y el shutdown abortaba el QThread -> SIGABRT.
    """
    import asyncio
    import threading
    import time

    from core.async_runner import AsyncRunner

    runner = AsyncRunner(name="test-cancel-running")
    try:
        async def slow_op():
            # Simula prefill: bloqueado 10s sin emitir nada.
            await asyncio.sleep(10)
            return "nunca deberia llegar"

        cancel = threading.Event()

        def cancel_soon():
            time.sleep(0.3)
            cancel.set()

        threading.Thread(target=cancel_soon, daemon=True).start()

        t0 = time.monotonic()
        try:
            runner.submit(slow_op(), cancel_event=cancel)
            raise AssertionError("debio lanzar cancelacion")
        except Exception as exc:
            # _CancelledByEvent o CancelledError, lo importante es
            # que salga rapido.
            elapsed = time.monotonic() - t0
            assert elapsed < 2.0, (
                f"cancel tardo {elapsed:.2f}s (esperado <2s)"
            )
    finally:
        runner.close(timeout=2.0)
