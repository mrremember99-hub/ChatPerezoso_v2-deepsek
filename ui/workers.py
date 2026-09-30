"""Workers que corren en hilos aparte."""
from __future__ import annotations

from typing import Any

from PySide6.QtCore import QObject, Signal

from core.ollama import OllamaClient, OllamaError
from plugins.mcp import MCPError


class ModelWorker(QObject):
    finished = Signal(list)
    error = Signal(str)

    def __init__(self, client: OllamaClient):
        super().__init__()
        self.client = client

    def run(self) -> None:
        try:
            self.finished.emit(self.client.list_models())
        except OllamaError as exc:
            self.error.emit(str(exc))


class CapabilitiesWorker(QObject):
    """Consulta /api/show para saber si el modelo soporta tools.

    Se ejecuta en un hilo aparte porque la consulta implica una
    peticion HTTP que puede tardar hasta 5s si Ollama esta ocupado.

    El `generation` permite al llamante descartar resultados
    obsoletos: si el usuario cambia de modelo varias veces, solo la
    última consulta debe actualizar la UI. El worker lo emite en
    ambas señales para que el receptor pueda comparar.
    """

    finished = Signal(str, object, int)  # model_name, caps, generation
    error = Signal(str, str, int)        # model_name, mensaje, generation

    def __init__(self, host: str, model: str, generation: int = 0):
        super().__init__()
        self.host = host
        self.model = model
        self.generation = generation

    def run(self) -> None:
        from core.model_capabilities import get_capabilities
        try:
            caps = get_capabilities(self.host, self.model)
            self.finished.emit(self.model, caps, self.generation)
        except Exception as exc:
            self.error.emit(self.model, str(exc), self.generation)


class MCPWorker(QObject):
    finished = Signal(str, object, list)
    error = Signal(str, str)

    def __init__(self, server_id: str, client: Any):
        super().__init__()
        self.server_id = server_id
        self.client = client

    def run(self) -> None:
        try:
            tools = self.client.list_tools()
            self.finished.emit(self.server_id, self.client, tools)
        except MCPError as exc:
            self._cleanup_client()
            self.error.emit(self.server_id, str(exc))
        except BaseException as exc:
            # Cualquier otra excepcion tambien debe llegar al
            # controller. Si no, el bridge queda con estado
            # inconsistente: el worker nunca emite signal, el
            # controller cree que sigue conectado, y la UI muestra
            # el servidor como activo mientras las llamadas fallan.
            self._cleanup_client()
            self.error.emit(
                self.server_id,
                f"{type(exc).__name__}: {exc}",
            )

    def _cleanup_client(self) -> None:
        """X1.5b (auditoria externa 2026-09-29, P2#3): cerrar el
        cliente MCP si el worker falla. Sin esto, el hilo del loop
        asyncio y el subprocess MCP sobreviven al QThread.
        Idempotente y silencioso: no debe tapar el error original."""
        try:
            close = getattr(self.client, "close", None)
            if callable(close):
                close()
        except Exception:
            pass
