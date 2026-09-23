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
import time
from typing import Any, Callable, Coroutine

from .shutdown import remaining


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
        """Version publica con lock. Para casos donde el llamante ya
        tiene el lock tomado, usar `_ensure_loop_locked`."""
        with self._lock:
            return self._ensure_loop_locked()

    def _ensure_loop_locked(self) -> asyncio.AbstractEventLoop:
        """Igual que `_ensure_loop` pero SIN tomar el lock.

        El llamante DEBE tener el lock tomado. Existe para que
        `submit` pueda agrupar obtener-loop + programar-coroutine en
        una sola seccion critica, sin dejar hueco a que `close()` se
        cuele entre las dos y deje la coroutine programada sobre un
        loop ya muerto.
        """
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

    def close(self, timeout: float | None = None) -> bool:
        """Cierra el runner.

        `timeout` es el presupuesto total para el cierre completo
        (callback + join). Se reparte entre las dos fases segun el
        tiempo restante. Si `timeout` es None, se usan los timeouts
        individuales (3s + 3s) como hasta ahora, para mantener
        compatibilidad con las llamadas existentes.

        Devuelve True si el cierre se completo limpiamente. False si
        alguna fase expiro su porcion del presupuesto: en ese caso el
        loop y el hilo se dejan como estan y el proceso terminara al
        salir.
        """
        with self._lock:
            if self._closed:
                return True
            self._closed = True
            loop = self._loop
            thread = self._thread
            close_cb = self._close_callback

        deadline = (
            time.monotonic() + timeout
            if timeout is not None
            else None
        )
        ok = True

        # 1. Callback de cierre dentro del loop (antes de pararlo).
        #    Es donde se cierra el AsyncClient persistente.
        if loop is not None and close_cb is not None and not loop.is_closed():
            cb_timeout = remaining(deadline, default=3.0)
            if cb_timeout <= 0:
                logger.warning(
                    "AsyncRunner.close: sin tiempo para close_callback"
                )
                ok = False
            else:
                try:
                    future = asyncio.run_coroutine_threadsafe(
                        close_cb(), loop
                    )
                    future.result(timeout=cb_timeout)
                except TimeoutError:
                    logger.warning(
                        "AsyncRunner.close: close_callback excedio %.2fs",
                        cb_timeout,
                    )
                    ok = False
                except Exception as exc:
                    logger.warning(
                        "Error cerrando recursos del runner: %s", exc
                    )

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
            join_timeout = remaining(deadline, default=3.0)
            if join_timeout <= 0:
                logger.warning("AsyncRunner.close: sin tiempo para join")
                ok = False
            else:
                thread.join(timeout=join_timeout)
                if thread.is_alive():
                    logger.warning(
                        "AsyncRunner.close: hilo sigue vivo tras %.2fs",
                        join_timeout,
                    )
                    ok = False
        self._loop = None
        self._thread = None
        return ok

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
        # Agrupar obtener-loop + programar-coroutine bajo el mismo
        # lock. Sin esto, entre `_ensure_loop()` y `run_coroutine_-
        # threadsafe()` otro hilo podia llamar a `close()`, parar el
        # loop, y dejar la coroutine programada sobre un loop muerto.
        with self._lock:
            loop = self._ensure_loop_locked()
            try:
                future = asyncio.run_coroutine_threadsafe(coro, loop)
            except RuntimeError as exc:
                # El loop se cerro entre el check y el schedule.
                # Cerrar la coroutine para no dejar warnings de
                # "coroutine was never awaited".
                coro.close()
                raise RuntimeError(
                    f"{self.name} se cerró durante el envío."
                ) from exc
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
