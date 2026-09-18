"""Ejecutor de corrutinas en un event loop dedicado.

Motivación: httpx sync (`httpx.stream`) no se puede interrumpir desde
otro hilo sin segfault. La cancelación cooperativa entre chunks deja
al usuario esperando hasta 10 segundos a que el modelo decida enviar
algo. httpx.AsyncClient sí es cancelable: `future.cancel()` interrumpe
el `await` en el próximo checkpoint, sin tocar el socket desde fuera.

Este runner encapsula un event loop en un hilo dedicado y ofrece un
método `submit()` síncrono que espera el resultado. Es el mismo
patrón que usa `plugins/mcp/client.py` para el transporte MCP.

Cancelación: se usa un hilo watcher que bloquea en cancel_event.wait()
(coste CPU ≈ 0 mientras espera) y dispara future.cancel() en el loop
al activarse. Antes se hacía polling cada 50 ms desde el hilo llamante;
ahora el hilo llamante bloquea en future.result() sin polling.
"""
from __future__ import annotations

import asyncio
import concurrent.futures
import logging
import threading
from typing import Any, Callable, Coroutine


logger = logging.getLogger(__name__)


# Intervalo de comprobación del hilo watcher. Solo importa cuando el
# cancel_event no se activa y la corrutina termina por su cuenta: en
# ese caso, el watcher despierta, ve que ya no hay nada que hacer y
# termina. Con 100 ms es más que suficiente.
_WATCHER_CHECK_INTERVAL_SECONDS = 0.1


class AsyncRunner:
    """Ejecuta corrutinas en un event loop dedicado en otro hilo."""

    def __init__(self, name: str = "AsyncRunner"):
        self.name = name
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._lock = threading.Lock()
        self._closed = False
        # Callback asíncrono opcional que se ejecuta antes de parar el
        # loop. Sirve para cerrar el AsyncClient persistente que vive
        # dentro del loop (httpx.AsyncClient está atado a su loop).
        self._close_callback: "Callable[[], Coroutine[Any, Any, None]] | None" = None

    def set_close_callback(
        self, callback: "Callable[[], Coroutine[Any, Any, None]]"
    ) -> None:
        """Registra una corrutina que se ejecutará al cerrar el runner.

        Se llama ANTES de parar el event loop, dentro del propio loop.
        Es el sitio correcto para cerrar recursos atados al loop
        (por ejemplo, httpx.AsyncClient).
        """
        self._close_callback = callback

    # -- ciclo de vida -------------------------------------------------------

    def _ensure_loop(self) -> None:
        """Arranca el hilo del event loop si no está corriendo."""
        with self._lock:
            if self._closed:
                raise RuntimeError(f"{self.name} ya está cerrado.")
            if (
                self._loop is not None
                and self._thread is not None
                and self._thread.is_alive()
            ):
                return
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop,
                name=self.name,
                daemon=True,
            )
            self._thread.start()

    def _run_loop(self) -> None:
        assert self._loop is not None
        asyncio.set_event_loop(self._loop)
        try:
            self._loop.run_forever()
        finally:
            pending = asyncio.all_tasks(self._loop)
            for task in pending:
                task.cancel()
            if pending:
                self._loop.run_until_complete(
                    asyncio.gather(*pending, return_exceptions=True)
                )
            self._loop.close()

    def close(self) -> None:
        with self._lock:
            if self._closed:
                return
            self._closed = True
            loop = self._loop
            thread = self._thread
            close_cb = self._close_callback

        # 1. Ejecutar el callback de cierre dentro del loop (antes de
        #    pararlo). Es donde se cierra el AsyncClient persistente.
        if loop is not None and close_cb is not None and not loop.is_closed():
            try:
                future = asyncio.run_coroutine_threadsafe(close_cb(), loop)
                future.result(timeout=3)
            except Exception as exc:
                logger.warning("Error cerrando recursos del runner: %s", exc)

        # 2. Parar el loop y esperar al hilo.
        if loop is not None:
            try:
                loop.call_soon_threadsafe(loop.stop)
            except RuntimeError:
                pass
        if (
            thread is not None
            and thread.is_alive()
            and thread is not threading.current_thread()
        ):
            thread.join(timeout=3)
        self._loop = None
        self._thread = None

    # -- ejecución -----------------------------------------------------------

    def submit(
        self,
        coro: Coroutine[Any, Any, Any],
        *,
        timeout: float | None = None,
        cancel_event: threading.Event | None = None,
    ) -> Any:
        """Ejecuta la corrutina y espera el resultado.

        Si `cancel_event` se activa, un hilo watcher cancela la
        corrutina en el event loop. La cancelación es inmediata
        (no hay polling desde el hilo llamante).

        Si `timeout` se agota, también cancela y lanza TimeoutError.
        """
        self._ensure_loop()
        assert self._loop is not None

        future = asyncio.run_coroutine_threadsafe(coro, self._loop)
        done_signal = threading.Event()

        watcher: threading.Thread | None = None
        if cancel_event is not None:
            watcher = threading.Thread(
                target=self._watch_for_cancel,
                args=(future, done_signal, cancel_event),
                name=f"{self.name}-watch",
                daemon=True,
            )
            watcher.start()

        try:
            if timeout is None:
                return future.result()
            return future.result(timeout=timeout)
        except concurrent.futures.CancelledError as exc:
            raise _CancelledByEvent() from exc
        except concurrent.futures.TimeoutError as exc:
            future.cancel()
            raise TimeoutError(
                f"{self.name} no terminó en {timeout}s"
            ) from exc
        finally:
            # Despertar al watcher para que no siga esperando si el
            # future ya terminó y el cancel_event nunca se activará.
            done_signal.set()

    def _watch_for_cancel(
        self,
        future: concurrent.futures.Future,
        done_signal: threading.Event,
        cancel_event: threading.Event,
    ) -> None:
        """Hilo watcher: cancela el future si el cancel_event se activa.

        Bloquea en cancel_event.wait() (coste CPU ≈ 0) en intervalos
        de _WATCHER_CHECK_INTERVAL_SECONDS para poder salir cuando la
        corrutina termina por su cuenta.
        """
        assert self._loop is not None
        while not done_signal.is_set():
            if cancel_event.wait(timeout=_WATCHER_CHECK_INTERVAL_SECONDS):
                if not future.done():
                    self._loop.call_soon_threadsafe(future.cancel)
                return


class _CancelledByEvent(Exception):
    """Cancelación solicitada por el usuario (via cancel_event)."""
    pass


def is_cancelled_error(exc: BaseException) -> bool:
    """True si la excepción es una cancelación de asyncio."""
    return isinstance(exc, (asyncio.CancelledError, _CancelledByEvent))
