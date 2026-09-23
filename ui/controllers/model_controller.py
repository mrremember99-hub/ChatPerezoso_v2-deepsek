from __future__ import annotations

from PySide6.QtCore import QObject, QThread, Signal

from core.ollama import OllamaClient
from core.shutdown import remaining
from ..workers import ModelWorker


class ModelController(QObject):
    loaded = Signal(list)
    error = Signal(str)
    loading = Signal()

    def __init__(self, parent: QObject, client: OllamaClient):
        super().__init__(parent)
        self.client = client
        self._thread: QThread | None = None
        self._worker: ModelWorker | None = None

    def load(self) -> None:
        if self._thread is not None:
            return
        self.loading.emit()
        self._thread = QThread(self)
        self._worker = ModelWorker(self.client)
        self._worker.moveToThread(self._thread)
        self._thread.started.connect(self._worker.run)
        self._worker.finished.connect(self.loaded)
        self._worker.error.connect(self.error)
        self._worker.finished.connect(self._thread.quit)
        self._worker.error.connect(self._thread.quit)
        self._thread.finished.connect(self._cleanup)
        self._thread.start()

    def _cleanup(self) -> None:
        if self._worker is not None:
            self._worker.deleteLater()
        if self._thread is not None:
            self._thread.deleteLater()
        self._worker = None
        self._thread = None

    def shutdown(self, deadline: float | None = None) -> bool:
        """Cierra el hilo del ModelWorker con deadline repartido."""
        if self._thread is None or not self._thread.isRunning():
            return True
        self._thread.quit()
        wait_budget = remaining(deadline, default=2.0)
        if wait_budget <= 0:
            return False
        return self._thread.wait(int(wait_budget * 1000))
