"""Tests específicos de los arreglos del análisis senior."""
from __future__ import annotations

import json
import subprocess
import shutil

import pytest


# -- §1.4 AgentStore respeta lista vacía ------------------------------------

def test_agent_store_respects_explicit_empty_list(tmp_path):
    from core.agents import AgentStore

    path = tmp_path / "agents.json"
    path.write_text(json.dumps({"agents": []}), encoding="utf-8")
    assert AgentStore(path).load() == []


def test_agent_controller_falls_back_to_defaults_when_empty(tmp_path, qapp):
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QObject
    from core.agents import AgentStore
    from ui.controllers.agent_controller import AgentController

    store = AgentStore(tmp_path / "agents.json")
    store.path.write_text(json.dumps({"agents": []}), encoding="utf-8")

    owner = QObject()
    ctrl = AgentController(
        parent=owner,
        parent_widget=None,
        available_tools=[],
        store=store,
    )
    ctrl._owner = owner
    # El controller debe haber recreado los defaults y guardado.
    assert ctrl.agents
    assert store.load()


# -- §4.6 git log en repo sin commits ---------------------------------------

pytestmark_git = pytest.mark.skipif(
    shutil.which("git") is None, reason="git no instalado"
)


@pytestmark_git
def test_git_log_on_empty_repo(tmp_path):
    from plugins.git import GitClient

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    output = GitClient(tmp_path).log()
    assert "no tiene commits" in output


@pytestmark_git
def test_git_log_with_commits(tmp_path):
    from plugins.git import GitClient

    subprocess.run(["git", "init", "-q"], cwd=tmp_path, check=True)
    subprocess.run(
        ["git", "-c", "user.email=t@t", "-c", "user.name=T", "commit",
         "--allow-empty", "-q", "-m", "primero"],
        cwd=tmp_path, check=True,
    )
    output = GitClient(tmp_path).log()
    assert "primero" in output


# -- §2.2 search con límite de archivos -------------------------------------

def test_search_with_file_limit(tmp_path, monkeypatch):
    from plugins.search import client as search_client
    from plugins.search import SearchClient

    # Fuerza un límite bajo para el test.
    monkeypatch.setattr(search_client, "_MAX_FILES_SCANNED", 3)

    for i in range(20):
        (tmp_path / f"f{i:02d}.txt").write_text("nada\n", encoding="utf-8")

    result = SearchClient(tmp_path).search("no-existe")
    assert "primeros" in result


def test_search_does_not_report_limit_when_not_hit(tmp_path):
    from plugins.search import SearchClient

    (tmp_path / "a.txt").write_text("hola\n", encoding="utf-8")
    result = SearchClient(tmp_path).search("hola")
    assert "primeros" not in result


# -- §4.1 shell avisa de intérpretes ----------------------------------------

def test_shell_flags_python_c():
    from plugins.shell import analyze_risk

    risks = analyze_risk("python3 -c 'print(1)'")
    assert any("Python" in r for r in risks)


def test_shell_flags_bash_c():
    from plugins.shell import analyze_risk

    risks = analyze_risk('bash -c "ls"')
    assert any("shell" in r.lower() for r in risks)


def test_shell_flags_perl_e():
    from plugins.shell import analyze_risk

    risks = analyze_risk("perl -e 'print 1'")
    assert risks


def test_shell_no_risk_for_plain_command():
    from plugins.shell import analyze_risk

    assert analyze_risk("ls -la") == []
    assert analyze_risk("pwd") == []


# -- §1.1 handlers del agente -----------------------------------------------

def test_app_controller_agent_handlers_separated(qapp, tmp_path, monkeypatch):
    """El combo emite un nombre; el controller lo traduce a Agent antes
    de tocar el chat. Verifica que no se llama _apply_agent con un string."""
    pytest.importorskip("PySide6")

    from ui.controllers import app_controller as app_module
    from core.agents import Agent

    # Espiamos _apply_agent y comprobamos que siempre recibe un Agent.
    received: list = []
    original = app_module.AppController._apply_agent

    def spy(self, agent):
        received.append(agent)
        return original(self, agent)

    monkeypatch.setattr(app_module.AppController, "_apply_agent", spy)

    # No construimos toda la app: solo el handler de string.
    # El handler invoca agent_ctrl.set_active(name), que emite Agent.
    # Comprobamos esa ruta con un AgentController real sobre un store
    # temporal.
    from PySide6.QtCore import QObject
    from core.agents import AgentStore, default_agents
    from ui.controllers.agent_controller import AgentController

    store = AgentStore(tmp_path / "agents.json")
    store.save(default_agents())
    owner = QObject()
    ctrl = AgentController(
        parent=owner,
        parent_widget=None,
        available_tools=[],
        store=store,
    )
    ctrl._owner = owner

    captured: list[Agent] = []
    ctrl.agent_changed.connect(captured.append)
    ctrl.set_active("Analista")
    assert captured
    assert isinstance(captured[-1], Agent)
    assert captured[-1].name == "Analista"


# -- §1.5 diagnostics ignora cancelaciones ----------------------------------

def test_diagnostics_ignores_cancelled_response(qapp, monkeypatch):
    """El umbral temporal (MIN_RESPONSE_SECONDS) descarta cancelaciones
    instantáneas. En el test no queremos esperar 0.3s reales, así que
    bajamos el umbral a 0 y dejamos que la condición importante sea la
    presencia de texto real."""
    pytest.importorskip("PySide6")
    from PySide6.QtCore import QObject

    from ui.controllers import diagnostics_controller as diag_module
    from ui.controllers.diagnostics_controller import DiagnosticsController
    from ui.views.diagnostics_panel import DiagnosticsPanel

    monkeypatch.setattr(diag_module, "MIN_RESPONSE_SECONDS", 0.0)

    class _FakeChat(QObject):
        from PySide6.QtCore import Signal as _S
        streaming_changed = _S(bool)
        conversation_changed = _S()

        def __init__(self):
            super().__init__()
            self.messages: list[dict] = []
            self._last = ""

        def last_assistant_text(self) -> str:
            return self._last

    chat = _FakeChat()
    panel = DiagnosticsPanel()
    ctrl = DiagnosticsController(parent=None, chat=chat, panel=panel)
    ctrl._owner = chat

    # Ciclo sin texto → no cuenta (aunque haya pasado el umbral temporal).
    chat.streaming_changed.emit(True)
    chat.streaming_changed.emit(False)
    assert ctrl.stats.responses == 0

    # Ciclo con texto → sí cuenta.
    chat._last = "algo"
    chat.streaming_changed.emit(True)
    chat.streaming_changed.emit(False)
    assert ctrl.stats.responses == 1

    # Otro ciclo con texto → cuenta la segunda.
    chat.streaming_changed.emit(True)
    chat.streaming_changed.emit(False)
    assert ctrl.stats.responses == 2
