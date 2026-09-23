from __future__ import annotations

import os
import threading
import time

from PySide6.QtWidgets import QApplication

from ui.controllers.app_controller import AppController
from ui.theme import DARK_STYLE
from ui.views.main_window import MainWindow


# Segundos que esperamos a que el shutdown termine limpiamente antes
# de forzar la salida. Si un worker está bloqueado en httpx.read() y
# el modelo no envía datos, no puede responder al cancel_event, y
# esperar indefinidamente deja la app colgada.
SHUTDOWN_GRACE_SECONDS = 15.0


def _force_exit_after(exit_code: int, timeout: float) -> None:
    """Fuerza la salida del proceso tras un timeout.

    Se lanza en un hilo daemon desde main(). Si el shutdown limpio
    termina antes, el hilo nunca hace nada (el proceso ya salió). Si
    el shutdown se cuelga, el hilo llama a os._exit() que termina el
    proceso sin más contemplaciones. Es feo pero garantiza que la app
    cierra.
    """
    time.sleep(timeout)
    os._exit(exit_code)


def main() -> int:
    app = QApplication([])
    app.setStyleSheet(DARK_STYLE)

    view = MainWindow()
    controller = AppController(view)
    view.show()

    exit_code = app.exec()

    # Watchdog: si el shutdown tarda más de SHUTDOWN_GRACE_SECONDS,
    # forzamos la salida. Evita el cuelgue cuando un worker está
    # bloqueado en I/O y no responde al cancel_event.
    threading.Thread(
        target=_force_exit_after,
        args=(exit_code, SHUTDOWN_GRACE_SECONDS),
        daemon=True,
    ).start()

    controller.shutdown()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
