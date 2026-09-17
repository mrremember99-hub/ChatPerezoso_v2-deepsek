"""Tests de ChatController sin arrancar hilos reales ni Ollama.

El controlador delega la orquestación a ``_spawn_worker``, que los tests
sustituyen por un stub. Así se cubre la lógica del controlador (historial,
señales, reacción a eventos) sin depender del transporte.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject

from ui.controllers.chat_controller import ChatController, MAX_HISTORY_MESSAGES


# -- dobles de prueba --------------------------------------------------------

class _FakeSignal:
    """Sustituto minimalista de una señal Qt para los stubs."""

    def __init__(self):
        self._callbacks = []

    def connect(self, callback):
        self._callbacks.append(callback)

    def emit(self, *args):
        for callback in list(self._callbacks):
            callback(*args)


class FakeRenderer:
    """Renderer que solo acumula lo que recibe, sin tocar Qt."""

    def __init__(self):
        self.response_text = ""
        self.response_start: int | None = None
        self.user_messages: list[str] = []
        self.tool_events: list[tuple[str, str]] = []
        self.tool_results: list[str] = []
        self.errors: list[str] = []
        self.final_calls: list[str] = []

    def reset(self):
        self.response_text = ""
        self.response_start = None

    def reset_response_segment(self):
        self.response_start = None

    def insert_user_message(self, text: str):
        self.user_messages.append(text)

    def on_text(self, text: str):
        self.response_text += text

    def insert_tool_event(self, text: str, color: str):
        self.tool_events.append((text, color))

    def insert_tool_result(self, text: str):
        self.tool_results.append(text)

    def insert_error(self, message: str):
        self.errors.append(message)

    def final_text(self, fallback: str) -> str:
        self.final_calls.append(fallback)
        return self.response_text or fallback


class FakeTools:
    def definitions(self):
        return []

    def call(self, name, arguments, *, allow_destructive=False, cancel_event=None):
        return "ok"

    def requires_confirmation(self, name):
        return False


class FakeWorker(QObject):
    def __init__(self, parent=None):
        super().__init__(parent)
        self.cancelled = False

    def cancel(self):
        self.cancelled = True


class FakeThread:
    """Thread stub con la superficie mínima que usa el controlador."""

    def __init__(self):
        self.started = _FakeSignal()
        self.finished = _FakeSignal()
        self.quitted = False

    def quit(self):
        self.quitted = True

    def isRunning(self):
        return False

    def wait(self, _timeout=0):
        return True

    def deleteLater(self):
        pass


# -- fixtures ----------------------------------------------------------------

@pytest.fixture
def controller(qapp, monkeypatch):
    """Controller con worker y thread sustituidos por stubs.

    El parent QObject se guarda como atributo del controlador para
    garantizar que no se recoja antes de tiempo (PySide6 puede liberar
    QObjects sin parent que no tengan referencias Python).
    """
    renderer = FakeRenderer()
    owner = QObject()
    ctrl = ChatController(
        parent=owner,
        parent_widget=None,      # los tests no muestran diálogos
        client=object(),         # nunca se usa: el worker real no se crea
        tools=FakeTools(),
        renderer=renderer,
    )
    ctrl._owner = owner           # mantiene vivo el QObject padre
    ctrl._fake_worker = FakeWorker(ctrl)
    ctrl._fake_thread = FakeThread()

    def spawn_stub(*_args, **_kwargs) -> None:
        ctrl._worker = ctrl._fake_worker
        ctrl._thread = ctrl._fake_thread

    monkeypatch.setattr(ctrl, "_spawn_worker", spawn_stub)
    return ctrl, renderer


# -- historial ---------------------------------------------------------------

def test_history_under_limit_is_kept_intact(controller):
    ctrl, _ = controller
    for i in range(MAX_HISTORY_MESSAGES):
        ctrl._append_message({"role": "user", "content": str(i)})
    assert len(ctrl.messages) == MAX_HISTORY_MESSAGES
    assert ctrl.messages[0]["content"] == "0"


def test_history_over_limit_cuts_at_first_user(controller):
    ctrl, _ = controller
    for i in range(MAX_HISTORY_MESSAGES + 5):
        role = "user" if i % 2 == 0 else "assistant"
        ctrl._append_message({"role": role, "content": str(i)})
    assert len(ctrl.messages) <= MAX_HISTORY_MESSAGES
    assert ctrl.messages[0]["role"] == "user"


def test_history_cut_does_not_leave_orphan_assistant(controller):
    ctrl, _ = controller
    for i in range(MAX_HISTORY_MESSAGES):
        role = "user" if i % 2 == 0 else "assistant"
        ctrl._append_message({"role": role, "content": str(i)})
    ctrl._append_message({"role": "assistant", "content": "final"})
    assert ctrl.messages[0]["role"] == "user"
    assert len(ctrl.messages) <= MAX_HISTORY_MESSAGES


# -- send --------------------------------------------------------------------

def test_send_ignores_empty_text(controller):
    ctrl, renderer = controller
    ctrl.send("", "modelo")
    assert not ctrl.is_streaming()
    assert ctrl.messages == []
    assert renderer.user_messages == []


def test_send_ignores_missing_model(controller):
    ctrl, renderer = controller
    ctrl.send("hola", "")
    assert not ctrl.is_streaming()
    assert ctrl.messages == []


def test_send_ignores_while_streaming(controller):
    ctrl, renderer = controller
    ctrl.send("primero", "modelo")
    assert ctrl.is_streaming()
    ctrl.send("segundo", "modelo")
    assert renderer.user_messages == ["primero"]
    assert [m["content"] for m in ctrl.messages if m["role"] == "user"] == ["primero"]


def test_send_emits_streaming_and_registers_user_message(controller):
    ctrl, renderer = controller
    events: list[bool] = []
    ctrl.streaming_changed.connect(events.append)
    ctrl.send("hola", "modelo")

    assert ctrl.is_streaming()
    assert renderer.user_messages == ["hola"]
    assert ctrl.messages == [{"role": "user", "content": "hola"}]
    assert events == [True]


# -- finalización ------------------------------------------------------------

def test_on_done_appends_assistant_and_finishes(controller):
    ctrl, renderer = controller
    events: list[bool] = []
    ctrl.streaming_changed.connect(events.append)
    statuses: list[str] = []
    ctrl.status.connect(statuses.append)

    ctrl.send("hola", "modelo")
    renderer.on_text("respuesta del modelo")
    ctrl._on_done("respuesta del modelo")

    assert not ctrl.is_streaming()
    assert events == [True, False]
    assert statuses[-1] == "Listo"
    assistant = [m for m in ctrl.messages if m["role"] == "assistant"]
    assert assistant == [{"role": "assistant", "content": "respuesta del modelo"}]
    assert renderer.final_calls == ["respuesta del modelo"]


def test_on_done_uses_fallback_when_no_streaming(controller):
    ctrl, renderer = controller
    ctrl.send("hola", "modelo")
    ctrl._on_done("respuesta directa")

    assistant = [m for m in ctrl.messages if m["role"] == "assistant"]
    assert assistant == [{"role": "assistant", "content": "respuesta directa"}]


def test_on_cancelled_finishes_with_status_cancelado(controller):
    ctrl, _ = controller
    statuses: list[str] = []
    ctrl.status.connect(statuses.append)
    ctrl.send("hola", "modelo")
    ctrl._on_cancelled()

    assert not ctrl.is_streaming()
    assert statuses[-1] == "Cancelado"
    assert all(m["role"] != "assistant" for m in ctrl.messages)


def test_on_error_inserts_error_and_finishes(controller):
    ctrl, renderer = controller
    ctrl.send("hola", "modelo")
    ctrl._on_error("algo se rompió")

    assert not ctrl.is_streaming()
    assert renderer.errors == ["algo se rompió"]


# -- eventos de herramienta --------------------------------------------------

def test_on_tool_event_colors_error_prefix(controller):
    ctrl, renderer = controller
    ctrl._on_tool_result("leer_archivo", "ERROR: no existe")
    label, _ = renderer.tool_events[-1]
    assert "Error" in label


def test_on_tool_event_colors_cancelled_prefix(controller):
    ctrl, renderer = controller
    ctrl._on_tool_result("borrar_archivo", "OPERACIÓN CANCELADA POR EL USUARIO")
    label, _ = renderer.tool_events[-1]
    assert "cancelada" in label.lower()


def test_on_tool_event_regular_result(controller):
    ctrl, renderer = controller
    ctrl._on_tool_result("listar_carpeta", "[FILE] a.txt")
    label, _ = renderer.tool_events[-1]
    assert "Resultado" in label


def test_on_tool_result_truncates_long_previews(controller):
    ctrl, renderer = controller
    long_result = "x" * 5000
    ctrl._on_tool_result("leer_archivo", long_result)
    assert renderer.tool_results
    assert len(renderer.tool_results[-1]) <= 1200 + len("\n…")


# -- cancelación y rebind ----------------------------------------------------

def test_cancel_forwards_to_worker(controller):
    ctrl, _ = controller
    ctrl.send("hola", "modelo")
    ctrl.cancel()
    assert ctrl._fake_worker.cancelled is True


def test_clear_ignores_while_streaming(controller):
    ctrl, _ = controller
    ctrl.send("hola", "modelo")
    ctrl.clear()
    assert ctrl.messages


def test_clear_empties_history_when_idle(controller):
    ctrl, _ = controller
    ctrl.send("hola", "modelo")
    ctrl._on_done("respuesta")
    ctrl.clear()
    assert ctrl.messages == []


def test_rebind_tools_replaces_provider(controller):
    ctrl, _ = controller
    new_tools = FakeTools()
    ctrl.rebind_tools(new_tools)
    assert ctrl.tools is new_tools

# -- persistencia ------------------------------------------------------------

def test_persists_after_each_turn(tmp_path, qapp, monkeypatch):
    from core.history import HistoryStore

    renderer = FakeRenderer()
    owner = QObject()
    store = HistoryStore(tmp_path / "history.json")
    ctrl = ChatController(
        parent=owner,
        parent_widget=None,
        client=object(),
        tools=FakeTools(),
        renderer=renderer,
        store=store,
        initial_messages=[],
    )
    ctrl._owner = owner  # mantiene vivo el QObject padre
    ctrl._fake_worker = FakeWorker(ctrl)
    ctrl._fake_thread = FakeThread()

    def spawn_stub(*_args, **_kwargs):
        ctrl._worker = ctrl._fake_worker
        ctrl._thread = ctrl._fake_thread

    monkeypatch.setattr(ctrl, "_spawn_worker", spawn_stub)

    ctrl.send("hola", "modelo")
    renderer.on_text("respuesta")
    ctrl._on_done("respuesta")

    history = store.load()
    assert history is not None
    assert [m["role"] for m in history.messages] == ["user", "assistant"]
    assert history.model == "modelo"


def test_loads_initial_messages(tmp_path, qapp, monkeypatch):
    from core.history import HistoryStore

    store = HistoryStore(tmp_path / "h.json")
    store.save(
        [
            {"role": "user", "content": "previo"},
            {"role": "assistant", "content": "respuesta previa"},
        ],
        model="m1",
    )
    loaded = store.load()
    assert loaded is not None

    owner = QObject()
    ctrl = ChatController(
        parent=owner,
        parent_widget=None,
        client=object(),
        tools=FakeTools(),
        renderer=FakeRenderer(),
        store=store,
        initial_messages=loaded.messages,
    )
    ctrl._owner = owner  # mantiene vivo el QObject padre
    assert len(ctrl.messages) == 2


def test_clear_removes_persisted_history(tmp_path, qapp, monkeypatch):
    from core.history import HistoryStore

    store = HistoryStore(tmp_path / "h.json")
    owner = QObject()
    ctrl = ChatController(
        parent=owner,
        parent_widget=None,
        client=object(),
        tools=FakeTools(),
        renderer=FakeRenderer(),
        store=store,
        initial_messages=[{"role": "user", "content": "x"}],
    )
    ctrl._owner = owner  # mantiene vivo el QObject padre
    store.save(ctrl.messages)
    assert store.load() is not None

    ctrl.clear()
    assert ctrl.messages == []
    assert store.load() is None
