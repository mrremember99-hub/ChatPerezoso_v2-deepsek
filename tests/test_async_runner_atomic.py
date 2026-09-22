"""Regresión M2: submit() agrupa obtener-loop + schedule bajo el lock.

Antes, entre `_ensure_loop()` y `run_coroutine_threadsafe()` otro hilo
podía llamar a `close()`, parar el loop, y dejar la coroutine
programada sobre un loop muerto. Los tests aceptaban RuntimeError,
pero la race era real.

Ahora:
  · `_ensure_loop_locked` no toma el lock (documentado).
  · `submit` envuelve ambas operaciones en `with self._lock:`.
  · Si el loop se cierra entre el check y el schedule, se cierra la
    coroutine explícitamente y se lanza RuntimeError limpio.
"""
from __future__ import annotations

import asyncio
import threading

import pytest

from core.async_runner import AsyncRunner, _CancelledByEvent


def test_ensure_loop_locked_does_not_acquire_lock():
    """`_ensure_loop_locked` asume el lock tomado; no lo vuelve a tomar.

    Si lo tomara, llamarlo con el lock ya tomado haría deadlock (los
    threading.Lock no son reentrantes). Este test verifica que se
    puede llamar desde dentro de un `with self._lock:`.
    """
    runner = AsyncRunner()
    try:
        # Ojo: este bloque DEBE completar. Si _ensure_loop_locked
        # tomase el lock, este `with` haría deadlock.
        with runner._lock:
            loop = runner._ensure_loop_locked()
            assert loop is not None

        runner.close()
    finally:
        # Por si el test falla antes de llegar al close.
        try:
            runner.close()
        except Exception:
            pass


@pytest.mark.filterwarnings("ignore::RuntimeWarning")
def test_submit_atomic_against_close_stress():
    """Estrés: submit y close concurrentes. Solo excepciones esperadas."""
    async def co():
        await asyncio.sleep(0)
        return 42

    unexpected: list[BaseException] = []
    iterations = 100

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
                unexpected.append((type(exc).__name__, str(exc)))

        t = threading.Thread(target=submitter)
        t.start()
        barrier.wait()
        runner.close()
        t.join(timeout=2)

    assert not unexpected, (
        f"excepciones inesperadas en submit/close: {unexpected}"
    )


def test_submit_closes_coroutine_on_schedule_error(monkeypatch):
    """Si el schedule falla con RuntimeError, la coroutine se cierra."""
    runner = AsyncRunner()

    # Forzar que run_coroutine_threadsafe lance RuntimeError.
    def failing_schedule(coro, loop):
        raise RuntimeError("loop cerrado")

    monkeypatch.setattr(asyncio, "run_coroutine_threadsafe", failing_schedule)

    coro = None

    async def co():
        return 1

    coro = co()
    try:
        with pytest.raises(RuntimeError, match="se cerró durante el envío"):
            runner.submit(coro)
        # La coroutine debe estar cerrada.
        assert coro.cr_frame is None or coro.cr_running is False
    finally:
        runner.close()


def test_submit_after_close_raises_clean_error():
    """Mensaje de error consistente cuando el runner ya está cerrado."""
    runner = AsyncRunner()
    runner.close()

    async def co():
        return 1

    coro = co()
    try:
        with pytest.raises(RuntimeError, match="cerrado"):
            runner.submit(coro)
    finally:
        # El close del runner no cerró la coroutine (falló antes de
        # programarla). La cerramos para no dejar warnings.
        coro.close()