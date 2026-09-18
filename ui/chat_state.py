"""Estados del ciclo de vida del chat.

Antes de este módulo, el estado del chat vivía repartido entre varios
flags booleanos: `ChatController._streaming`, `DiagnosticsController._in_flight`,
y el estado implícito de `_thread` / `_worker` (None o no None). Eso hacía
que preguntas como "¿está el chat esperando una confirmación del usuario?"
no tuvieran una respuesta clara.

Con `ChatState`, hay una única fuente de verdad. Los consumidores
(`ChatPanel`, `DiagnosticsController`, `AppController`) leen el estado
en lugar de inferirlo de flags dispersos.

Estados actuales:
  · IDLE       — en reposo, listo para recibir un mensaje.
  · STREAMING  — generando respuesta (incluye ejecución de tools).
  · CANCELLING — el usuario pulsó Detener, esperando a que el worker
                 termine de cancelar.
  · ERROR      — el último turno falló. Se vuelve a IDLE tras limpiar.

Estados futuros (documentados, no implementados):
  · WAITING_CONFIRMATION — el worker espera confirmación de una
    operación destructiva. Hoy eso vive dentro del ChatWorker y no
    afecta al estado del controlador.
  · EXECUTING_TOOL — el worker está ejecutando una tool. Mismo caso
    que el anterior.

No se añaden hasta que la UI necesite distinguirlos.
"""
from __future__ import annotations

from enum import Enum, auto


class ChatState(Enum):
    IDLE = auto()
    STREAMING = auto()
    CANCELLING = auto()
    ERROR = auto()

    @property
    def is_active(self) -> bool:
        """True si el chat está haciendo trabajo (streaming o cancelando)."""
        return self in (ChatState.STREAMING, ChatState.CANCELLING)
