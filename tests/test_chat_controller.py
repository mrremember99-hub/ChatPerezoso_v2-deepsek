"""Tests de ChatController sin arrancar hilos reales ni Ollama.

El controlador delega la orquestación a ``_spawn_worker``, que los tests
sustituyen por un stub. Así se cubre la lógica del controlador (historial,
señales, reacción a eventos) sin depender del transporte.
"""
from __future__ import annotations

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject

from core.tool_result import ToolResult
from ui.controllers.chat_controller import ChatController, MIN_TURNS_TO_KEEP


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
    """Renderer que solo acumula lo que recibe, sin tocar Qt.

    Mantiene las listas antiguas (tool_events, tool_results) por
    compatibilidad con tests escritos antes del rediseño. Los métodos
    nuevos (insert_tool_card, insert_narration) las siguen poblando.
    """

    def __init__(self):
        self.response_text = ""
        self.response_start: int | None = None
        self.user_messages: list[str] = []
        self.tool_events: list[tuple[str, str]] = []   # (name, status)
        self.tool_results: list[str] = []
        self.tool_cards: list = []
        self.narrations: list[tuple[str, bool]] = []
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

    def insert_narration(self, text: str, active: bool = False):
        self.narrations.append((text, active))

    def insert_tool_card(self, result):
        # Espeja el comportamiento del controlador antiguo: construye
        # labels ("Error en X", "Operación cancelada", "Resultado: X") para
        # no romper tests escritos antes del rediseño.
        self.tool_cards.append(result)
        if result.status == "error":
            label = f"Error en {result.tool_name}"
            color = "#E0A0A0"
        elif result.status == "cancelled":
            label = "Operación cancelada"
            color = "#E0BC7A"
        else:
            label = f"Resultado: {result.tool_name}"
            color = "#7C8F87"
        self.tool_events.append((label, color))
        if result.detail:
            preview = result.detail
            if len(preview) > 1200:
                preview = preview[:1200] + "\n…"
            self.tool_results.append(preview)

    def insert_error(self, message: str):
        self.errors.append(message)

    def insert_queue_list(self, prompts):
        self.queue_lists = getattr(self, "queue_lists", [])
        self.queue_lists.append(list(prompts))

    def update_queue_list(self, current: int, status: str):
        self.queue_updates = getattr(self, "queue_updates", [])
        self.queue_updates.append((current, status))

    def final_text(self, fallback: str) -> str:
        self.final_calls.append(fallback)
        return self.response_text or fallback

    def restore_assistant_message(self, text: str):
        self.response_text += text

    def remove_from_last_user(self):
        pass


class FakeTools:
    def definitions(self):
        return []

    def intent_rules(self):
        # Requerido por el Protocol ToolProvider. Vacío: los tests no
        # ejercitan la heurística de intención a través del worker.
        return {}

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


# -- historial: compactacion por tokens --------------------------------------

def test_compact_uses_default_when_context_unknown(controller):
    """Sin context_limit, se asume 4096 tokens (default de Ollama)."""
    ctrl, _ = controller
    # 4 mensajes x 100 chars = 400 chars = 100 tokens, muy por debajo
    # del 70% de 4096.
    ctrl.messages = [
        {"role": "user", "content": "x" * 100},
        {"role": "assistant", "content": "y" * 100},
        {"role": "user", "content": "x" * 100},
        {"role": "assistant", "content": "y" * 100},
    ]
    ctrl._compact_if_needed()
    assert len(ctrl.messages) == 4


def test_compact_triggers_near_context_limit(controller):
    """Con context_limit bajo, se compacta hasta que el historial cabe.

    El parche X cambió el contrato: ya no se garantiza min_turns si
    eso viola el presupuesto. Se garantiza que el resultado cabe.
    """
    ctrl, _ = controller
    ctrl.set_context_limit(1000)
    # 12 turnos con 500 chars cada uno = 12000 chars ≈ 2857 tokens.
    # Budget efectivo: 1000 - reserva (500) = 500 tokens.
    # 2857 > 500 → se compacta hasta caber.
    ctrl.messages = []
    for i in range(12):
        ctrl.messages.append({"role": "user", "content": "x" * 500})
        ctrl.messages.append({"role": "assistant", "content": "y" * 500})
    ctrl._compact_if_needed()
    assert len(ctrl.messages) < 24
    # El historial podado debe ser significativamente menor.
    assert len(ctrl.messages) <= 6
    # Y no puede quedar vacío: al menos el último user se preserva.
    assert len(ctrl.messages) >= 1
    assert ctrl.messages[0]["role"] == "user"


def test_compact_respects_agent_num_ctx(controller):
    """Si el agente fija num_ctx, se usa el menor entre ese y el del modelo."""
    ctrl, _ = controller
    ctrl.set_context_limit(131072)
    ctrl.set_current_options({"num_ctx": 2048})
    ctrl.messages = []
    for i in range(20):
        ctrl.messages.append({"role": "user", "content": "x" * 400})
        ctrl.messages.append({"role": "assistant", "content": "y" * 400})
    # 40 mensajes x 400 chars = 16000 chars = 4000 tokens
    # 70% de 2048 = 1433 -> se compacta
    ctrl._compact_if_needed()
    assert len(ctrl.messages) < 40


def test_compact_preserves_last_user_when_nothing_fits(controller):
    """Con contexto mínimo, el chat preserva al menos el último user.

    Antes de los parches X+Y, se devolvía el historial completo aunque
    excediera el presupuesto. Ahora se poda hasta que quepa, con un
    suelo: nunca dejar el chat sin prompt.
    """
    ctrl, _ = controller
    ctrl.set_context_limit(100)
    ctrl.messages = [
        {"role": "user", "content": "x" * 1000},
        {"role": "assistant", "content": "y" * 1000},
    ]
    ctrl._compact_if_needed()
    # El último user se preserva aunque exceda el budget.
    assert len(ctrl.messages) == 1
    assert ctrl.messages[0]["role"] == "user"


def test_compact_preserves_recent_messages(controller):
    """El contenido mas reciente nunca se pierde."""
    ctrl, _ = controller
    ctrl.set_context_limit(500)
    ctrl.messages = []
    for i in range(20):
        ctrl.messages.append({"role": "user", "content": f"pregunta {i}" * 20})
        ctrl.messages.append({"role": "assistant", "content": f"respuesta {i}" * 20})
    ctrl._compact_if_needed()
    if ctrl.messages:
        assert "respuesta 19" in ctrl.messages[-1]["content"]


def test_set_context_limit_accepts_zero(controller):
    """set_context_limit(0) significa desconocido, usa el default."""
    ctrl, _ = controller
    ctrl.set_context_limit(0)
    assert ctrl._context_limit == 0


def test_set_context_limit_ignores_negative(controller):
    """Valores negativos se clampean a 0."""
    ctrl, _ = controller
    ctrl.set_context_limit(-100)
    assert ctrl._context_limit == 0


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
    ctrl._on_tool_result(ToolResult(tool_name="leer_archivo", summary="ERROR: no existe", detail="ERROR: no existe", is_error=True))
    label, _ = renderer.tool_events[-1]
    assert "Error" in label


def test_on_tool_event_colors_cancelled_prefix(controller):
    ctrl, renderer = controller
    ctrl._on_tool_result(ToolResult(tool_name="borrar_archivo", summary="Operación cancelada por el usuario.", is_cancelled=True))
    label, _ = renderer.tool_events[-1]
    assert "cancelada" in label.lower()


def test_on_tool_event_regular_result(controller):
    ctrl, renderer = controller
    ctrl._on_tool_result(ToolResult(tool_name="listar_carpeta", summary="Contenido del directorio", detail="[FILE] a.txt"))
    label, _ = renderer.tool_events[-1]
    assert "Resultado" in label


def test_on_tool_result_truncates_long_previews(controller):
    ctrl, renderer = controller
    long_result = "x" * 5000
    ctrl._on_tool_result(ToolResult(tool_name="leer_archivo", summary="archivo leído", detail=long_result, truncated=True))
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

    # La persistencia es debounced (500 ms). Con el timer activo, el
    # archivo puede no estar escrito todavia. Forzamos el flush para
    # verificar el contrato de "al cerrar/forzar, se persiste".
    ctrl._persist_now()

    history = store.load()
    assert history is not None
    assert [m["role"] for m in history.messages] == ["user", "assistant"]
    assert history.model == "modelo"


def test_persist_is_debounced_not_immediate(tmp_path, qapp, monkeypatch):
    """Verifica que la persistencia NO ocurre inmediatamente tras cada
    mensaje, sino que se difiere 500 ms con el timer."""
    from core.history import HistoryStore

    renderer = FakeRenderer()
    owner = QObject()
    store = HistoryStore(tmp_path / "h.json")
    ctrl = ChatController(
        parent=owner,
        parent_widget=None,
        client=object(),
        tools=FakeTools(),
        renderer=renderer,
        store=store,
        initial_messages=[],
    )
    ctrl._owner = owner

    ctrl._append_message({"role": "user", "content": "hola"})

    # El archivo NO debe existir todavía: la persistencia es diferida.
    assert not (tmp_path / "h.json").exists()
    # Y el timer debe estar activo.
    assert ctrl._persist_timer.isActive()


def test_persist_now_forces_immediate_write(tmp_path, qapp, monkeypatch):
    """Forzar con _persist_now escribe a disco inmediatamente."""
    from core.history import HistoryStore

    renderer = FakeRenderer()
    owner = QObject()
    store = HistoryStore(tmp_path / "h.json")
    ctrl = ChatController(
        parent=owner,
        parent_widget=None,
        client=object(),
        tools=FakeTools(),
        renderer=renderer,
        store=store,
        initial_messages=[],
    )
    ctrl._owner = owner

    ctrl._append_message({"role": "user", "content": "hola"})
    ctrl._persist_now()

    history = store.load()
    assert history is not None
    assert len(history.messages) == 1
    assert history.messages[0]["content"] == "hola"


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
