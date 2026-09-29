from __future__ import annotations

import os
import threading
import time

from PySide6.QtWidgets import QApplication

from core.shutdown import close_auxiliary_caches
from ui.controllers.app_controller import AppController

# Switch de mascara (auditoria 2026-09-27, rediseno visual v2).
# Default v1: la app de siempre, cero cambios. v2 se activa con
# variable de entorno:
#     CHATPEREZOSO_UI=v2 python bootstrap.py
# Los paneles v2 exponen la misma interfaz que los v1
# (sidebar, chat_panel, right_panel, set_status), asi que
# AppController no necesita cambios.
_USE_V2 = os.environ.get("CHATPEREZOSO_UI", "").lower() == "v2"

if _USE_V2:
    from ui.theme_v2 import DARK_STYLE, load_font
    from ui.views.main_window_v2 import MainWindowV2 as MainWindow
else:
    from ui.theme import DARK_STYLE
    from ui.views.main_window import MainWindow

    def load_font() -> bool:  # noqa: D401
        """No-op en v1 (la fuente es la del sistema)."""
        return False


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
    # En v2, load_font registra VT323 antes del stylesheet para que
    # QSS encuentre la fuente por nombre. En v1 es no-op.
    load_font()
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
    # Caches compartidos fuera del arbol de controllers (AstIndex,
    # futuro _RAG_CACHE, etc.). Despues del controller para no
    # cerrar el indice mientras el RAG aun lo consulta.
    close_auxiliary_caches()
    return exit_code


if __name__ == "__main__":
    raise SystemExit(main())
