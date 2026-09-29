#!/usr/bin/env python3
"""Tracker de una sesion de ChatPerezoso para auditoria posterior.

Lanza la app como subproceso, tee a stdout+stderr, y toma snapshots
periodicos de los ficheros de estado para reconstruir la sesion.

Uso:
    python3 scripts/overpaper_tracker.py run
    python3 scripts/overpaper_tracker.py run --entry main.py
    python3 scripts/overpaper_tracker.py run --interval 2.0

Salida (todo bajo ~/.cache/chatperezoso/overpaper_runs/<ts>/):
    app.log                  stdout+stderr crudo
    pre_state.json           git, config, agents, workspace inicial
    post_state.json          idem al terminar
    snapshots/history_N.json copia verbatim de history.json al cambiar
    workspace_timeline.jsonl una linea por creacion/modificacion/borrado
    analysis.md              informe final

Ctrl+C en el tracker mata la app con SIGTERM (5s de gracia) y cierra.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import signal
import subprocess
import sys
import threading
import time
from datetime import datetime
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
RUNS_DIR = Path.home() / ".cache" / "chatperezoso" / "overpaper_runs"

# Ficheros de estado a snapshotear. Los que no existan se ignoran.
TRACKED_FILES = ("history.json", "config.json", "agents.json")

# Extensiones a incluir en el timeline del workspace.
WORKSPACE_EXTS = {".py", ".md", ".txt", ".json", ".png", ".jpg", ".jpeg", ".tif", ".tiff"}


def _sha256_file(path: Path) -> str:
    h = hashlib.sha256()
    try:
        with path.open("rb") as f:
            for chunk in iter(lambda: f.read(65536), b""):
                h.update(chunk)
    except OSError:
        return ""
    return h.hexdigest()


def _load_workspace_root() -> Path:
    config = ROOT / "config.json"
    if config.exists():
        try:
            data = json.loads(config.read_text(encoding="utf-8"))
            ws = data.get("workspace")
            if isinstance(ws, str) and ws.strip():
                return Path(ws).expanduser().resolve()
        except (OSError, ValueError):
            pass
    return ROOT / "workspace"


def _list_workspace(root: Path) -> dict[str, dict]:
    """Mapea path relativo -> {size, mtime, sha256}."""
    out: dict[str, dict] = {}
    if not root.is_dir():
        return out
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if p.suffix.lower() not in WORKSPACE_EXTS:
            continue
        try:
            st = p.stat()
        except OSError:
            continue
        rel = str(p.relative_to(root))
        out[rel] = {
            "size": st.st_size,
            "mtime": st.st_mtime,
            "sha256": _sha256_file(p),
        }
    return out


def _git_snapshot() -> dict:
    def run(args: list[str]) -> str:
        try:
            proc = subprocess.run(
                args, cwd=ROOT, capture_output=True, text=True, timeout=5,
            )
            return proc.stdout
        except (OSError, subprocess.TimeoutExpired):
            return ""
    return {
        "head": run(["git", "rev-parse", "HEAD"]).strip(),
        "status": run(["git", "status", "--short"]),
        "branch": run(["git", "rev-parse", "--abbrev-ref", "HEAD"]).strip(),
    }


def _file_or_null(path: Path) -> dict | None:
    if not path.exists():
        return None
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {"_raw": path.read_text(encoding="utf-8", errors="replace")}


def _state_snapshot() -> dict:
    return {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "git": _git_snapshot(),
        "config": _file_or_null(ROOT / "config.json"),
        "agents": _file_or_null(ROOT / "agents.json"),
        "workspace_root": str(_load_workspace_root()),
        "workspace_files": _list_workspace(_load_workspace_root()),
    }


class Tracker:
    def __init__(self, run_dir: Path, interval: float) -> None:
        self.run_dir = run_dir
        self.interval = interval
        self.snapshots_dir = run_dir / "snapshots"
        self.snapshots_dir.mkdir(parents=True, exist_ok=True)
        self.timeline_path = run_dir / "workspace_timeline.jsonl"
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None
        self._last_hashes: dict[str, str] = {}
        self._last_workspace: dict[str, dict] = {}
        self._history_counter = 0

    # -- polling ----------------------------------------------------------
    def start(self) -> None:
        self._thread = threading.Thread(target=self._loop, daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)

    def _loop(self) -> None:
        while not self._stop.is_set():
            try:
                self._tick()
            except Exception as exc:  # noqa: BLE001
                # Nunca abortar el tracker por un fallo de polling.
                self._append_timeline({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "kind": "tracker_error",
                    "error": repr(exc),
                })
            self._stop.wait(self.interval)

    def _tick(self) -> None:
        self._snapshot_tracked_files()
        self._snapshot_workspace()

    def _snapshot_tracked_files(self) -> None:
        for name in TRACKED_FILES:
            path = ROOT / name
            if not path.exists():
                continue
            h = _sha256_file(path)
            if h == self._last_hashes.get(name):
                continue
            self._last_hashes[name] = h
            if name == "history.json":
                dest = self.snapshots_dir / f"history_{self._history_counter:03d}.json"
                self._history_counter += 1
                try:
                    dest.write_bytes(path.read_bytes())
                except OSError:
                    continue
                self._append_timeline({
                    "ts": datetime.now().isoformat(timespec="seconds"),
                    "kind": "history_snapshot",
                    "file": str(dest.relative_to(self.run_dir)),
                })

    def _snapshot_workspace(self) -> None:
        current = _list_workspace(_load_workspace_root())
        ts = datetime.now().isoformat(timespec="seconds")
        for rel, meta in current.items():
            prev = self._last_workspace.get(rel)
            if prev is None:
                self._append_timeline({"ts": ts, "kind": "created", "path": rel, **meta})
            elif prev["sha256"] != meta["sha256"]:
                self._append_timeline({
                    "ts": ts, "kind": "modified", "path": rel,
                    "old_size": prev["size"], "new_size": meta["size"],
                    "sha256": meta["sha256"],
                })
        for rel in self._last_workspace:
            if rel not in current:
                self._append_timeline({"ts": ts, "kind": "deleted", "path": rel})
        self._last_workspace = current

    def _append_timeline(self, entry: dict) -> None:
        with self.timeline_path.open("a", encoding="utf-8") as f:
            f.write(json.dumps(entry, ensure_ascii=False) + "\n")


def _tee(stream_in, stream_out, log_file) -> None:
    """Copia lineas de stream_in a stream_out y al log_file."""
    for line in iter(stream_in.readline, ""):
        stream_out.write(line)
        stream_out.flush()
        log_file.write(line)
        log_file.flush()
    stream_in.close()


def _analyze(run_dir: Path, exit_code: int, duration_s: float) -> None:
    timeline = run_dir / "workspace_timeline.jsonl"
    counts = {"created": 0, "modified": 0, "deleted": 0, "history_snapshot": 0}
    first_ts = None
    last_ts = None
    if timeline.exists():
        for raw in timeline.read_text(encoding="utf-8").splitlines():
            if not raw.strip():
                continue
            try:
                e = json.loads(raw)
            except ValueError:
                continue
            k = e.get("kind")
            if k in counts:
                counts[k] += 1
            ts = e.get("ts")
            if ts:
                first_ts = first_ts or ts
                last_ts = ts
    log = run_dir / "app.log"
    errors: list[str] = []
    if log.exists():
        for i, line in enumerate(log.read_text(encoding="utf-8", errors="replace").splitlines(), 1):
            s = line.strip()
            if s.startswith("Traceback") or "Error:" in s or "ERROR " in s:
                errors.append(f"L{i}: {s[:200]}")
    lines = [
        f"# Analisis de corrida OVERPAPER",
        "",
        f"- Carpeta: `{run_dir}`",
        f"- Exit code app: {exit_code}",
        f"- Duracion: {duration_s:.1f} s",
        f"- Rango timeline: {first_ts} .. {last_ts}",
        "",
        "## Cambios de workspace",
        f"- Creados: {counts['created']}",
        f"- Modificados: {counts['modified']}",
        f"- Borrados: {counts['deleted']}",
        "",
        "## Snapshots de history.json",
        f"- Total: {counts['history_snapshot']}",
        "",
        f"## Errores en log ({len(errors)})",
    ]
    lines.extend(f"- `{e}`" for e in errors[:50])
    (run_dir / "analysis.md").write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_run(args: argparse.Namespace) -> int:
    entry = Path(args.entry)
    if not (ROOT / entry).exists():
        print(f"ERROR: entry no existe: {entry}", file=sys.stderr)
        return 2
    ts = datetime.now().strftime("%Y%m%d-%H%M%S")
    run_dir = RUNS_DIR / ts
    run_dir.mkdir(parents=True, exist_ok=True)
    print(f"[tracker] run_dir = {run_dir}")

    (run_dir / "pre_state.json").write_text(
        json.dumps(_state_snapshot(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )

    tracker = Tracker(run_dir, args.interval)
    tracker.start()
    # Primer tick inmediato para capturar el workspace inicial.
    tracker._tick()

    log_path = run_dir / "app.log"
    start = time.monotonic()
    with log_path.open("w", encoding="utf-8") as logf:
        proc = subprocess.Popen(
            [sys.executable, str(entry)],
            cwd=ROOT,
            stdout=subprocess.PIPE,
            stderr=subprocess.STDOUT,
            text=True,
            bufsize=1,
        )
        t_out = threading.Thread(
            target=_tee, args=(proc.stdout, sys.stdout, logf), daemon=True,
        )
        t_out.start()

        try:
            exit_code = proc.wait()
        except KeyboardInterrupt:
            print("\n[tracker] Ctrl+C: enviando SIGTERM a la app...")
            proc.send_signal(signal.SIGTERM)
            try:
                exit_code = proc.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                print("[tracker] sin respuesta: SIGKILL")
                proc.kill()
                exit_code = proc.wait()
        finally:
            t_out.join(timeout=2.0)
            tracker.stop()
            duration = time.monotonic() - start

    (run_dir / "post_state.json").write_text(
        json.dumps(_state_snapshot(), ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    _analyze(run_dir, exit_code, duration)
    print(f"\n[tracker] analisis: {run_dir / 'analysis.md'}")
    return exit_code


def cmd_report(args: argparse.Namespace) -> int:
    run_dir = Path(args.run_dir).expanduser().resolve()
    if not run_dir.is_dir():
        print(f"ERROR: no existe: {run_dir}", file=sys.stderr)
        return 2
    _analyze(run_dir, -1, 0.0)
    print(f"[tracker] analisis regenerado: {run_dir / 'analysis.md'}")
    return 0


def main() -> int:
    p = argparse.ArgumentParser(description=__doc__)
    sub = p.add_subparsers(dest="cmd", required=True)

    run_p = sub.add_parser("run", help="Lanza la app y captura la sesion")
    run_p.add_argument("--entry", default="bootstrap.py")
    run_p.add_argument("--interval", type=float, default=5.0)
    run_p.set_defaults(func=cmd_run)

    rep_p = sub.add_parser("report", help="Regenera analysis.md de una corrida")
    rep_p.add_argument("run_dir")
    rep_p.set_defaults(func=cmd_report)

    args = p.parse_args()
    return args.func(args)


if __name__ == "__main__":
    raise SystemExit(main())
