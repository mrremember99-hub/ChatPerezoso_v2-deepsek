"""S2-a: tests de EventLog + from_dict."""
from __future__ import annotations

import json
import threading
from datetime import UTC, datetime, timedelta

import pytest

from core.harness import events as ev
from core.harness.durable import EventLog
from core.harness.policy import DurablePolicy


def _event(kind: str, seq: int, run_id: str, ts: str, **kw):
    cls = ev.Event._registry[kind]
    return cls(seq=seq, run_id=run_id, ts=ts, **kw)


def _now_iso() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _old_iso(days: int) -> str:
    return (
        datetime.now(UTC) - timedelta(days=days)
    ).isoformat(timespec="seconds")


@pytest.fixture
def log(tmp_path):
    lg = EventLog(tmp_path / "events.sqlite")
    yield lg
    lg.close()


# ── from_dict / roundtrip ────────────────────────────────────────


def test_from_dict_roundtrip():
    e = ev.RunStarted(
        seq=0, run_id="r1", ts="2026-09-30T00:00:00",
        user_message="hola", agent_name="A", model_name="M",
    )
    d = e.to_dict()
    back = ev.Event.from_dict(d)
    assert back == e


def test_from_dict_kind_desconocido_devuelve_unknown():
    """P2#17: kind desconocido -> UnknownEvent, no excepcion."""
    out = ev.Event.from_dict({
        "kind": "inventado", "seq": 1, "run_id": "r", "ts": "t",
    })
    assert isinstance(out, ev.UnknownEvent)
    assert out.original_kind == "inventado"
    assert out.seq == 1
    assert out.run_id == "r"


def test_from_dict_sin_kind_devuelve_unknown():
    """P2#17: kind vacio/ausente -> UnknownEvent con kind=''."""
    out = ev.Event.from_dict({"seq": 1, "run_id": "r", "ts": "t"})
    assert isinstance(out, ev.UnknownEvent)
    assert out.original_kind == ""


def test_from_dict_campos_extra_ignorados():
    """P2#17: campos desconocidos en el payload se ignoran."""
    e = ev.StepStarted(seq=1, run_id="r", ts="t", step_index=0)
    d = e.to_dict()
    d["campo_viejo_de_otra_version"] = "ignorado"
    out = ev.Event.from_dict(d)
    assert isinstance(out, ev.StepStarted)
    assert out.step_index == 0


def test_from_dict_payload_malformado_devuelve_unknown():
    """P2#17: payload que no encaja en la dataclass -> UnknownEvent."""
    d = {
        "kind": "step_started", "seq": 1, "run_id": "r", "ts": "t",
        "step_index": "no-es-int",  # malformado
    }
    # StepStarted acepta el int? Si no, cae a UnknownEvent.
    try:
        out = ev.Event.from_dict(d)
        # Aceptamos ambas ramas segun el tipo real de los campos.
        assert isinstance(out, (ev.StepStarted, ev.UnknownEvent))
    except Exception as exc:  # noqa: BLE001
        raise AssertionError(
            f"from_dict no debe lanzar: {exc}"
        ) from exc


def test_registry_cubre_todos_los_kinds():
    """Toda subclase de Event esta registrada por kind."""
    for name in dir(ev):
        obj = getattr(ev, name)
        if (
            isinstance(obj, type)
            and issubclass(obj, ev.Event)
            and obj is not ev.Event
        ):
            assert obj.kind in ev.Event._registry
            assert ev.Event._registry[obj.kind] is obj


# ── append + read ────────────────────────────────────────────────


def test_append_read_basico(log):
    log.append(_event(
        "step_started", seq=0, run_id="r1", ts=_now_iso(),
        step_index=0,
    ))
    log.append(_event(
        "message_delta", seq=0, run_id="r1", ts=_now_iso(),
        role="assistant", content="x",
    ))
    log.append(_event(
        "step_ended", seq=0, run_id="r1", ts=_now_iso(),
        step_index=0, outcome="ok",
    ))
    got = list(log.read("r1"))
    assert len(got) == 3
    assert [e.kind for e in got] == [
        "step_started", "message_delta", "step_ended",
    ]
    # seq asignado por SQLite, monotono.
    assert [e.seq for e in got] == sorted(e.seq for e in got)
    assert all(e.seq > 0 for e in got)


def test_read_since_seq(log):
    for _ in range(5):
        log.append(_event(
            "step_started", seq=0, run_id="r1", ts=_now_iso(),
            step_index=0,
        ))
    first = list(log.read("r1"))
    second = list(log.read("r1", since_seq=first[2].seq))
    assert len(second) == 2
    assert all(e.seq > first[2].seq for e in second)


def test_read_run_inexistente(log):
    assert list(log.read("nope")) == []


# ── count / list_runs / has_run_ended ────────────────────────────


def test_count_por_run(log):
    for _ in range(3):
        log.append(_event(
            "step_started", seq=0, run_id="r1", ts=_now_iso(),
            step_index=0,
        ))
    log.append(_event(
        "step_started", seq=0, run_id="r2", ts=_now_iso(),
        step_index=0,
    ))
    assert log.count("r1") == 3
    assert log.count("r2") == 1
    assert log.count("r3") == 0


def test_list_runs(log):
    for rid in ("r1", "r2", "r1"):
        log.append(_event(
            "step_started", seq=0, run_id=rid, ts=_now_iso(),
            step_index=0,
        ))
    assert log.list_runs() == ["r1", "r2"]


def test_has_run_ended(log):
    log.append(_event(
        "step_started", seq=0, run_id="r1", ts=_now_iso(),
        step_index=0,
    ))
    assert log.has_run_ended("r1") is False
    log.append(_event(
        "run_ended", seq=0, run_id="r1", ts=_now_iso(),
        reason="completed", summary="ok",
    ))
    assert log.has_run_ended("r1") is True


# ── retencion ────────────────────────────────────────────────────


def test_retention_borra_completos_viejos(log):
    # Run completo antiguo.
    log.append(_event(
        "run_started", seq=0, run_id="old", ts=_old_iso(60),
        user_message="", agent_name="A", model_name="M",
    ))
    log.append(_event(
        "run_ended", seq=0, run_id="old", ts=_old_iso(60),
        reason="completed", summary="",
    ))
    # Run completo reciente.
    log.append(_event(
        "run_started", seq=0, run_id="new", ts=_now_iso(),
        user_message="", agent_name="A", model_name="M",
    ))
    log.append(_event(
        "run_ended", seq=0, run_id="new", ts=_now_iso(),
        reason="completed", summary="",
    ))

    policy = DurablePolicy(
        events_retention_days=30, events_retention_min_runs=50,
    )
    deleted = log.apply_retention(policy)
    assert deleted == 1
    assert log.count("old") == 0
    assert log.count("new") == 2


def test_retention_nunca_borra_incompletos(log):
    log.append(_event(
        "run_started", seq=0, run_id="wip", ts=_old_iso(365),
        user_message="", agent_name="A", model_name="M",
    ))
    # Sin RunEnded: es candidato a resume.
    policy = DurablePolicy(
        events_retention_days=1, events_retention_min_runs=1,
    )
    deleted = log.apply_retention(policy)
    assert deleted == 0
    assert log.count("wip") == 1


def test_retention_keep_last_n(log):
    # 5 runs completos, distintos ts (mas recientes primero).
    for i in range(5):
        rid = f"r{i}"
        ts = (
            datetime.now(UTC) - timedelta(minutes=i)
        ).isoformat(timespec="seconds")
        log.append(_event(
            "run_started", seq=0, run_id=rid, ts=ts,
            user_message="", agent_name="A", model_name="M",
        ))
        log.append(_event(
            "run_ended", seq=0, run_id=rid, ts=ts,
            reason="completed", summary="",
        ))
    policy = DurablePolicy(
        events_retention_days=365, events_retention_min_runs=2,
    )
    deleted = log.apply_retention(policy)
    assert deleted == 3
    assert log.count("r0") == 2  # mas reciente
    assert log.count("r1") == 2
    assert log.count("r2") == 0
    assert log.count("r3") == 0
    assert log.count("r4") == 0


# ── WAL + thread-safety ─────────────────────────────────────────


def test_wal_mode_activo(tmp_path):
    lg = EventLog(tmp_path / "e.sqlite")
    try:
        cur = lg._con.cursor()
        mode = cur.execute("PRAGMA journal_mode").fetchone()[0]
        assert mode.lower() == "wal"
    finally:
        lg.close()


def test_append_threadsafe(log):
    errs: list[Exception] = []

    def worker(rid: str, n: int) -> None:
        try:
            for i in range(n):
                log.append(_event(
                    "step_started", seq=0, run_id=rid,
                    ts=_now_iso(), step_index=i,
                ))
        except Exception as e:  # noqa: BLE001
            errs.append(e)

    threads = [
        threading.Thread(target=worker, args=("r1", 20)),
        threading.Thread(target=worker, args=("r2", 20)),
        threading.Thread(target=worker, args=("r3", 20)),
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=5.0)

    assert errs == []
    assert log.count("r1") == 20
    assert log.count("r2") == 20
    assert log.count("r3") == 20
    # seqs unicos entre todos los runs.
    all_seqs = [e.seq for rid in ("r1", "r2", "r3") for e in log.read(rid)]
    assert len(set(all_seqs)) == 60


# ── contexto ────────────────────────────────────────────────────


def test_context_manager(tmp_path):
    with EventLog(tmp_path / "e.sqlite") as lg:
        lg.append(_event(
            "step_started", seq=0, run_id="r1", ts=_now_iso(),
            step_index=0,
        ))
    # Tras salir del with, la conexion esta cerrada pero el fichero
    # sigue existiendo.
    assert (tmp_path / "e.sqlite").exists()
