"""Helpers para shutdown con deadline global.

Motivacion: varios componentes tienen shutdown() con timeouts
individuales. Sin un deadline propagado, los timeouts se suman y
pueden exceder el watchdog global de main.py. `remaining()` calcula
cuanto queda del presupuesto total para repartirlo entre fases.
"""
from __future__ import annotations

import logging
import time

logger = logging.getLogger(__name__)

# Presupuesto total de shutdown. Menor que el watchdog de
# main.py (SHUTDOWN_GRACE_SECONDS), para que el watchdog solo
# entre si algo se cuelga de verdad.
SHUTDOWN_BUDGET_SECONDS: float = 12.0


def remaining(deadline: float | None, *, default: float) -> float:
    """Segundos restantes hasta `deadline`.

    Si `deadline` es None, devuelve `default`. Si ya paso, devuelve 0.
    Sirve para repartir un presupuesto total de shutdown entre fases
    sin que cada una espere su timeout individual completo.
    """
    if deadline is None:
        return default
    return max(0.0, deadline - time.monotonic())


def close_auxiliary_caches() -> None:
    """Cierra caches compartidos que no pertenecen a ningun controller.

    Se llama desde main.py DESPUES de controller.shutdown(): durante
    el cierre del controller, el RAG puede seguir consultando el
    AstIndex. Cualquier fallo se registra con log pero no propaga:
    un cache que no cierra no debe abortar el shutdown de la app.
    """
    from core import ast_index

    try:
        ast_index.close_all()
    except Exception:
        logger.exception("close_auxiliary_caches: fallo cerrando ast_index")
