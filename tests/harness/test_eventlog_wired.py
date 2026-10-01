"""P1.4: EventLog persistente conectado al controller."""
from __future__ import annotations

from pathlib import Path

import pytest

pytest.importorskip("PySide6")

from PySide6.QtCore import QObject

from core.harness.durable import EventLog


class _FakeTools:
    def definitions(self):
        return []

    def call(self, *a, **kw):
        return ""

    def requires_confirmation(self, name):
        return False

    def intent_rules(self):
        return {}


def _make_ctrl():
    from ui.controllers.chat_controller import ChatController

    return ChatController(
        parent=QObject(),
        parent_widget=None,
        client=object(),
        tools=_FakeTools(),
        renderer=object(),
    )


def test_build_harness_worker_crea_event_log(tmp_path):
    ctrl = _make_ctrl()
    ctrl.messages = [{"role": "user", "content": "hola"}]
    ctrl._loop_detection_enabled = False

    worker = ctrl._build_harness_worker("m1", None, "sys")
    session = worker._session

    assert session.event_log is not None
    assert isinstance(session.event_log, EventLog)
    # El archivo debe existir tras el constructor.
    db_path = session.event_log.db_path
    assert db_path.exists()
    assert db_path.name == "events.db"


def test_worker_guarda_referencia_al_log(tmp_path):
    ctrl = _make_ctrl()
    ctrl.messages = [{"role": "user", "content": "hola"}]
    ctrl._loop_detection_enabled = False

    worker = ctrl._build_harness_worker("m1", None, "sys")
    assert worker._event_log is worker._session.event_log


def test_cerrar_event_log_es_idempotente(tmp_path):
    ctrl = _make_ctrl()
    ctrl.messages = [{"role": "user", "content": "hola"}]
    ctrl._loop_detection_enabled = False

    worker = ctrl._build_harness_worker("m1", None, "sys")
    log = worker._event_log
    assert log is not None
    log.close()
    # Segunda llamada no debe lanzar.
    log.close()
