from pathlib import Path
import os
import sys

import pytest

ROOT = Path(__file__).resolve().parent.parent
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))


@pytest.fixture(autouse=True)
def _cleanup_async_runners(request):
    """Fuerza la recolección de objetos tras cada test.

    Cada OllamaClient() crea un AsyncRunner con su propio hilo + loop.
    Los tests no llaman shutdown() consistentemente, así que los hilos
    se acumulan. Con pytest-qt llamando processEvents() entre tests,
    esa acumulación provoca SIGBUS en macOS + PySide6.

    Con el __del__ de AsyncRunner + gc.collect() aquí, los runners
    residuales se cierran antes del siguiente test.
    """
    yield
    nodeid = request.node.nodeid
    if any(k in nodeid for k in ("ollama", "chat_controller", "regenerate")):
        import gc
        gc.collect()


@pytest.fixture(scope="session")
def qapp():
    """QApplication offscreen compartida por todos los tests de UI.

    Si PySide6 no está instalado, se omite el test en lugar de fallar.
    """
    pytest.importorskip("PySide6")
    os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app
    app.processEvents()


# ── Helper compartido para mockear httpx.AsyncClient ──────────────────
#
# El cliente Ollama usa httpx.AsyncClient + aiter_bytes() para poder
# cancelar limpiamente. Los tests que antes mockeaban httpx.stream
# (sync) ahora necesitan mockear la API async.
#
# Este helper construye un mock de AsyncClient que simula el stream
# de NDJSON como Ollama lo envía.


@pytest.fixture
def async_stream_mock(monkeypatch):
    """Devuelve una función `set_stream(lines)` para configurar el mock.

    `lines` es una lista de strings, cada uno una línea NDJSON completa
    (sin salto de línea). El mock los envía todos juntos por aiter_bytes
    en chunks de 1024 bytes.

    Uso:
        def test_x(async_stream_mock):
            async_stream_mock([
                '{"message": {"content": "hola"}, "done": false}',
                '{"message": {}, "done": true}',
            ])
            ...
    """
    import httpx

    state: dict = {"lines": [], "raise_exc": None}

    class _FakeStreamResponse:
        def __init__(self, lines):
            self._lines = lines

        def raise_for_status(self):
            if state["raise_exc"] is not None:
                raise state["raise_exc"]
            return None

        async def aiter_bytes(self, chunk_size=1024):
            for line in self._lines:
                yield (line + "\n").encode("utf-8")

    class _FakeStreamContext:
        def __init__(self, lines):
            self._lines = lines

        async def __aenter__(self):
            return _FakeStreamResponse(self._lines)

        async def __aexit__(self, *args):
            return False

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs):
            pass

        async def __aenter__(self):
            return self

        async def __aexit__(self, *args):
            return False

        def stream(self, method, url, **kwargs):
            return _FakeStreamContext(state["lines"])

    def _set_stream(lines, raise_exc=None):
        state["lines"] = lines
        state["raise_exc"] = raise_exc

    monkeypatch.setattr(httpx, "AsyncClient", _FakeAsyncClient)
    return _set_stream
