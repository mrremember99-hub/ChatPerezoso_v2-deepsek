"""Durable execution: event log + checkpoints.

Spec: docs/harness-v3.md §4.

Estado de slices:
  S2-a (este)  — EventLog SQLite WAL + from_dict.
  S2-b         — CheckpointManager + IdempotencyRegistry.
  S2-c         — resume() helpers.
  S4           — integracion con el harness ciclo.
"""
from __future__ import annotations

import contextlib
import json
import sqlite3
import threading
from collections.abc import Iterator
from datetime import UTC, datetime, timedelta
from pathlib import Path

from core.harness.events import Event
from core.harness.policy import DurablePolicy

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    seq     INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id  TEXT NOT NULL,
    ts      TEXT NOT NULL,
    kind    TEXT NOT NULL,
    payload TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id, seq);
"""


class EventLog:
    """Event log append-only sobre SQLite con WAL.

    Uso:
        log = EventLog(db_path)
        log.append(event)
        for e in log.read(run_id): ...
        log.close()

    Thread-safe via RLock. check_same_thread=False + WAL + lock
    propio (aprendizaje del bug de ast_index: el modulo C de sqlite
    tiene threadsafety=3 pero el wrapper Python no protege su
    estado interno).
    """

    def __init__(self, db_path: Path) -> None:
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.RLock()
        self._con = sqlite3.connect(
            str(self.db_path),
            check_same_thread=False,
        )
        self._con.row_factory = sqlite3.Row
        self._con.execute("PRAGMA journal_mode=WAL;")
        self._con.execute("PRAGMA synchronous=NORMAL;")
        self._con.execute("PRAGMA busy_timeout=5000;")
        with self._lock:
            self._con.executescript(_SCHEMA)
            self._con.commit()

    # -- API ----------------------------------------------------------

    def append(self, event: Event) -> int:
        """Persiste el evento. Devuelve el seq asignado.

        El campo `seq` del evento se descarta: SQLite lo asigna
        via AUTOINCREMENT. Al leer, se rellena con el valor real.
        """
        payload = event.to_dict()
        payload.pop("seq", None)
        payload.pop("kind", None)  # va en columna dedicada
        body = json.dumps(payload, ensure_ascii=False, default=str)
        with self._lock:
            cur = self._con.cursor()
            cur.execute(
                "INSERT INTO events (run_id, ts, kind, payload) "
                "VALUES (?, ?, ?, ?)",
                (event.run_id, event.ts, event.kind, body),
            )
            self._con.commit()
            last = cur.lastrowid
            return int(last) if last is not None else 0

    def read(
        self, run_id: str, since_seq: int = 0,
    ) -> Iterator[Event]:
        """Itera eventos de un run desde seq > since_seq."""
        with self._lock:
            cur = self._con.cursor()
            rows = list(cur.execute(
                "SELECT seq, kind, payload FROM events "
                "WHERE run_id = ? AND seq > ? ORDER BY seq",
                (run_id, int(since_seq)),
            ))
        for row in rows:
            data = json.loads(row["payload"])
            data["seq"] = int(row["seq"])
            data["kind"] = row["kind"]
            yield Event.from_dict(data)

    def count(self, run_id: str) -> int:
        with self._lock:
            cur = self._con.cursor()
            row = cur.execute(
                "SELECT COUNT(*) AS n FROM events WHERE run_id = ?",
                (run_id,),
            ).fetchone()
            return int(row["n"]) if row else 0

    def list_runs(self) -> list[str]:
        with self._lock:
            cur = self._con.cursor()
            rows = cur.execute(
                "SELECT DISTINCT run_id FROM events ORDER BY run_id",
            ).fetchall()
            return [row["run_id"] for row in rows]

    def has_run_ended(self, run_id: str) -> bool:
        with self._lock:
            cur = self._con.cursor()
            row = cur.execute(
                "SELECT 1 FROM events "
                "WHERE run_id = ? AND kind = 'run_ended' LIMIT 1",
                (run_id,),
            ).fetchone()
            return row is not None

    def apply_retention(
        self,
        policy: DurablePolicy,
        *,
        now_iso: str | None = None,
    ) -> int:
        """Borra eventos de runs completos y antiguos.

        Un run se considera completo si tiene un `RunEnded`.
        Nunca borra runs incompletos (son candidatos a resume).

        Reglas:
          - `keep_days`: runs cuyo ultimo evento sea anterior a
            `now - keep_days` se borran.
          - `keep_last_n`: se mantienen los N runs completos mas
            recientes; el resto se borra.

        Devuelve el numero de runs borrados.
        """
        now_iso = now_iso or datetime.now(UTC).isoformat(
            timespec="seconds",
        )
        # Cutoff ISO: comparacion lexicografica funciona porque
        # todos los ts son ISO 8601 con la misma zona.
        now_dt = datetime.fromisoformat(now_iso)
        if now_dt.tzinfo is None:
            now_dt = now_dt.replace(tzinfo=UTC)
        cutoff_iso = (
            now_dt - timedelta(days=policy.events_retention_days)
        ).isoformat(timespec="seconds")

        with self._lock:
            cur = self._con.cursor()
            rows = cur.execute(
                """
                SELECT run_id,
                       MAX(ts) AS last_ts,
                       MAX(CASE WHEN kind = 'run_ended' THEN 1 ELSE 0 END)
                           AS complete
                FROM events
                GROUP BY run_id
                ORDER BY last_ts DESC
                """,
            ).fetchall()

        deleted = 0
        complete_index = 0
        for row in rows:
            run_id = row["run_id"]
            last_ts = row["last_ts"]
            complete = bool(row["complete"])
            if not complete:
                continue
            too_old = last_ts < cutoff_iso
            too_many = complete_index >= policy.events_retention_min_runs
            complete_index += 1
            if too_old or too_many:
                with self._lock:
                    cur = self._con.cursor()
                    cur.execute(
                        "DELETE FROM events WHERE run_id = ?",
                        (run_id,),
                    )
                    self._con.commit()
                deleted += 1
        return deleted

    def close(self) -> None:
        with self._lock, contextlib.suppress(sqlite3.Error):
            self._con.close()

    def __enter__(self) -> EventLog:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# CheckpointManager se implementa en S2-b.
class CheckpointManager:
    """Gestor de checkpoints. Stub de S0. Implementacion en S2-b."""

    def save(self, state: object) -> str:
        raise NotImplementedError("S2-b: implementar save")

    def load(self, checkpoint_id: str) -> object:
        raise NotImplementedError("S2-b: implementar load")
