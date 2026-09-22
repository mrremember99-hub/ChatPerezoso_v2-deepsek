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

    def __del__(self) -> None:
        """Cierra el hilo del loop al recolectar el objeto.

        Se ejecuta cuando el AsyncRunner pierde todas sus referencias
        (por ejemplo, cuando un OllamaClient de un test se recoge).
        Sin esto, el hilo del loop queda vivo hasta que muere el
        intérprete, y acumular hilos vivos entre tests provoca SIGBUS
        con pytest-qt + PySide6 en macOS.

        Si hay un close_callback registrado (por ejemplo, para cerrar
        el httpx.AsyncClient persistente), se programa su ejecución
        antes de parar el loop. Se hace en un hilo daemon para no
        bloquear al GC: un __del__ que espera I/O puede colgar el
        recolector y provocar deadlocks sutiles.
        """
        try:
            if getattr(self, "_closed", True):
                return
            self._closed = True
            loop = getattr(self, "_loop", None)
            close_cb = getattr(self, "_close_callback", None)
            if loop is None or loop.is_closed():
                return

            if close_cb is None or not loop.is_running():
                # Sin callback o loop ya parado: solo pedir stop.
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:
                    pass
                return

            def _cleanup() -> None:
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        close_cb(), loop
                    )
                    try:
                        future.result(timeout=1.0)
                    except Exception:
                        pass
                finally:
                    try:
                        loop.call_soon_threadsafe(loop.stop)
                    except RuntimeError:
                        pass

            try:
                threading.Thread(target=_cleanup, daemon=True).start()
            except BaseException:
                # Si no podemos arrancar el hilo (shutdown del
                # intérprete), al menos paramos el loop.
                try:
                    loop.call_soon_threadsafe(loop.stop)
                except RuntimeError:
                    pass
        except BaseException:
            # Un __del__ que lanza es peor que un __del__ que falla.
            pass

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

    def _ensure_loop(self) -> asyncio.AbstractEventLoop:
        """Arranca el hilo del event loop si no está corriendo.

        Devuelve la referencia al loop vivo. El llamante debe usar el
        valor devuelto en lugar de leer `self._loop` después: `close()`
        puede poner el atributo a None desde otro hilo, y leerlo fuera
        del lock provoca una carrera.
        """
        with self._lock:
            if self._closed:
                raise RuntimeError(f"{self.name} ya está cerrado.")
            if (
                self._loop is not None
                and self._thread is not None
                and self._thread.is_alive()
            ):
                return self._loop
            self._loop = asyncio.new_event_loop()
            self._thread = threading.Thread(
                target=self._run_loop,
                name=self.name,
                daemon=True,
            )
            self._thread.start()
            return self._loop

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
        # `_ensure_loop` devuelve el loop y lo captura dentro del lock.
        # No leer `self._loop` después: `close()` puede ponerlo a None
        # desde otro hilo y provocar una carrera.
        loop = self._ensure_loop()

        future = asyncio.run_coroutine_threadsafe(coro, loop)
        done_signal = threading.Event()

        watcher: threading.Thread | None = None
        if cancel_event is not None:
            watcher = threading.Thread(
                target=self._watch_for_cancel,
                args=(future, done_signal, cancel_event, loop),
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
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Hilo watcher: cancela el future si el cancel_event se activa.

        `loop` se captura al crear el watcher y se pasa como argumento
        para evitar leer `self._loop`, que `close()` puede poner a None
        mientras este hilo sigue vivo. Sin esto, existe una carrera
        entre el cierre del runner y el watcher.
        """
        while not done_signal.is_set():
            if cancel_event.wait(timeout=_WATCHER_CHECK_INTERVAL_SECONDS):
                if not future.done():
                    try:
                        loop.call_soon_threadsafe(future.cancel)
                    except RuntimeError:
                        # El loop se cerró entre el wait y el call.
                        # No hay nada que cancelar.
                        pass
                return


class _CancelledByEvent(Exception):
    """Cancelación solicitada por el usuario (via cancel_event)."""
    pass


def is_cancelled_error(exc: BaseException) -> bool:
    """True si la excepción es una cancelación de asyncio."""
    return isinstance(exc, (asyncio.CancelledError, _CancelledByEvent))
