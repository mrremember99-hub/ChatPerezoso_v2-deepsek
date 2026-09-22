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
