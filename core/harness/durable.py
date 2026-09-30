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
import hashlib
import json
import secrets
import sqlite3
import threading
from collections.abc import Iterable, Iterator
from dataclasses import asdict, dataclass, field
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

CREATE TABLE IF NOT EXISTS checkpoints (
    checkpoint_id TEXT PRIMARY KEY,
    run_id        TEXT NOT NULL,
    seq           INTEGER NOT NULL,
    snapshot      TEXT NOT NULL,
    ts            TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_checkpoints_run
    ON checkpoints(run_id, seq DESC);

CREATE TABLE IF NOT EXISTS executed_ops (
    key     TEXT PRIMARY KEY,
    run_id  TEXT NOT NULL,
    op_type TEXT NOT NULL,
    call_id TEXT NOT NULL,
    state   TEXT NOT NULL,
    result  TEXT,
    ts      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_executed_ops_run
    ON executed_ops(run_id, call_id);
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


# ══════════════ S2-b: Checkpoints + Idempotencia ══════════════


@dataclass(frozen=True)
class CheckpointSnapshot:
    """Estado minimo de un run para reanudar.

    Spec §4.2.2. NO guarda el contenido del workspace (vive en
    disco) ni los vectores RAG (viven en su DB). Solo lo necesario
    para retomar el ciclo sin releer el event log entero.
    """

    run_id: str
    last_event_seq: int
    step_index: int
    messages_summary: list[dict]
    tools_executed: list[dict]
    loop_state: dict
    correctives_count: int
    verification_issues: list[dict]
    agent_spec_hash: str
    policy_hash: str


class CheckpointManager:
    """Gestor de checkpoints sobre SQLite.

    Uso:
        cm = CheckpointManager(db_path)
        cid = cm.save(snapshot)
        snap = cm.load(cid)
        snap = cm.get_latest("run_id")
        cm.close()

    Retencion: por run, se mantienen los `keep_last_checkpoints`
    mas recientes. Los demas se borran.
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

    def save(self, snapshot: CheckpointSnapshot) -> str:
        """Persiste el snapshot. Devuelve el checkpoint_id."""
        cid = f"cp_{secrets.token_hex(8)}"
        body = json.dumps(
            asdict(snapshot), ensure_ascii=False, default=str,
        )
        ts = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            cur = self._con.cursor()
            cur.execute(
                "INSERT INTO checkpoints "
                "(checkpoint_id, run_id, seq, snapshot, ts) "
                "VALUES (?, ?, ?, ?, ?)",
                (cid, snapshot.run_id, snapshot.last_event_seq,
                 body, ts),
            )
            self._con.commit()
        return cid

    def load(self, checkpoint_id: str) -> CheckpointSnapshot | None:
        with self._lock:
            cur = self._con.cursor()
            row = cur.execute(
                "SELECT snapshot FROM checkpoints "
                "WHERE checkpoint_id = ?",
                (checkpoint_id,),
            ).fetchone()
        if row is None:
            return None
        return self._from_json(row["snapshot"])

    def get_latest(self, run_id: str) -> CheckpointSnapshot | None:
        with self._lock:
            cur = self._con.cursor()
            row = cur.execute(
                "SELECT snapshot FROM checkpoints "
                "WHERE run_id = ? ORDER BY seq DESC LIMIT 1",
                (run_id,),
            ).fetchone()
        if row is None:
            return None
        return self._from_json(row["snapshot"])

    def list_run(self, run_id: str) -> list[str]:
        with self._lock:
            cur = self._con.cursor()
            rows = cur.execute(
                "SELECT checkpoint_id FROM checkpoints "
                "WHERE run_id = ? ORDER BY seq DESC",
                (run_id,),
            ).fetchall()
        return [r["checkpoint_id"] for r in rows]

    def count(self, run_id: str) -> int:
        with self._lock:
            cur = self._con.cursor()
            row = cur.execute(
                "SELECT COUNT(*) AS n FROM checkpoints WHERE run_id = ?",
                (run_id,),
            ).fetchone()
        return int(row["n"]) if row else 0

    def delete(self, checkpoint_id: str) -> bool:
        with self._lock:
            cur = self._con.cursor()
            cur.execute(
                "DELETE FROM checkpoints WHERE checkpoint_id = ?",
                (checkpoint_id,),
            )
            self._con.commit()
            return cur.rowcount > 0

    def apply_retention(self, policy: DurablePolicy) -> int:
        """Borra checkpoints antiguos por run, dejando los N ultimos.

        Devuelve cuantos checkpoints se borraron.
        """
        keep = max(1, policy.keep_last_checkpoints)
        with self._lock:
            cur = self._con.cursor()
            runs = [
                r["run_id"] for r in cur.execute(
                    "SELECT DISTINCT run_id FROM checkpoints",
                ).fetchall()
            ]
            deleted = 0
            for rid in runs:
                ids = [
                    r["checkpoint_id"] for r in cur.execute(
                        "SELECT checkpoint_id FROM checkpoints "
                        "WHERE run_id = ? ORDER BY seq DESC",
                        (rid,),
                    ).fetchall()
                ]
                to_delete = ids[keep:]
                if not to_delete:
                    continue
                placeholders = ",".join("?" * len(to_delete))
                cur.execute(
                    f"DELETE FROM checkpoints "
                    f"WHERE checkpoint_id IN ({placeholders})",
                    to_delete,
                )
                deleted += len(to_delete)
            self._con.commit()
            return deleted

    @staticmethod
    def _from_json(raw: str) -> CheckpointSnapshot:
        data = json.loads(raw)
        return CheckpointSnapshot(
            run_id=data["run_id"],
            last_event_seq=int(data["last_event_seq"]),
            step_index=int(data["step_index"]),
            messages_summary=list(data.get("messages_summary", [])),
            tools_executed=list(data.get("tools_executed", [])),
            loop_state=dict(data.get("loop_state", {})),
            correctives_count=int(data.get("correctives_count", 0)),
            verification_issues=list(
                data.get("verification_issues", []),
            ),
            agent_spec_hash=str(data.get("agent_spec_hash", "")),
            policy_hash=str(data.get("policy_hash", "")),
        )

    def close(self) -> None:
        with self._lock, contextlib.suppress(sqlite3.Error):
            self._con.close()

    def __enter__(self) -> CheckpointManager:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ── Idempotencia ─────────────────────────────────────────────────


def idempotency_key(
    run_id: str, step_index: int, call_id: str,
) -> str:
    """Clave estable para una operacion con side effect.

    Spec §4.2.3. Mismo (run, step, call) -> misma clave. Si el
    proceso muere y se reanuda, la operacion no se repite.
    """
    raw = f"{run_id}|{step_index}|{call_id}".encode()
    return hashlib.sha256(raw).hexdigest()[:24]


class IdempotencyRegistry:
    """Registro de operaciones ejecutadas.

    Estados:
      - pending:   se intento ejecutar, aun no hay resultado.
      - completed: ejecutada con exito; `result` guarda el output.
      - failed:    fallo; reintentar es seguro (no hubo side effect
                   persistente).

    La regla clave: SIEMPRE insertar como `pending` ANTES de
    ejecutar. Asi un crash a mitad deja rastro (`pending`) que
    resume() puede tratar con cuidado.
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

    def mark_pending(
        self, key: str, run_id: str, op_type: str, call_id: str,
    ) -> None:
        ts = datetime.now(UTC).isoformat(timespec="seconds")
        with self._lock:
            cur = self._con.cursor()
            cur.execute(
                "INSERT OR REPLACE INTO executed_ops "
                "(key, run_id, op_type, call_id, state, result, ts) "
                "VALUES (?, ?, ?, ?, 'pending', NULL, ?)",
                (key, run_id, op_type, call_id, ts),
            )
            self._con.commit()

    def mark_completed(self, key: str, result: object) -> None:
        body = json.dumps(result, ensure_ascii=False, default=str)
        with self._lock:
            cur = self._con.cursor()
            cur.execute(
                "UPDATE executed_ops SET state = 'completed', "
                "result = ? WHERE key = ?",
                (body, key),
            )
            self._con.commit()

    def mark_failed(self, key: str) -> None:
        with self._lock:
            cur = self._con.cursor()
            cur.execute(
                "UPDATE executed_ops SET state = 'failed' "
                "WHERE key = ?",
                (key,),
            )
            self._con.commit()

    def get_state(self, key: str) -> str | None:
        with self._lock:
            cur = self._con.cursor()
            row = cur.execute(
                "SELECT state FROM executed_ops WHERE key = ?",
                (key,),
            ).fetchone()
        return row["state"] if row else None

    def get_result(self, key: str) -> object | None:
        with self._lock:
            cur = self._con.cursor()
            row = cur.execute(
                "SELECT result FROM executed_ops WHERE key = ?",
                (key,),
            ).fetchone()
        if row is None or row["result"] is None:
            return None
        return json.loads(row["result"])

    def list_pending(self, run_id: str) -> list[str]:
        with self._lock:
            cur = self._con.cursor()
            rows = cur.execute(
                "SELECT key FROM executed_ops "
                "WHERE run_id = ? AND state = 'pending'",
                (run_id,),
            ).fetchall()
        return [r["key"] for r in rows]

    def clear(self, run_id: str) -> int:
        with self._lock:
            cur = self._con.cursor()
            cur.execute(
                "DELETE FROM executed_ops WHERE run_id = ?",
                (run_id,),
            )
            self._con.commit()
            return cur.rowcount

    def close(self) -> None:
        with self._lock, contextlib.suppress(sqlite3.Error):
            self._con.close()

    def __enter__(self) -> IdempotencyRegistry:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()


# ══════════════ S2-c: Resume (fold + plan) ══════════════


@dataclass
class HarnessState:
    """Estado reconstruido de un run desde event log + checkpoint.

    No es frozen: fold_events lo muta en sitio. Para devolverlo
    al exterior conviene copiarlo (dataclasses.replace).
    """

    run_id: str
    started: bool = False
    ended: bool = False
    end_reason: str = ""
    current_step: int = -1
    last_event_seq: int = 0
    messages: list[dict] = field(default_factory=list)
    last_tool_call: dict | None = None
    pending_confirmation: str | None = None
    loop_correctives_count: int = 0
    loop_aborted: bool = False
    last_verification: dict | None = None
    last_error: str | None = None
    last_warning: str | None = None
    # Hashes del ultimo checkpoint (si hubo). Vacio si no.
    agent_spec_hash: str = ""
    policy_hash: str = ""


_MAX_MESSAGES_IN_STATE = 20


def state_from_checkpoint(snap: CheckpointSnapshot) -> HarnessState:
    """Convierte un CheckpointSnapshot en HarnessState inicial."""
    return HarnessState(
        run_id=snap.run_id,
        started=True,
        current_step=snap.step_index,
        last_event_seq=snap.last_event_seq,
        messages=list(snap.messages_summary[-_MAX_MESSAGES_IN_STATE:]),
        loop_correctives_count=snap.correctives_count,
        last_verification=(
            {"issues": list(snap.verification_issues)}
            if snap.verification_issues else None
        ),
        agent_spec_hash=snap.agent_spec_hash,
        policy_hash=snap.policy_hash,
    )


def fold_events(
    events: Iterable[Event],
    initial: HarnessState | None = None,
) -> HarnessState:
    """Reduce eventos a un HarnessState.

    Funcion pura: no lee DB. Testeable con listas sinteticas.

    Uso tipico:
        state = fold_events(log.read(run_id))
        state = fold_events(log.read(run_id, since_seq=X), initial=base)
    """
    state = initial or HarnessState(run_id="")
    for e in events:
        state.last_event_seq = max(state.last_event_seq, e.seq)
        kind = e.kind
        if kind == "run_started":
            state.started = True
            if not state.run_id:
                state.run_id = e.run_id
        elif kind == "run_ended":
            state.ended = True
            state.end_reason = str(getattr(e, "reason", ""))
        elif kind == "step_started":
            state.current_step = int(getattr(e, "step_index", -1))
        elif kind == "message_completed":
            role = str(getattr(e, "role", ""))
            content = str(getattr(e, "content", ""))
            state.messages.append({"role": role, "content": content})
            if len(state.messages) > _MAX_MESSAGES_IN_STATE:
                state.messages = state.messages[
                    -_MAX_MESSAGES_IN_STATE:
                ]
        elif kind == "tool_call_requested":
            state.last_tool_call = {
                "call_id": str(getattr(e, "call_id", "")),
                "tool_name": str(getattr(e, "tool_name", "")),
                "arguments": dict(getattr(e, "arguments", {})),
                "auto_approved": bool(
                    getattr(e, "auto_approved", False)
                ),
            }
        elif kind == "tool_call_completed":
            # Limpia el "en vuelo" solo si coincide el call_id.
            if (
                state.last_tool_call
                and state.last_tool_call.get("call_id")
                == str(getattr(e, "call_id", ""))
            ):
                state.last_tool_call = None
        elif kind == "confirmation_requested":
            state.pending_confirmation = str(
                getattr(e, "call_id", "")
            )
        elif kind == "confirmation_resolved":
            if (
                state.pending_confirmation
                == str(getattr(e, "call_id", ""))
            ):
                state.pending_confirmation = None
        elif kind == "loop_corrective_prompt":
            state.loop_correctives_count += 1
        elif kind == "loop_aborted":
            state.loop_aborted = True
        elif kind == "verification_run":
            state.last_verification = {
                "call_id": str(getattr(e, "call_id", "")),
                "target": str(getattr(e, "target", "")),
                "issues": list(getattr(e, "issues", [])),
            }
        elif kind == "harness_error":
            state.last_error = str(getattr(e, "message", ""))
        elif kind == "harness_warning":
            state.last_warning = str(
                getattr(e, "warning_kind", "")
            )
    return state


# ── Carga de estado (IO) ─────────────────────────────────────────


def load_state(
    log: EventLog,
    cm: CheckpointManager,
    run_id: str,
) -> HarnessState:
    """Reconstruye el estado de un run desde log + ultimo checkpoint.

    Optimizacion: si hay checkpoint, no re-lee todo el log; solo
    los eventos posteriores al `last_event_seq` del checkpoint.
    """
    snap = cm.get_latest(run_id)
    if snap is None:
        return fold_events(log.read(run_id))

    base = state_from_checkpoint(snap)
    rest = log.read(run_id, since_seq=snap.last_event_seq)
    return fold_events(rest, initial=base)


# ── Deteccion de inconsistencias ─────────────────────────────────


@dataclass(frozen=True)
class Inconsistency:
    kind: str       # "policy_changed" | "agent_changed" | ...
    severity: str   # "warning" | "critical"
    message: str


def detect_inconsistencies(
    state: HarnessState,
    *,
    current_agent_spec_hash: str = "",
    current_policy_hash: str = "",
) -> list[Inconsistency]:
    """Compara hashes del checkpoint con la config actual.

    Un cambio de policy o de agente invalida el checkpoint: el
    run continuo con otra config, asi que reanudarlo tal cual
    seria inconsistente. Se emite como critical.
    """
    out: list[Inconsistency] = []
    if (
        state.policy_hash
        and current_policy_hash
        and state.policy_hash != current_policy_hash
    ):
        out.append(Inconsistency(
            kind="policy_changed",
            severity="critical",
            message=(
                "policy_hash cambio entre sesiones "
                f"({state.policy_hash[:8]} != "
                f"{current_policy_hash[:8]})"
            ),
        ))
    if (
        state.agent_spec_hash
        and current_agent_spec_hash
        and state.agent_spec_hash != current_agent_spec_hash
    ):
        out.append(Inconsistency(
            kind="agent_changed",
            severity="critical",
            message=(
                "agent_spec_hash cambio entre sesiones "
                f"({state.agent_spec_hash[:8]} != "
                f"{current_agent_spec_hash[:8]})"
            ),
        ))
    return out


# ── Plan de resume ───────────────────────────────────────────────


@dataclass(frozen=True)
class ResumePlan:
    """Decision de resume para un run.

    action:
      · "nothing"        — run terminado, nada que hacer
      · "start"          — run no empezado, empezar de cero
      · "resolve_pending"— hay ops pending; resolverlas antes
      · "continue"       — continuar desde from_step
      · "abort"          — no se puede reanudar (inconsistencias)

    from_step:
      · Indice del step desde el que continuar (si action="continue").
      · -1 si no aplica.

    pending_ops:
      · Claves de ops en estado pending (si action="resolve_pending").
      · Lista vacia en otros casos.
    """

    action: str
    from_step: int = -1
    pending_ops: list[str] = field(default_factory=list)
    reason: str = ""


def plan_resume(
    state: HarnessState,
    ir: IdempotencyRegistry | None = None,
    *,
    inconsistencies: list[Inconsistency] | None = None,
) -> ResumePlan:
    """Decide que hacer con un run interrumpido.

    Orden de comprobaciones:
      1. Inconsistencias criticas -> abort.
      2. Run terminado            -> nothing.
      3. Run no empezado          -> start.
      4. Ops pending              -> resolve_pending.
      5. Resto                    -> continue desde current_step.
    """
    # 1. Inconsistencias criticas abortan.
    if inconsistencies:
        critical = [
            i for i in inconsistencies if i.severity == "critical"
        ]
        if critical:
            return ResumePlan(
                action="abort",
                reason="; ".join(i.message for i in critical),
            )

    # 2. Ya termino.
    if state.ended:
        return ResumePlan(
            action="nothing",
            reason=f"run terminado ({state.end_reason})",
        )

    # 3. No empezo.
    if not state.started:
        return ResumePlan(
            action="start",
            reason="run sin RunStarted",
        )

    # 4. Ops pending.
    if ir is not None:
        pending = ir.list_pending(state.run_id)
        if pending:
            return ResumePlan(
                action="resolve_pending",
                pending_ops=list(pending),
                reason=f"{len(pending)} op(s) pending",
            )

    # 5. Continuar.
    return ResumePlan(
        action="continue",
        from_step=state.current_step,
        reason=f"continuar desde step {state.current_step}",
    )
