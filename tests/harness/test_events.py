"""S0: verificacion de la estructura de eventos."""
from __future__ import annotations

import dataclasses

import pytest

from core.harness import events as ev


def test_event_base_es_frozen():
    """Los eventos son inmutables."""
    e = ev.RunStarted(
        seq=1, run_id="r1", ts="2026-09-30T00:00:00",
        user_message="hola", agent_name="Programador",
        model_name="qwen3:30b-a3b",
    )
    assert e.kind == "run_started"
    with pytest.raises(dataclasses.FrozenInstanceError):
        e.seq = 2  # type: ignore[misc]


def test_run_started_to_dict():
    e = ev.RunStarted(
        seq=1, run_id="r1", ts="2026-09-30T00:00:00",
        user_message="hola", agent_name="Programador",
        model_name="qwen3:30b-a3b",
    )
    d = e.to_dict()
    assert d["kind"] == "run_started"
    assert d["user_message"] == "hola"
    assert d["agent_name"] == "Programador"
    assert d["seq"] == 1
    assert d["run_id"] == "r1"


def test_kinds_unicos():
    """Cada subclase de Event declara un kind unico."""
    kinds: list[str] = []
    for name in dir(ev):
        obj = getattr(ev, name)
        if (
            isinstance(obj, type)
            and issubclass(obj, ev.Event)
            and obj is not ev.Event
        ):
            kinds.append(obj.kind)
    assert len(kinds) == len(set(kinds)), f"kinds duplicados: {kinds}"


def test_tool_call_requested_arguments_es_dict():
    e = ev.ToolCallRequested(
        seq=5, run_id="r1", ts="2026-09-30T00:00:00",
        call_id="c1", tool_name="escribir_archivo",
        arguments={"path": "gui.py"}, auto_approved=True,
    )
    assert e.arguments["path"] == "gui.py"
    assert e.auto_approved is True


def test_harness_warning_usa_warning_kind():
    """HarnessWarning no colisiona con el ClassVar kind."""
    e = ev.HarnessWarning(
        seq=10, run_id="r1", ts="2026-09-30T00:00:00",
        warning_kind="resume_with_changes", details=[],
    )
    assert e.kind == "harness_warning"
    assert e.warning_kind == "resume_with_changes"


def test_todos_los_eventos_llevan_seq_run_id_ts():
    """Contrato: cualquier evento tiene los 3 fields base."""
    for name in dir(ev):
        obj = getattr(ev, name)
        if (
            isinstance(obj, type)
            and issubclass(obj, ev.Event)
            and obj is not ev.Event
        ):
            field_names = {f.name for f in dataclasses.fields(obj)}
            assert {"seq", "run_id", "ts"} <= field_names, name
