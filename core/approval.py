"""Politica de auto-aprobacion de tools (fuente unica).

Reglas heredadas de ChatWorker._is_auto_approved (Runs #2 y #5
de OVERPAPER) y del Bloque E (allowlist del shell):

  - `borrar_archivo` NUNCA se auto-aprueba, ni con autopilot.
  - `ejecutar_comando` solo con auto_approve Y auto_approve_shell,
    y solo si el comando pasa la allowlist. Sin allowlist
    inyectada se degrada a confirmacion (fail-safe, spec §0.6).
  - Con `command` invalido (no str / vacio) se deja pasar para
    que la tool lo rechace con un mensaje accionable en vez de
    abrir un dialogo por cada intento malformado (Run #2).
  - Resto: auto-aprobado si auto_approve.

Solo se consulta cuando la tool requiere confirmacion. `core/`
no importa `plugins/`: la allowlist llega inyectada.

Auditoria externa P2#5 (HEAD 75a2240).
"""
from __future__ import annotations

from collections.abc import Callable
from typing import Any

NEVER_AUTO_APPROVE: frozenset[str] = frozenset({"borrar_archivo"})
SHELL_TOOL = "ejecutar_comando"

# Timeout del dialogo de confirmacion. Sin limite, un cierre de
# ventana dejaba el worker colgado. Vive aqui (no en ui/workers)
# para desacoplar UI y harness: ambos lo importan de la misma
# fuente unica.
CONFIRMATION_TIMEOUT_SECONDS = 600  # 10 minutos


def is_auto_approved(
    name: str,
    arguments: dict[str, Any] | Any,
    *,
    auto_approve: bool,
    auto_approve_shell: bool = False,
    command_allowed: Callable[[str], bool] | None = None,
) -> bool:
    """True si la tool se ejecuta sin dialogo de confirmacion.

    Solo se llama cuando la tool requiere confirmacion.
    """
    if name in NEVER_AUTO_APPROVE or not auto_approve:
        return False
    if name != SHELL_TOOL:
        return True
    if not auto_approve_shell:
        return False
    cmd = (
        arguments.get("command", "")
        if isinstance(arguments, dict)
        else ""
    )
    if not isinstance(cmd, str) or not cmd.strip():
        # Comando malformado: dejar que la tool lo rechace con
        # mensaje accionable. Run #2.
        return True
    if command_allowed is None:
        return False
    try:
        return bool(command_allowed(cmd))
    except Exception:  # noqa: BLE001
        return False
